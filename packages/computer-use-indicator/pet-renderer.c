/* SPDX-License-Identifier: MIT
 * Presentation only. The broker owns capture permission, task lifetime and the
 * notification queue. A painted phase is an acknowledgement of GTK drawing,
 * never a claim about compositor presentation or permission enforcement.
 *
 * Sprite ABI: four columns, eight rows (idle, working, reading, lookingAway,
 * waiting, attention, success, error). Native privacy poses deliberately
 * replace untrusted sprite pixels: an arbitrary sheet has no safe face bounds.
 */
#define _POSIX_C_SOURCE 200809L
#include <gtk/gtk.h>
#include <gtk-layer-shell.h>
#include <glib-unix.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include "monitor-match.h"

#define LEASE_US (4 * G_USEC_PER_SEC)
#define MAX_LINE (16384u - 1u) /* Include the terminating newline in the 16 KiB cap. */
#define MAX_ASSET_BYTES (16u * 1024u * 1024u)
#define MAX_SUMMARY_BYTES 2048
#define MAX_SUMMARY_CHARS 240
#define PET_SIZE 144
#define BODY_HEIGHT 160
#define FRAME_INTERVAL_MS 167
#define PI 3.14159265358979323846

typedef enum {
    POSE_IDLE, POSE_WORKING, POSE_READING, POSE_PRIVATE,
    POSE_WAITING, POSE_ATTENTION, POSE_SUCCESS, POSE_UNKNOWN
} PetPose;
typedef enum { PHASE_UNKNOWN, PHASE_ACTIVE, PHASE_OBSERVING,
               PHASE_WAITING, PHASE_QUEUED } Phase;
typedef enum { OBS_UNKNOWN, OBS_ENABLED, OBS_DISABLED } Observation;
typedef enum { BUBBLE_NONE, BUBBLE_APPROVAL, BUBBLE_QUESTION, BUBBLE_RESULT } BubbleKind;

static GtkWidget *body, *drawing, *bubble, *summary_label, *kind_label;
static GtkWidget *count_label, *stop_label, *open_button, *dismiss_button;
static GtkCssProvider *css;
static GdkMonitor *target;
static GdkRectangle expected, initial_geometry;
static GdkPixbuf *sheet;
static char *sheet_path;
static struct stat sheet_info;
static guint64 asset_serial;
static int frame_width, frame_height;
static char *output_name, *task_id, *activity;
static char *bubble_id, *bubble_summary;
static BubbleKind bubble_kind;
static Phase phase = PHASE_UNKNOWN;
static Observation observation = OBS_UNKNOWN;
static PetPose pose = POSE_UNKNOWN;
static gboolean observation_active, begun, awaiting_begin, stopped, cu_seen;
static gboolean visible = TRUE, reduced_motion, target_valid;
static gboolean ready_sent, action_sent, output_failed;
static gboolean phase_ack_pending, drew_current;
static Phase ack_phase;
static guint64 ack_transition, render_serial, drawn_serial;
static PetPose drawn_pose;
static Observation drawn_observation;
static gboolean drawn_observation_active;
static unsigned working_count, queue_count, frame, animation_step, animation_length;
static guint animation_source;
static gint64 last_heartbeat;
static char pending[MAX_LINE + 1];
static size_t pending_len;

static void finish(const char *reason);
static void refresh_presentation(gboolean animate);
static void sync_visibility(void);
static void hide_for_monitor(void);

static gboolean monitor_unchanged(void) {
    if (!target_valid || !target) return FALSE;
    GdkRectangle current;
    gdk_monitor_get_geometry(target, &current);
    return current.x == initial_geometry.x && current.y == initial_geometry.y &&
        current.width == initial_geometry.width && current.height == initial_geometry.height;
}

static gboolean identifier(const char *value) {
    if (!value || !*value || strlen(value) > 128) return FALSE;
    for (const unsigned char *p = (const unsigned char *)value; *p; ++p)
        if (!g_ascii_isalnum(*p) && *p != '_' && *p != '-' && *p != '.') return FALSE;
    return TRUE;
}

static gboolean unsigned_number(const char *text, guint64 maximum, guint64 *result) {
    guint64 value = 0;
    if (!text || !*text) return FALSE;
    for (const unsigned char *p = (const unsigned char *)text; *p; ++p) {
        if (*p < '0' || *p > '9') return FALSE;
        unsigned digit = *p - '0';
        if (value > maximum / 10 || (value == maximum / 10 && digit > maximum % 10))
            return FALSE;
        value = value * 10 + digit;
    }
    *result = value;
    return TRUE;
}

static gboolean coordinate(const char *text, int *result) {
    gboolean negative = text && text[0] == '-';
    guint64 magnitude;
    if (!unsigned_number(text ? text + (negative ? 1 : 0) : NULL, 100000, &magnitude))
        return FALSE;
    *result = negative ? -(int)magnitude : (int)magnitude;
    return TRUE;
}

static gboolean geometry(char **values, GdkRectangle *rect) {
    return coordinate(values[0], &rect->x) && coordinate(values[1], &rect->y) &&
        coordinate(values[2], &rect->width) && coordinate(values[3], &rect->height) &&
        rect->width >= 320 && rect->height >= 200;
}

static gboolean safe_text(const char *text, size_t max_bytes, glong max_chars) {
    if (!text || !*text || strlen(text) > max_bytes || !g_utf8_validate(text, -1, NULL) ||
        g_utf8_strlen(text, -1) > max_chars) return FALSE;
    gboolean nonspace = FALSE;
    for (const char *p = text; *p; p = g_utf8_next_char(p)) {
        gunichar ch = g_utf8_get_char(p);
        if (g_unichar_iscntrl(ch) || ch == 0x061c || ch == 0x200e || ch == 0x200f || (ch >= 0x202a && ch <= 0x202e) ||
            (ch >= 0x2066 && ch <= 0x2069)) return FALSE;
        if (!g_unichar_isspace(ch)) nonspace = TRUE;
    }
    return nonspace;
}

/* GLib's decoder is permissive; enforce canonical RFC 4648 text before using
 * it for a path or notification. Embedded NULs never reach a C string API. */
static char *decode_text(const char *encoded, size_t maximum) {
    size_t length = strlen(encoded);
    if (!length || length % 4 || length > ((maximum + 2) / 3) * 4) return NULL;
    size_t padding = 0;
    for (size_t i = 0; i < length; ++i) {
        unsigned char ch = (unsigned char)encoded[i];
        if (ch == '=') {
            if (++padding > 2 || i < length - 2) return NULL;
        } else if (padding || !(g_ascii_isalnum(ch) || ch == '+' || ch == '/')) return NULL;
    }
    gsize decoded_length = 0;
    guchar *bytes = g_base64_decode(encoded, &decoded_length);
    char *canonical = g_base64_encode(bytes, decoded_length);
    gboolean valid = decoded_length > 0 && decoded_length <= maximum &&
        !memchr(bytes, '\0', decoded_length) && !strcmp(canonical, encoded) &&
        g_utf8_validate((const char *)bytes, (gssize)decoded_length, NULL);
    char *result = valid ? g_strndup((const char *)bytes, decoded_length) : NULL;
    g_free(canonical);
    g_free(bytes);
    return result;
}

static char *json_string(const char *text) {
    GString *result = g_string_new("\"");
    for (const unsigned char *p = (const unsigned char *)text; *p; ++p) {
        if (*p == '"' || *p == '\\') { g_string_append_c(result, '\\'); g_string_append_c(result, *p); }
        else if (*p < 0x20) g_string_append_printf(result, "\\u%04x", (unsigned)*p);
        else g_string_append_c(result, *p);
    }
    g_string_append_c(result, '"');
    return g_string_free(result, FALSE);
}

static void output_line(const char *line) {
    if (output_failed) return;
    if (fputs(line, stdout) == EOF || fputc('\n', stdout) == EOF || fflush(stdout) == EOF) {
        output_failed = TRUE;
        finish("event_pipe_closed");
    }
}

static const char *phase_name(Phase value) {
    switch (value) {
        case PHASE_ACTIVE: return "active";
        case PHASE_OBSERVING: return "observing";
        case PHASE_WAITING: return "waiting";
        case PHASE_QUEUED: return "queued";
        default: return "unknown";
    }
}

static gboolean parse_phase(const char *name, Phase *value) {
    for (int i = PHASE_UNKNOWN; i <= PHASE_QUEUED; ++i)
        if (!strcmp(name, phase_name((Phase)i))) { *value = (Phase)i; return TRUE; }
    return FALSE;
}

static void emit_ready(void) {
    if (ready_sent || stopped) return;
    char *task = json_string(task_id), *output = json_string(output_name);
    char *line = g_strdup_printf(
        "{\"revision\":1,\"event\":\"ready\",\"task_id\":%s,\"output\":%s,"
        "\"surfaces\":2,\"keyboard\":\"none\",\"input_region\":\"empty\","
        "\"bubble_input_region\":\"interactive\",\"exclusive_zone\":-1,"
        "\"geometry\":[%d,%d,%d,%d]}", task, output,
        expected.x, expected.y, expected.width, expected.height);
    ready_sent = TRUE;
    output_line(line);
    g_free(line); g_free(output); g_free(task);
}

static void stop_animation(void) {
    if (animation_source) { g_source_remove(animation_source); animation_source = 0; }
}

static void finish(const char *reason) {
    if (stopped) return;
    stopped = TRUE;
    phase_ack_pending = FALSE;
    stop_animation();
    if (body) gtk_widget_hide(body);
    if (bubble) gtk_widget_hide(bubble);
    char *escaped = json_string(reason);
    char *line = g_strdup_printf("{\"revision\":1,\"event\":\"ended\",\"reason\":%s}", escaped);
    output_line(line);
    g_free(line); g_free(escaped);
    if (gtk_main_level()) gtk_main_quit();
}

static PetPose current_pose(void) {
    if (observation == OBS_DISABLED) return POSE_PRIVATE;
    if (observation == OBS_UNKNOWN) return POSE_UNKNOWN;
    if (observation_active || phase == PHASE_OBSERVING) return POSE_READING;
    if (queue_count || bubble_kind != BUBBLE_NONE || phase == PHASE_QUEUED) return POSE_ATTENTION;
    if (phase == PHASE_ACTIVE || working_count) return POSE_WORKING;
    if (phase == PHASE_WAITING) return POSE_WAITING;
    return POSE_IDLE;
}

static gboolean can_present(void) {
    return begun && visible && target_valid && !stopped;
}

static gboolean cu_in_progress(void) {
    return !awaiting_begin && (phase != PHASE_UNKNOWN || observation_active);
}

static void accessible_state(void) {
    const char *privacy = observation == OBS_DISABLED ? "Bildschirmzugriff ausgeschaltet. Pfoten vor den Augen." :
        observation == OBS_UNKNOWN ? "Bildschirmzugriff unbestätigt. Augen geschlossen." :
        (observation_active || phase == PHASE_OBSERVING) ? "Liest den Bildschirm." :
        "Bildschirmzugriff erlaubt, liest gerade nicht.";
    const char *status = pose == POSE_WORKING ? "Arbeitet." : pose == POSE_WAITING ? "Wartet." :
        pose == POSE_ATTENTION ? "Nachricht wartet." : "";
    char *description = g_strdup_printf("%s %s %u Aufgaben, %u Nachrichten. %s",
        privacy, status, working_count, queue_count, activity ? activity : "");
    AtkObject *object = gtk_widget_get_accessible(drawing);
    atk_object_set_name(object, "Wiesel");
    atk_object_set_role(object, ATK_ROLE_IMAGE);
    atk_object_set_description(object, description);
    g_free(description);
}

static void ellipse(cairo_t *cr, double x, double y, double rx, double ry) {
    cairo_save(cr);
    cairo_translate(cr, x, y); cairo_scale(cr, rx, ry);
    cairo_arc(cr, 0, 0, 1, 0, 2 * PI);
    cairo_restore(cr);
}

static void fill_ellipse(cairo_t *cr, double x, double y, double rx, double ry,
                         double r, double g, double b) {
    ellipse(cr, x, y, rx, ry); cairo_set_source_rgb(cr, r, g, b); cairo_fill(cr);
}

static void round_rect(cairo_t *cr, double x, double y, double w, double h, double radius) {
    cairo_new_sub_path(cr);
    cairo_arc(cr, x + w - radius, y + radius, radius, -PI / 2, 0);
    cairo_arc(cr, x + w - radius, y + h - radius, radius, 0, PI / 2);
    cairo_arc(cr, x + radius, y + h - radius, radius, PI / 2, PI);
    cairo_arc(cr, x + radius, y + radius, radius, PI, 3 * PI / 2);
    cairo_close_path(cr);
}

static void small_text(cairo_t *cr, const char *text, double x, double y, double size) {
    PangoLayout *layout = pango_cairo_create_layout(cr);
    PangoFontDescription *font = pango_font_description_from_string("Sans Bold");
    pango_font_description_set_absolute_size(font, size * PANGO_SCALE);
    pango_layout_set_font_description(layout, font);
    pango_layout_set_text(layout, text, -1);
    int width, height;
    pango_layout_get_pixel_size(layout, &width, &height);
    cairo_move_to(cr, x - width / 2.0, y - height / 2.0);
    pango_cairo_show_layout(cr, layout);
    pango_font_description_free(font); g_object_unref(layout);
}

static void locked_eye(cairo_t *cr) {
    fill_ellipse(cr, 118, 115, 15, 15, 0.08, 0.15, 0.19);
    cairo_set_source_rgb(cr, 0.83, 0.94, 0.90); cairo_set_line_width(cr, 1.6);
    cairo_move_to(cr, 108, 115); cairo_curve_to(cr, 114, 107, 122, 107, 128, 115);
    cairo_curve_to(cr, 122, 121, 114, 121, 108, 115); cairo_stroke(cr);
    cairo_move_to(cr, 110, 123); cairo_line_to(cr, 126, 107); cairo_stroke(cr);
}

/* The custom character has a long tail, cream muzzle, rust coat and teal
 * neckerchief. All poses are procedural, independent of any system emoji.
 * Privacy frame zero already has an opaque eye mask AND covering paws. */
static void native_pet(cairo_t *cr, PetPose state, unsigned animation_frame) {
    static const double bob[] = { 0, -1.4, -0.4, 0 };
    double offset = state == POSE_WORKING || state == POSE_READING || state == POSE_ATTENTION ?
        bob[animation_frame] : 0;
    cairo_save(cr);
    cairo_set_source_rgba(cr, 0.02, 0.05, 0.08, 0.18);
    ellipse(cr, 78, 135, 42, 4); cairo_fill(cr);
    cairo_translate(cr, 0, offset);
    cairo_set_line_cap(cr, CAIRO_LINE_CAP_ROUND);
    cairo_set_source_rgb(cr, 0.29, 0.13, 0.10); cairo_set_line_width(cr, 21);
    cairo_move_to(cr, 97, 115); cairo_curve_to(cr, 140, 129, 138, 79, 126, 69); cairo_stroke(cr);
    cairo_set_source_rgb(cr, 0.68, 0.32, 0.17); cairo_set_line_width(cr, 15);
    cairo_move_to(cr, 97, 113); cairo_curve_to(cr, 135, 125, 135, 83, 126, 69); cairo_stroke(cr);
    fill_ellipse(cr, 77, 98, 29, 36, 0.60, 0.26, 0.14);
    fill_ellipse(cr, 75, 102, 18, 27, 0.97, 0.87, 0.68);
    fill_ellipse(cr, 58, 130, 13, 6, 0.32, 0.16, 0.11);
    fill_ellipse(cr, 95, 130, 13, 6, 0.32, 0.16, 0.11);
    fill_ellipse(cr, 44, 27, 12, 16, 0.40, 0.18, 0.12);
    fill_ellipse(cr, 107, 27, 12, 16, 0.40, 0.18, 0.12);
    fill_ellipse(cr, 44, 27, 7, 10, 0.84, 0.51, 0.40);
    fill_ellipse(cr, 107, 27, 7, 10, 0.84, 0.51, 0.40);
    fill_ellipse(cr, 76, 53, 39, 34, 0.74, 0.37, 0.19);
    fill_ellipse(cr, 60, 58, 16, 20, 0.98, 0.89, 0.73);
    fill_ellipse(cr, 91, 58, 16, 20, 0.98, 0.89, 0.73);
    fill_ellipse(cr, 76, 69, 25, 17, 0.98, 0.89, 0.73);
    cairo_set_source_rgb(cr, 0.11, 0.16, 0.17); cairo_set_line_width(cr, 2.6);
    if (state == POSE_PRIVATE || state == POSE_UNKNOWN || state == POSE_WAITING) {
        cairo_move_to(cr, 53, 48); cairo_curve_to(cr, 57, 51, 62, 51, 66, 48);
        cairo_move_to(cr, 85, 48); cairo_curve_to(cr, 89, 51, 94, 51, 98, 48); cairo_stroke(cr);
    } else {
        double eye_y = state == POSE_READING ? 51 : 48;
        fill_ellipse(cr, 60, eye_y, 4, 5.8, 0.10, 0.14, 0.15);
        fill_ellipse(cr, 91, eye_y, 4, 5.8, 0.10, 0.14, 0.15);
        fill_ellipse(cr, 61, eye_y - 2, 1.3, 1.5, 1, 1, 0.96);
        fill_ellipse(cr, 92, eye_y - 2, 1.3, 1.5, 1, 1, 0.96);
    }
    if (state == POSE_READING) {
        cairo_set_source_rgb(cr, 0.09, 0.39, 0.39); cairo_set_line_width(cr, 2.2);
        round_rect(cr, 49, 42, 22, 18, 6); round_rect(cr, 80, 42, 22, 18, 6); cairo_stroke(cr);
        cairo_move_to(cr, 71, 49); cairo_curve_to(cr, 73, 47, 78, 47, 80, 49); cairo_stroke(cr);
    }
    fill_ellipse(cr, 76, 64, 5, 3.8, 0.23, 0.13, 0.12);
    cairo_set_source_rgb(cr, 0.34, 0.21, 0.17); cairo_set_line_width(cr, 1.4);
    cairo_move_to(cr, 76, 67); cairo_line_to(cr, 76, 72);
    cairo_move_to(cr, 69, 73); cairo_curve_to(cr, 73, 76, 79, 76, 83, 73); cairo_stroke(cr);
    cairo_set_source_rgba(cr, 0.41, 0.26, 0.20, 0.70); cairo_set_line_width(cr, 1);
    cairo_move_to(cr, 49, 67); cairo_line_to(cr, 35, 63);
    cairo_move_to(cr, 49, 72); cairo_line_to(cr, 33, 73);
    cairo_move_to(cr, 103, 67); cairo_line_to(cr, 117, 63);
    cairo_move_to(cr, 103, 72); cairo_line_to(cr, 119, 73); cairo_stroke(cr);
    cairo_set_source_rgb(cr, 0.09, 0.43, 0.42);
    cairo_move_to(cr, 52, 80); cairo_curve_to(cr, 65, 91, 87, 91, 99, 80);
    cairo_line_to(cr, 97, 89); cairo_curve_to(cr, 82, 96, 65, 94, 54, 88); cairo_close_path(cr); cairo_fill(cr);
    cairo_move_to(cr, 86, 89); cairo_line_to(cr, 97, 91); cairo_line_to(cr, 90, 108);
    cairo_line_to(cr, 82, 102); cairo_close_path(cr); cairo_fill(cr);
    if (state == POSE_PRIVATE) {
        /* No sprite pixels are painted for this state. This covers both eyes
         * before the first ACK even if custom artwork puts its face elsewhere. */
        cairo_set_source_rgb(cr, 0.11, 0.16, 0.18);
        round_rect(cr, 43, 34, 65, 28, 12); cairo_fill(cr);
        static const double settle[] = { 0, -1, 0.6, 0 };
        double paw_y = settle[animation_frame];
        fill_ellipse(cr, 58, 50 + paw_y, 12, 16, 0.64, 0.29, 0.16);
        fill_ellipse(cr, 93, 50 + paw_y, 12, 16, 0.64, 0.29, 0.16);
        fill_ellipse(cr, 58, 53 + paw_y, 7, 9, 0.91, 0.65, 0.45);
        fill_ellipse(cr, 93, 53 + paw_y, 7, 9, 0.91, 0.65, 0.45);
        cairo_set_source_rgb(cr, 0.43, 0.22, 0.14); cairo_set_line_width(cr, 1.3);
        for (int finger = 0; finger < 3; ++finger) {
            cairo_move_to(cr, 53 + finger * 4, 40 + paw_y); cairo_line_to(cr, 53 + finger * 4, 45 + paw_y);
            cairo_move_to(cr, 88 + finger * 4, 40 + paw_y); cairo_line_to(cr, 88 + finger * 4, 45 + paw_y);
        }
        cairo_stroke(cr);
    } else {
        fill_ellipse(cr, 49, 105, 7, 15, 0.65, 0.29, 0.16);
        fill_ellipse(cr, 102, 105, 7, 15, 0.65, 0.29, 0.16);
    }
    cairo_restore(cr);
    if (state == POSE_PRIVATE) locked_eye(cr);
    if (state == POSE_UNKNOWN) {
        fill_ellipse(cr, 119, 24, 13, 13, 0.17, 0.21, 0.27);
        cairo_set_source_rgb(cr, 0.96, 0.88, 0.68); small_text(cr, "?", 119, 24, 19);
    }
}

static void thought_dots(cairo_t *cr) {
    /* Pure progress marks, never invented thoughts or task summaries. */
    cairo_set_source_rgba(cr, 0.08, 0.14, 0.19, 0.94);
    round_rect(cr, 4, 4, 45, 24, 12); cairo_fill(cr);
    ellipse(cr, 37, 32, 4, 3); cairo_fill(cr);
    ellipse(cr, 41, 39, 2, 2); cairo_fill(cr);
    for (unsigned i = 0; i < 3; ++i) {
        cairo_set_source_rgba(cr, 0.80, 0.94, 0.90, reduced_motion || frame % 3 == i ? 1 : 0.45);
        ellipse(cr, 15 + i * 11, 16, 2.5, 2.5); cairo_fill(cr);
    }
}

static void queue_badge(cairo_t *cr) {
    unsigned count = queue_count ? queue_count : bubble_kind != BUBBLE_NONE ? 1 : 0;
    if (!count) return;
    char text[8];
    if (count > 99) g_strlcpy(text, "99+", sizeof text);
    else g_snprintf(text, sizeof text, "%u", count);
    fill_ellipse(cr, 119, 24, count > 99 ? 17 : 14, 13, 0.96, 0.73, 0.32);
    cairo_set_source_rgb(cr, 0.15, 0.17, 0.20); small_text(cr, text, 119, 24, 12);
}

static gboolean pet_draw(GtkWidget *widget, cairo_t *cr, gpointer unused) {
    (void)unused;
    drew_current = FALSE;
    cairo_save(cr);
    cairo_set_operator(cr, CAIRO_OPERATOR_SOURCE);
    cairo_set_source_rgba(cr, 0, 0, 0, 0); cairo_paint(cr);
    cairo_set_operator(cr, CAIRO_OPERATOR_OVER);
    int width = gtk_widget_get_allocated_width(widget), height = gtk_widget_get_allocated_height(widget);
    double scale_x = width / (double)PET_SIZE, scale_y = height / (double)BODY_HEIGHT;
    double scale = scale_x < scale_y ? scale_x : scale_y;
    cairo_translate(cr, (width - PET_SIZE * scale) / 2, (height - BODY_HEIGHT * scale) / 2);
    cairo_scale(cr, scale, scale);
    if (sheet && pose != POSE_PRIVATE && pose != POSE_UNKNOWN) {
        GdkPixbuf *sprite = gdk_pixbuf_new_subpixbuf(sheet, (int)frame * frame_width,
            (int)pose * frame_height, frame_width, frame_height);
        double sx = PET_SIZE / (double)frame_width, sy = PET_SIZE / (double)frame_height;
        double sprite_scale = sx < sy ? sx : sy;
        cairo_save(cr);
        cairo_translate(cr, (PET_SIZE - frame_width * sprite_scale) / 2,
            (PET_SIZE - frame_height * sprite_scale) / 2);
        cairo_scale(cr, sprite_scale, sprite_scale);
        cairo_rectangle(cr, 0, 0, frame_width, frame_height); cairo_clip(cr);
        gdk_cairo_set_source_pixbuf(cr, sprite, 0, 0);
        cairo_pattern_set_filter(cairo_get_source(cr), CAIRO_FILTER_BILINEAR);
        cairo_paint(cr); cairo_restore(cr); g_object_unref(sprite);
    } else native_pet(cr, pose, frame);
    if (pose == POSE_WORKING || working_count || phase == PHASE_ACTIVE) thought_dots(cr);
    /* A queue badge must not conceal the unknown-permission question mark. */
    if (pose != POSE_UNKNOWN) queue_badge(cr);
    if (cu_seen && !awaiting_begin) {
        cairo_set_source_rgba(cr, 0.07, 0.13, 0.17, 0.94);
        round_rect(cr, 20, 145, 104, 14, 7); cairo_fill(cr);
        cairo_set_source_rgb(cr, 0.90, 0.96, 0.94);
        small_text(cr, "Alt + Esc · Stopp", 72, 152, 10);
    }
    cairo_restore(cr);
    if (cairo_status(cr) == CAIRO_STATUS_SUCCESS) {
        drawn_serial = render_serial; drawn_pose = pose; drawn_observation = observation;
        drawn_observation_active = observation_active; drew_current = TRUE;
    }
    return FALSE;
}

static gboolean pet_painted(GtkWidget *widget, cairo_t *cr, gpointer unused) {
    (void)cr; (void)unused;
    if (!monitor_unchanged()) {
        if (target_valid) hide_for_monitor();
        return FALSE;
    }
    if (!drew_current || !can_present() || !gtk_widget_get_mapped(widget) ||
        drawn_serial != render_serial || drawn_pose != current_pose() ||
        drawn_observation != observation || drawn_observation_active != observation_active ||
        g_get_monotonic_time() - last_heartbeat >= LEASE_US) return FALSE;
    emit_ready();
    if (stopped || !phase_ack_pending || awaiting_begin || ack_phase != phase) return FALSE;
    /* This runs after the drawing area's draw handler, including the native
     * privacy mask. A property change, map signal or heartbeat cannot ACK. */
    phase_ack_pending = FALSE;
    char *task = json_string(task_id), *name = json_string(phase_name(ack_phase));
    char *line = g_strdup_printf("{\"revision\":1,\"event\":\"phase_applied\","
        "\"task_id\":%s,\"phase\":%s,\"transition\":%" G_GUINT64_FORMAT "}",
        task, name, ack_transition);
    output_line(line);
    g_free(line); g_free(name); g_free(task);
    return FALSE;
}

static gboolean animate_frame(gpointer unused) {
    (void)unused;
    if (!can_present() || reduced_motion || ++animation_step >= animation_length) {
        animation_source = 0;
        return G_SOURCE_REMOVE;
    }
    frame = animation_step % 4;
    ++render_serial;
    gtk_widget_queue_draw(drawing);
    if (animation_step + 1 == animation_length) {
        animation_source = 0;
        return G_SOURCE_REMOVE;
    }
    return G_SOURCE_CONTINUE;
}

static void start_animation(void) {
    stop_animation();
    animation_step = 0;
    frame = reduced_motion && pose == POSE_PRIVATE ? 3 : 0;
    animation_length = pose == POSE_WORKING || pose == POSE_READING ||
        pose == POSE_ATTENTION || pose == POSE_PRIVATE ? 4 : 1;
    if (!reduced_motion && can_present() && animation_length > 1)
        animation_source = g_timeout_add(FRAME_INTERVAL_MS, animate_frame, NULL);
}

static void click_through(GtkWidget *widget, gpointer unused) {
    (void)unused;
    cairo_region_t *empty = cairo_region_create();
    gtk_widget_input_shape_combine_region(widget, empty);
    GdkWindow *window = gtk_widget_get_window(widget);
    if (window) gdk_window_input_shape_combine_region(window, empty, 0, 0);
    cairo_region_destroy(empty);
}

static void sync_visibility(void) {
    if (can_present()) {
        gtk_widget_show_all(body);
        if (bubble_kind != BUBBLE_NONE) {
            gtk_widget_show_all(bubble);
            gtk_widget_set_visible(stop_label, cu_seen && !awaiting_begin);
            gtk_widget_set_visible(count_label, queue_count > 0);
        } else gtk_widget_hide(bubble);
    } else {
        gtk_widget_hide(body); gtk_widget_hide(bubble);
        stop_animation();
    }
}

static void refresh_presentation(gboolean animate) {
    PetPose next = current_pose();
    gboolean changed_pose = pose != next;
    pose = next;
    if (animate || changed_pose) start_animation();
    ++render_serial;
    accessible_state();
    sync_visibility();
    gtk_widget_queue_draw(drawing);
}

static void bubble_action(GtkButton *button, gpointer value) {
    (void)button;
    if (stopped || action_sent || !bubble_id || !can_present() || !gtk_widget_get_mapped(bubble)) return;
    const char *action = value;
    char *id = json_string(bubble_id), *escaped = json_string(action);
    char *line = g_strdup_printf("{\"revision\":1,\"event\":\"action\",\"id\":%s,\"action\":%s}", id, escaped);
    action_sent = TRUE;
    gtk_widget_set_sensitive(open_button, FALSE); gtk_widget_set_sensitive(dismiss_button, FALSE);
    output_line(line);
    g_free(line); g_free(escaped); g_free(id);
}

static void update_bubble(void) {
    char count[16]; g_snprintf(count, sizeof count, "%u", queue_count);
    gtk_label_set_text(GTK_LABEL(count_label), count);
    gtk_label_set_text(GTK_LABEL(summary_label), bubble_summary ? bubble_summary : "");
    gtk_label_set_text(GTK_LABEL(kind_label), bubble_kind == BUBBLE_QUESTION ? "?" :
        bubble_kind == BUBBLE_APPROVAL ? "✓" : "↳");
    atk_object_set_name(gtk_widget_get_accessible(kind_label), bubble_kind == BUBBLE_QUESTION ? "Frage" :
        bubble_kind == BUBBLE_APPROVAL ? "Freigabe erforderlich" : "Ergebnis");
    atk_object_set_name(gtk_widget_get_accessible(count_label), "Nachrichten in der Warteschlange");
    gtk_widget_set_sensitive(open_button, !action_sent);
    gtk_widget_set_sensitive(dismiss_button, !action_sent);
    gtk_window_resize(GTK_WINDOW(bubble), 1, 1);
}

static void clear_bubble(void) {
    bubble_kind = BUBBLE_NONE; action_sent = FALSE;
    g_clear_pointer(&bubble_id, g_free); g_clear_pointer(&bubble_summary, g_free);
    update_bubble();
}

static gboolean window_deleted(GtkWidget *widget, GdkEvent *event, gpointer unused) {
    (void)widget; (void)event; (void)unused;
    finish("surface_closed");
    return TRUE;
}

static GtkWidget *new_surface(const char *name) {
    GtkWidget *window = gtk_window_new(GTK_WINDOW_TOPLEVEL);
    gtk_widget_set_name(window, name);
    gtk_window_set_decorated(GTK_WINDOW(window), FALSE);
    gtk_window_set_resizable(GTK_WINDOW(window), FALSE);
    gtk_window_set_accept_focus(GTK_WINDOW(window), FALSE);
    gtk_window_set_focus_on_map(GTK_WINDOW(window), FALSE);
    gtk_widget_set_can_focus(window, FALSE);
    GdkVisual *visual = gdk_screen_get_rgba_visual(gtk_widget_get_screen(window));
    if (visual) gtk_widget_set_visual(window, visual);
    gtk_widget_set_app_paintable(window, TRUE);
    gtk_layer_init_for_window(GTK_WINDOW(window));
    gtk_layer_set_namespace(GTK_WINDOW(window), "weasel-computer-use-pet");
    gtk_layer_set_layer(GTK_WINDOW(window), GTK_LAYER_SHELL_LAYER_OVERLAY);
    gtk_layer_set_monitor(GTK_WINDOW(window), target);
    gtk_layer_set_exclusive_zone(GTK_WINDOW(window), -1);
    gtk_layer_set_keyboard_mode(GTK_WINDOW(window), GTK_LAYER_SHELL_KEYBOARD_MODE_NONE);
    gtk_layer_set_anchor(GTK_WINDOW(window), GTK_LAYER_SHELL_EDGE_RIGHT, TRUE);
    gtk_layer_set_anchor(GTK_WINDOW(window), GTK_LAYER_SHELL_EDGE_BOTTOM, TRUE);
    gtk_layer_set_margin(GTK_WINDOW(window), GTK_LAYER_SHELL_EDGE_RIGHT, 14);
    g_signal_connect(window, "delete-event", G_CALLBACK(window_deleted), NULL);
    return window;
}

static void position_bubble(void) {
    int width = MIN(360, expected.width - 28);
    gtk_label_set_max_width_chars(GTK_LABEL(summary_label), MAX(18, (width - 32) / 10));
    gtk_widget_set_size_request(summary_label, width - 32, 32);
    gtk_widget_set_size_request(bubble, width, -1);
    gtk_layer_set_margin(GTK_WINDOW(bubble), GTK_LAYER_SHELL_EDGE_BOTTOM,
        MIN(174, MAX(8, expected.height - 184)));
}

static void create_surfaces(void) {
    body = new_surface("weasel-pet-body");
    gtk_layer_set_margin(GTK_WINDOW(body), GTK_LAYER_SHELL_EDGE_BOTTOM, 10);
    drawing = gtk_drawing_area_new();
    gtk_widget_set_size_request(drawing, PET_SIZE, BODY_HEIGHT);
    gtk_widget_set_can_focus(drawing, FALSE);
    gtk_container_add(GTK_CONTAINER(body), drawing);
    g_signal_connect(body, "realize", G_CALLBACK(click_through), NULL);
    g_signal_connect(body, "map", G_CALLBACK(click_through), NULL);
    g_signal_connect(drawing, "draw", G_CALLBACK(pet_draw), NULL);
    g_signal_connect_after(drawing, "draw", G_CALLBACK(pet_painted), NULL);

    bubble = new_surface("weasel-pet-bubble");
    GtkWidget *card = gtk_box_new(GTK_ORIENTATION_VERTICAL, 7);
    gtk_widget_set_name(card, "weasel-pet-card");
    gtk_container_set_border_width(GTK_CONTAINER(card), 12);
    gtk_container_add(GTK_CONTAINER(bubble), card);
    GtkWidget *header = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 8);
    kind_label = gtk_label_new("?"); gtk_widget_set_name(kind_label, "pet-message-kind");
    count_label = gtk_label_new("0"); gtk_widget_set_name(count_label, "pet-message-count");
    stop_label = gtk_label_new("Alt + Esc · Stopp"); gtk_widget_set_name(stop_label, "pet-stop-hint");
    gtk_box_pack_start(GTK_BOX(header), kind_label, FALSE, FALSE, 0);
    gtk_box_pack_start(GTK_BOX(header), count_label, FALSE, FALSE, 0);
    gtk_box_pack_end(GTK_BOX(header), stop_label, FALSE, FALSE, 0);
    gtk_box_pack_start(GTK_BOX(card), header, FALSE, FALSE, 0);
    summary_label = gtk_label_new("");
    gtk_label_set_xalign(GTK_LABEL(summary_label), 0);
    gtk_label_set_line_wrap(GTK_LABEL(summary_label), TRUE);
    gtk_label_set_line_wrap_mode(GTK_LABEL(summary_label), PANGO_WRAP_WORD_CHAR);
    gtk_label_set_ellipsize(GTK_LABEL(summary_label), PANGO_ELLIPSIZE_END);
    gtk_label_set_lines(GTK_LABEL(summary_label), 4);
    gtk_widget_set_name(summary_label, "pet-summary");
    gtk_box_pack_start(GTK_BOX(card), summary_label, FALSE, FALSE, 0);
    GtkWidget *actions = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 8);
    open_button = gtk_button_new_with_label("Öffnen");
    dismiss_button = gtk_button_new_with_label("Ausblenden");
    gtk_widget_set_can_focus(open_button, FALSE); gtk_widget_set_can_focus(dismiss_button, FALSE);
    gtk_widget_set_focus_on_click(open_button, FALSE); gtk_widget_set_focus_on_click(dismiss_button, FALSE);
    gtk_box_pack_end(GTK_BOX(actions), dismiss_button, FALSE, FALSE, 0);
    gtk_box_pack_end(GTK_BOX(actions), open_button, FALSE, FALSE, 0);
    gtk_box_pack_start(GTK_BOX(card), actions, FALSE, FALSE, 0);
    g_signal_connect(open_button, "clicked", G_CALLBACK(bubble_action), "open");
    g_signal_connect(dismiss_button, "clicked", G_CALLBACK(bubble_action), "dismiss");
    position_bubble();
}

/* Keep a strong reference across remove/add notifications and only select a
 * unique, exact logical geometry. There is no primary/default-output path. */
static GdkMonitor *find_monitor(const GdkRectangle *rect) {
    GdkDisplay *display = gdk_display_get_default();
    GdkMonitor *found = NULL;
    for (int i = 0; i < gdk_display_get_n_monitors(display); ++i) {
        GdkMonitor *candidate = gdk_display_get_monitor(display, i);
        GdkRectangle actual; gdk_monitor_get_geometry(candidate, &actual);
        if (monitor_geometry_matches(actual.x, actual.y, actual.width, actual.height,
                                     rect->x, rect->y, rect->width, rect->height)) {
            if (found) return NULL;
            found = candidate;
        }
    }
    return found ? g_object_ref(found) : NULL;
}

static void hide_for_monitor(void) {
    target_valid = FALSE;
    stop_animation();
    gtk_widget_hide(body); gtk_widget_hide(bubble);
    drew_current = FALSE;
}

static gboolean select_monitor(void) {
    GdkMonitor *replacement = find_monitor(&expected);
    if (!replacement) return FALSE;
    gdk_monitor_get_geometry(replacement, &initial_geometry);
    gtk_layer_set_monitor(GTK_WINDOW(body), replacement);
    gtk_layer_set_monitor(GTK_WINDOW(bubble), replacement);
    g_clear_object(&target); target = replacement;
    target_valid = TRUE;
    position_bubble();
    refresh_presentation(TRUE);
    return TRUE;
}

static void monitor_removed(GdkDisplay *display, GdkMonitor *monitor, gpointer unused) {
    (void)display; (void)unused;
    if (monitor == target && !stopped) hide_for_monitor();
}

static guint32 png_u32(const guchar *bytes) {
    return ((guint32)bytes[0] << 24) | ((guint32)bytes[1] << 16) |
        ((guint32)bytes[2] << 8) | (guint32)bytes[3];
}

static gboolean load_sheet(const char *path, int fw, int fh) {
    static const guchar signature[8] = { 137, 'P', 'N', 'G', 13, 10, 26, 10 };
    if (!path || !g_path_is_absolute(path) || !safe_text(path, 4096, 4096) ||
        fw < 64 || fw > 512 || fh < 64 || fh > 512) return FALSE;
    struct stat current;
    if (sheet && sheet_path && !strcmp(path, sheet_path) && fw == frame_width && fh == frame_height &&
        lstat(path, &current) == 0 && S_ISREG(current.st_mode) && current.st_dev == sheet_info.st_dev &&
        current.st_ino == sheet_info.st_ino && current.st_size == sheet_info.st_size &&
        current.st_mtim.tv_sec == sheet_info.st_mtim.tv_sec && current.st_mtim.tv_nsec == sheet_info.st_mtim.tv_nsec)
        return TRUE;
    int fd = open(path, O_RDONLY | O_CLOEXEC | O_NONBLOCK | O_NOFOLLOW);
    if (fd < 0) return FALSE;
    struct stat info;
    if (fstat(fd, &info) < 0 || !S_ISREG(info.st_mode) || info.st_size < 33 ||
        info.st_size > (off_t)MAX_ASSET_BYTES) { close(fd); return FALSE; }
    GByteArray *data = g_byte_array_sized_new((guint)info.st_size);
    guchar chunk[8192];
    gboolean valid = TRUE;
    for (;;) {
        ssize_t length = read(fd, chunk, sizeof chunk);
        if (length < 0 && errno == EINTR) continue;
        if (length < 0 || (length > 0 && (gsize)length > MAX_ASSET_BYTES - data->len)) { valid = FALSE; break; }
        if (!length) break;
        g_byte_array_append(data, chunk, (guint)length);
    }
    close(fd);
    /* Check allocation bounds before entering a decoder, not after a large
     * pixbuf has already been allocated. Only 8-bit true RGBA PNG is accepted. */
    if (!valid || data->len < 33 || memcmp(data->data, signature, 8) ||
        png_u32(data->data + 8) != 13 || memcmp(data->data + 12, "IHDR", 4) ||
        png_u32(data->data + 16) != (guint32)fw * 4 ||
        png_u32(data->data + 20) != (guint32)fh * 8 || data->data[24] != 8 ||
        data->data[25] != 6 || data->data[26] != 0 || data->data[27] != 0 || data->data[28] > 1) {
        g_byte_array_unref(data); return FALSE;
    }
    GError *error = NULL;
    GdkPixbufLoader *loader = gdk_pixbuf_loader_new_with_type("png", &error);
    if (!loader) { g_clear_error(&error); g_byte_array_unref(data); return FALSE; }
    valid = gdk_pixbuf_loader_write(loader, data->data, data->len, &error);
    if (!gdk_pixbuf_loader_close(loader, valid ? &error : NULL)) valid = FALSE;
    GdkPixbuf *decoded = valid ? gdk_pixbuf_loader_get_pixbuf(loader) : NULL;
    if (!decoded || !gdk_pixbuf_get_has_alpha(decoded) || gdk_pixbuf_get_n_channels(decoded) != 4 ||
        gdk_pixbuf_get_bits_per_sample(decoded) != 8 || gdk_pixbuf_get_width(decoded) != fw * 4 ||
        gdk_pixbuf_get_height(decoded) != fh * 8) valid = FALSE;
    if (valid) {
        g_object_ref(decoded); g_clear_object(&sheet); sheet = decoded;
        frame_width = fw; frame_height = fh;
        g_free(sheet_path); sheet_path = g_strdup(path); sheet_info = info; ++asset_serial;
    }
    g_clear_error(&error); g_object_unref(loader); g_byte_array_unref(data);
    return valid;
}

/* Default assets have the same ABI; infer the frame dimensions from IHDR,
 * then pass through the same bounded RGBA decoder as explicit asset commands. */
static gboolean load_default_sheet(const char *path) {
    if (!g_path_is_absolute(path) || !safe_text(path, 4096, 4096)) return FALSE;
    int fd = open(path, O_RDONLY | O_CLOEXEC | O_NONBLOCK | O_NOFOLLOW);
    if (fd < 0) return FALSE;
    struct stat info;
    if (fstat(fd, &info) < 0 || !S_ISREG(info.st_mode) || info.st_size < 33 ||
        info.st_size > (off_t)MAX_ASSET_BYTES) { close(fd); return FALSE; }
    guchar header[24]; size_t offset = 0;
    while (offset < sizeof header) {
        ssize_t length = read(fd, header + offset, sizeof header - offset);
        if (length < 0 && errno == EINTR) continue;
        if (length <= 0) break;
        offset += (size_t)length;
    }
    close(fd);
    if (offset != sizeof header) return FALSE;
    guint32 width = png_u32(header + 16), height = png_u32(header + 20);
    if (width < 256 || width > 2048 || width % 4 || height < 512 || height > 4096 || height % 8)
        return FALSE;
    return load_sheet(path, (int)(width / 4), (int)(height / 8));
}

static void process_line(const char *line) {
    if (!g_utf8_validate(line, -1, NULL)) { finish("invalid_protocol"); return; }
    if (!strncmp(line, "v1 activity ", 12)) {
        if (!begun || awaiting_begin || !safe_text(line + 12, 1024, 120)) { finish("invalid_activity"); return; }
        g_free(activity); activity = g_strdup(line + 12);
        accessible_state();
        return;
    }
    char **words = g_strsplit(line, " ", -1);
    gsize count = g_strv_length(words);
    guint64 number = 0;
    if (count < 2 || strcmp(words[0], "v1")) { finish("invalid_protocol"); goto done; }
    for (gsize i = 0; i < count; ++i)
        if (!*words[i]) { finish("invalid_protocol"); goto done; }
    if (count == 2 && !strcmp(words[1], "end")) finish("explicit_end");
    else if (count == 2 && !strcmp(words[1], "begin") && (!begun || awaiting_begin)) {
        begun = TRUE; awaiting_begin = FALSE;
        last_heartbeat = g_get_monotonic_time();
        refresh_presentation(TRUE);
    } else if (count == 2 && !strcmp(words[1], "heartbeat") && begun) {
        last_heartbeat = g_get_monotonic_time(); /* No queue_draw, no animation restart. */
    } else if ((count == 3 || count == 4) && !strcmp(words[1], "phase") && begun && !awaiting_begin) {
        Phase next;
        if (!parse_phase(words[2], &next) ||
            (count == 4 && !unsigned_number(words[3], G_MAXUINT64, &number))) {
            finish("invalid_phase"); goto done;
        }
        gboolean changed = phase != next;
        phase = next; phase_ack_pending = count == 4;
        /* The persistent companion can wait without owning Computer Use.
         * Only a CU phase/preparation or an actual read enables the stop hint. */
        if (count == 4 || next == PHASE_ACTIVE || next == PHASE_OBSERVING) cu_seen = TRUE;
        ack_phase = next; ack_transition = number;
        if (changed || phase_ack_pending) refresh_presentation(changed);
    } else if (count == 4 && !strcmp(words[1], "observation")) {
        Observation next = !strcmp(words[2], "enabled") ? OBS_ENABLED :
            !strcmp(words[2], "disabled") ? OBS_DISABLED : OBS_UNKNOWN;
        if ((next == OBS_UNKNOWN && strcmp(words[2], "unknown")) ||
            (strcmp(words[3], "active") && strcmp(words[3], "idle")) ||
            (!strcmp(words[3], "active") && next != OBS_ENABLED)) {
            finish("invalid_observation"); goto done;
        }
        gboolean active = !strcmp(words[3], "active");
        if (active) cu_seen = TRUE;
        if (observation != next || observation_active != active) {
            observation = next; observation_active = active;
            refresh_presentation(TRUE);
        }
    } else if (count == 3 && !strcmp(words[1], "visible") &&
               (!strcmp(words[2], "0") || !strcmp(words[2], "1"))) {
        gboolean next = !strcmp(words[2], "1");
        if (visible != next) { visible = next; refresh_presentation(TRUE); }
    } else if (count == 3 && (!strcmp(words[1], "working") || !strcmp(words[1], "count")) &&
               unsigned_number(words[2], 10000, &number)) {
        unsigned *field = !strcmp(words[1], "working") ? &working_count : &queue_count;
        if (*field != (unsigned)number) {
            *field = (unsigned)number; update_bubble(); refresh_presentation(FALSE);
        }
    } else if (count == 3 && !strcmp(words[1], "motion") &&
               (!strcmp(words[2], "reduced") || !strcmp(words[2], "full"))) {
        gboolean next = !strcmp(words[2], "reduced");
        if (reduced_motion != next) { reduced_motion = next; refresh_presentation(TRUE); }
    } else if (count == 3 && !strcmp(words[1], "bubble") && !strcmp(words[2], "clear")) {
        clear_bubble(); refresh_presentation(FALSE);
    } else if (count == 5 && !strcmp(words[1], "bubble")) {
        BubbleKind kind = !strcmp(words[2], "approval") ? BUBBLE_APPROVAL :
            !strcmp(words[2], "question") ? BUBBLE_QUESTION :
            !strcmp(words[2], "result") ? BUBBLE_RESULT : BUBBLE_NONE;
        char *text = decode_text(words[4], MAX_SUMMARY_BYTES);
        if (kind == BUBBLE_NONE || !identifier(words[3]) ||
            !safe_text(text, MAX_SUMMARY_BYTES, MAX_SUMMARY_CHARS)) {
            g_free(text); finish("invalid_bubble"); goto done;
        }
        gboolean same = bubble_id && !strcmp(bubble_id, words[3]);
        g_free(bubble_id); g_free(bubble_summary);
        bubble_id = g_strdup(words[3]); bubble_summary = text; bubble_kind = kind;
        if (!same) action_sent = FALSE;
        update_bubble(); refresh_presentation(!same);
    } else if (count == 7 && !strcmp(words[1], "monitor")) {
        GdkRectangle rect;
        if (!identifier(words[2]) || !geometry(words + 3, &rect)) { finish("invalid_monitor"); goto done; }
        if (!strcmp(output_name, words[2]) && rect.x == expected.x && rect.y == expected.y &&
            rect.width == expected.width && rect.height == expected.height && target_valid) goto done;
        hide_for_monitor();
        g_free(output_name); output_name = g_strdup(words[2]); expected = rect;
        select_monitor(); /* If absent, remain hidden until this exact geometry exists. */
    } else if (count == 3 && !strcmp(words[1], "bind")) {
        if (!identifier(words[2]) || cu_in_progress() || phase_ack_pending) { finish("invalid_bind"); goto done; }
        g_free(task_id); task_id = g_strdup(words[2]);
        g_free(activity); activity = g_strdup("");
        phase_ack_pending = FALSE; phase = PHASE_UNKNOWN;
        cu_seen = FALSE;
        observation = OBS_UNKNOWN; observation_active = FALSE;
        awaiting_begin = TRUE; ready_sent = FALSE;
        refresh_presentation(FALSE);
        /* Binding readiness is transport/surface readiness, never a privacy
         * ACK. The next begin opens phase processing for the new identity. */
        emit_ready();
    } else if (count == 3 && !strcmp(words[1], "asset") && !strcmp(words[2], "default")) {
        guint64 previous_asset = asset_serial;
        const char *path = g_getenv("WEASEL_PET_SHEET");
        if (path && *path) {
            if (!load_default_sheet(path)) { finish("invalid_asset"); goto done; }
        } else if (sheet) { g_clear_object(&sheet); g_clear_pointer(&sheet_path, g_free); ++asset_serial; }
        if (asset_serial != previous_asset) refresh_presentation(TRUE);
    } else if (count == 5 && !strcmp(words[1], "asset")) {
        guint64 previous_asset = asset_serial;
        guint64 fw, fh;
        char *path = decode_text(words[2], 4096);
        gboolean valid = path && unsigned_number(words[3], 512, &fw) && fw >= 64 &&
            unsigned_number(words[4], 512, &fh) && fh >= 64 && load_sheet(path, (int)fw, (int)fh);
        g_free(path);
        if (!valid) { finish("invalid_asset"); goto done; }
        if (asset_serial != previous_asset) refresh_presentation(TRUE);
    } else finish("invalid_protocol");
done:
    g_strfreev(words);
}

static gboolean input_ready(gint fd, GIOCondition condition, gpointer unused) {
    (void)unused;
    char buffer[4096];
    /* Bound work per main-loop dispatch so a busy pipe cannot starve painting,
     * input to the bubble, monitor loss handling or the four-second lease. */
    for (unsigned batch = 0; batch < 16; ++batch) {
        ssize_t length = read(fd, buffer, sizeof buffer);
        if (length > 0) {
            for (ssize_t i = 0; i < length; ++i) {
                if (buffer[i] == '\n') {
                    pending[pending_len] = '\0'; process_line(pending); pending_len = 0;
                    if (stopped) return G_SOURCE_REMOVE;
                } else if (!buffer[i] || pending_len == MAX_LINE) {
                    finish("invalid_protocol"); return G_SOURCE_REMOVE;
                } else pending[pending_len++] = buffer[i];
            }
        } else if (length == 0) { finish("owner_pipe_closed"); return G_SOURCE_REMOVE; }
        else if (errno == EINTR) continue;
        else if (errno != EAGAIN && errno != EWOULDBLOCK) {
            finish("owner_pipe_error"); return G_SOURCE_REMOVE;
        } else {
            if (condition & (G_IO_HUP | G_IO_ERR | G_IO_NVAL)) {
                finish("owner_pipe_closed"); return G_SOURCE_REMOVE;
            }
            return G_SOURCE_CONTINUE;
        }
    }
    return G_SOURCE_CONTINUE;
}

static gboolean lease_timer(gpointer unused) {
    (void)unused;
    if (stopped) return G_SOURCE_REMOVE;
    if (g_get_monotonic_time() - last_heartbeat >= LEASE_US) {
        finish("lease_expired"); return G_SOURCE_REMOVE;
    }
    if (target_valid && !monitor_unchanged()) hide_for_monitor();
    if (!target_valid) select_monitor();
    return G_SOURCE_CONTINUE;
}

static gboolean interrupted(gpointer unused) {
    (void)unused;
    finish("signal");
    return G_SOURCE_REMOVE;
}

int main(int argc, char **argv) {
    if (argc == 5 && !strcmp(argv[1], "--validate-sheet")) {
        guint64 fw, fh;
        gboolean valid = unsigned_number(argv[3], 512, &fw) && fw >= 64 &&
            unsigned_number(argv[4], 512, &fh) && fh >= 64 && load_sheet(argv[2], (int)fw, (int)fh);
        g_clear_object(&sheet);
        return valid ? 0 : 5;
    }
    if ((argc != 7 && argc != 8) || !identifier(argv[1]) || !identifier(argv[2]) ||
        !geometry(argv + 3, &expected) || (argc == 8 && !safe_text(argv[7], 1024, 120))) {
        fprintf(stderr, "usage: pet-renderer OUTPUT TASK_ID X Y LOGICAL_WIDTH LOGICAL_HEIGHT [ACTIVITY]\n");
        return 2;
    }
    output_name = g_strdup(argv[1]); task_id = g_strdup(argv[2]);
    cu_seen = g_strcmp0(g_getenv("WEASEL_PET_COMPANION"), "1") != 0;
    activity = g_strdup(argc == 8 ? argv[7] : "");
    signal(SIGPIPE, SIG_IGN);
    g_setenv("GDK_BACKEND", "wayland", TRUE);
    if (!gtk_init_check(NULL, NULL) || !gtk_layer_is_supported()) {
        fprintf(stderr, "Wayland layer-shell unavailable\n"); return 3;
    }
    target = find_monitor(&expected);
    if (!target) { fprintf(stderr, "Selected monitor missing or ambiguous\n"); return 4; }
    target_valid = TRUE;
    gdk_monitor_get_geometry(target, &initial_geometry);
    const char *default_asset = g_getenv("WEASEL_PET_SHEET");
    if (default_asset && *default_asset && !load_default_sheet(default_asset)) {
        fprintf(stderr, "Default pet asset rejected: expected bounded 4 by 8 RGBA PNG\n");
        finish("invalid_asset"); return 5;
    }
    css = gtk_css_provider_new();
    const char *style =
        "#weasel-pet-body, #weasel-pet-bubble { background: transparent; border: none; padding: 0; margin: 0; }"
        "#weasel-pet-card { background: #17262e; color: #edf4f1; border: 1px solid #536b73; border-radius: 17px; }"
        "#weasel-pet-card label { color: #edf4f1; font: 13px sans-serif; }"
        "#weasel-pet-card #pet-message-kind { color: #f4cd80; font-weight: bold; }"
        "#weasel-pet-card #pet-message-count { color: #d4eae1; font-size: 11px; }"
        "#weasel-pet-card #pet-stop-hint { color: #c0d2d4; font-size: 11px; }"
        "#weasel-pet-card button { background: #25454d; color: #f3faf6; border: 1px solid #63818a;"
        " border-radius: 8px; padding: 4px 10px; min-height: 20px; min-width: 0; box-shadow: none; text-shadow: none; font: 12px sans-serif; }"
        "#weasel-pet-card button:hover { background: #35606a; }"
        "#weasel-pet-card button:active { background: #193b44; }"
        "#weasel-pet-card button:disabled { color: #b5c4c5; background: #24353c; }";
    GError *error = NULL;
    if (!gtk_css_provider_load_from_data(css, style, -1, &error)) {
        fprintf(stderr, "Pet style invalid\n"); g_clear_error(&error); return 5;
    }
    gtk_style_context_add_provider_for_screen(gdk_screen_get_default(), GTK_STYLE_PROVIDER(css),
        GTK_STYLE_PROVIDER_PRIORITY_APPLICATION);
    create_surfaces(); accessible_state();
    g_signal_connect(gdk_display_get_default(), "monitor-removed", G_CALLBACK(monitor_removed), NULL);
    int flags = fcntl(STDIN_FILENO, F_GETFL);
    if (flags < 0 || fcntl(STDIN_FILENO, F_SETFL, flags | O_NONBLOCK) < 0) {
        finish("owner_pipe_error"); return 6;
    }
    int output_flags = fcntl(STDOUT_FILENO, F_GETFL);
    if (output_flags < 0 || fcntl(STDOUT_FILENO, F_SETFL, output_flags | O_NONBLOCK) < 0) {
        finish("event_pipe_error"); return 6;
    }
    last_heartbeat = g_get_monotonic_time();
    guint input_source = g_unix_fd_add(STDIN_FILENO, G_IO_IN | G_IO_HUP | G_IO_ERR | G_IO_NVAL, input_ready, NULL);
    guint lease_source = g_timeout_add(100, lease_timer, NULL);
    guint term_source = g_unix_signal_add(SIGTERM, interrupted, NULL);
    guint int_source = g_unix_signal_add(SIGINT, interrupted, NULL);
    gtk_main();
    /* A source can already have removed itself. Look it up before removal to
     * avoid GLib criticals on normal EOF, lease expiry or signal shutdown. */
    guint sources[] = { input_source, lease_source, term_source, int_source };
    for (unsigned i = 0; i < G_N_ELEMENTS(sources); ++i)
        if (g_main_context_find_source_by_id(NULL, sources[i])) g_source_remove(sources[i]);
    gtk_widget_destroy(bubble); gtk_widget_destroy(body);
    g_clear_object(&sheet); g_clear_object(&target); g_clear_object(&css); g_free(sheet_path);
    g_free(output_name); g_free(task_id); g_free(activity); g_free(bubble_id); g_free(bubble_summary);
    return output_failed ? 6 : 0;
}
