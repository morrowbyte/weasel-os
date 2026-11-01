/* SPDX-License-Identifier: MIT
 * Deliberately separate from the Rust desktop actor: presentation only.
 * Five small, noninteractive layer surfaces avoid a full-monitor buffer.
 */
#define _POSIX_C_SOURCE 200809L
#include <gtk/gtk.h>
#include <gtk-layer-shell.h>
#include <glib-unix.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "monitor-match.h"

#define LEASE_US (4 * G_USEC_PER_SEC)
#define MAX_LINE 512
static GtkWidget *surfaces[5];
static GdkMonitor *target;
static GdkRectangle expected;
static GdkRectangle initial_gdk_geometry;
static char *output_name, *task_id;
static const char *activity = "Computer Use";
static GtkWidget *activity_label;
static gint64 last_heartbeat;
static gboolean begun, stopped;
static gboolean target_valid = TRUE;
static unsigned mapped;
static char pending[MAX_LINE];
static size_t pending_len;

static void finish(const char *reason) {
    if (stopped) return;
    stopped = TRUE;
    for (unsigned i = 0; i < G_N_ELEMENTS(surfaces); i++)
        if (surfaces[i]) gtk_widget_hide(surfaces[i]);
    printf("{\"revision\":1,\"event\":\"ended\",\"reason\":\"%s\"}\n", reason);
    fflush(stdout);
    gtk_main_quit();
}

static void click_through(GtkWidget *widget, gpointer unused) {
    (void)unused;
    cairo_region_t *empty = cairo_region_create();
    gtk_widget_input_shape_combine_region(widget, empty);
    GdkWindow *window = gtk_widget_get_window(widget);
    if (window) gdk_window_input_shape_combine_region(window, empty, 0, 0);
    cairo_region_destroy(empty);
}

static void surface_mapped(GtkWidget *widget, gpointer unused) {
    click_through(widget, unused);
    if (++mapped == G_N_ELEMENTS(surfaces)) {
        printf("{\"revision\":1,\"event\":\"ready\",\"task_id\":\"%s\","
               "\"output\":\"%s\",\"surfaces\":5,\"keyboard\":\"none\","
               "\"input_region\":\"empty\",\"exclusive_zone\":-1,"
               "\"geometry\":[%d,%d,%d,%d]}\n", task_id, output_name,
               expected.x, expected.y, expected.width, expected.height);
        fflush(stdout);
    }
}

static gboolean window_deleted(GtkWidget *widget, GdkEvent *event, gpointer unused) {
    (void)widget; (void)event; (void)unused;
    finish("surface_closed");
    return TRUE;
}

static GtkWidget *surface(int width, int height, gboolean left, gboolean right,
                          gboolean top, gboolean bottom, gboolean label) {
    GtkWidget *window = gtk_window_new(GTK_WINDOW_TOPLEVEL);
    gtk_window_set_decorated(GTK_WINDOW(window), FALSE);
    gtk_window_set_accept_focus(GTK_WINDOW(window), FALSE);
    gtk_window_set_focus_on_map(GTK_WINDOW(window), FALSE);
    gtk_widget_set_can_focus(window, FALSE);
    gtk_widget_set_name(window, label ? "computer-use-label" : "computer-use-edge");
    gtk_widget_set_size_request(window, width, height);
    gtk_layer_init_for_window(GTK_WINDOW(window));
    gtk_layer_set_namespace(GTK_WINDOW(window), "weasel-computer-use-indicator");
    gtk_layer_set_layer(GTK_WINDOW(window), GTK_LAYER_SHELL_LAYER_OVERLAY);
    gtk_layer_set_monitor(GTK_WINDOW(window), target);
    /* -1 means draw at physical output edges, without reserving workspace. */
    gtk_layer_set_exclusive_zone(GTK_WINDOW(window), -1);
    gtk_layer_set_keyboard_mode(GTK_WINDOW(window), GTK_LAYER_SHELL_KEYBOARD_MODE_NONE);
    gtk_layer_set_anchor(GTK_WINDOW(window), GTK_LAYER_SHELL_EDGE_LEFT, left);
    gtk_layer_set_anchor(GTK_WINDOW(window), GTK_LAYER_SHELL_EDGE_RIGHT, right);
    gtk_layer_set_anchor(GTK_WINDOW(window), GTK_LAYER_SHELL_EDGE_TOP, top);
    gtk_layer_set_anchor(GTK_WINDOW(window), GTK_LAYER_SHELL_EDGE_BOTTOM, bottom);
    if (label) {
        GtkWidget *box = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 14);
        gtk_widget_set_margin_start(box, 16);
        gtk_widget_set_margin_end(box, 16);
        gtk_widget_set_margin_top(box, 8);
        gtk_widget_set_margin_bottom(box, 8);
        GtkWidget *text = gtk_label_new(activity);
        activity_label = text;
        gtk_label_set_ellipsize(GTK_LABEL(text), PANGO_ELLIPSIZE_END);
        gtk_label_set_width_chars(GTK_LABEL(text), 30);
        gtk_label_set_max_width_chars(GTK_LABEL(text), 55);
        gtk_label_set_xalign(GTK_LABEL(text), 0.0f);
        GtkWidget *shortcut = gtk_label_new("Alt + Esc  ·  Abbrechen");
        gtk_widget_set_name(shortcut, "abort-shortcut");
        gtk_widget_set_can_focus(text, FALSE);
        gtk_box_pack_start(GTK_BOX(box), text, TRUE, TRUE, 0);
        gtk_box_pack_end(GTK_BOX(box), shortcut, FALSE, FALSE, 0);
        gtk_container_add(GTK_CONTAINER(window), box);
        gtk_layer_set_margin(GTK_WINDOW(window), GTK_LAYER_SHELL_EDGE_TOP, 12);
    }
    g_signal_connect(window, "realize", G_CALLBACK(click_through), NULL);
    g_signal_connect(window, "map", G_CALLBACK(surface_mapped), NULL);
    g_signal_connect(window, "delete-event", G_CALLBACK(window_deleted), NULL);
    return window;
}

static void process_line(const char *line) {
    if (!strcmp(line, "v1 begin") && !begun) {
        begun = TRUE;
        last_heartbeat = g_get_monotonic_time();
        for (unsigned i = 0; i < G_N_ELEMENTS(surfaces); i++) gtk_widget_show_all(surfaces[i]);
    } else if (!strcmp(line, "v1 heartbeat") && begun) {
        last_heartbeat = g_get_monotonic_time();
    } else if (!strncmp(line, "v1 activity ", 12) && begun) {
        const char *value = line + 12;
        if (!*value || !g_utf8_validate(value, -1, NULL) || g_utf8_strlen(value, -1) > 120) {
            finish("invalid_activity");
        } else {
            gtk_label_set_text(GTK_LABEL(activity_label), value);
        }
    } else if (!strcmp(line, "v1 end")) {
        finish("explicit_end");
    } else {
        finish("invalid_protocol");
    }
}

static gboolean input_ready(gint fd, GIOCondition condition, gpointer unused) {
    (void)unused;
    char buffer[128];
    ssize_t length;
    while ((length = read(fd, buffer, sizeof buffer)) > 0) {
        for (ssize_t i = 0; i < length; i++) {
            if (buffer[i] == '\n') {
                pending[pending_len] = '\0';
                process_line(pending);
                pending_len = 0;
                if (stopped) return G_SOURCE_REMOVE;
            } else if (buffer[i] == '\0' || pending_len + 1 >= sizeof pending) {
                finish("invalid_protocol");
                return G_SOURCE_REMOVE;
            } else pending[pending_len++] = buffer[i];
        }
    }
    if (length == 0 || condition & (G_IO_HUP | G_IO_ERR | G_IO_NVAL)) {
        finish("owner_pipe_closed");
        return G_SOURCE_REMOVE;
    }
    if (length < 0 && errno != EAGAIN && errno != EINTR) {
        finish("owner_pipe_error");
        return G_SOURCE_REMOVE;
    }
    return G_SOURCE_CONTINUE;
}

static gboolean timer(gpointer unused) {
    (void)unused;
    GdkRectangle current;
    gdk_monitor_get_geometry(target, &current);
    if (!target_valid || current.x != initial_gdk_geometry.x ||
        current.y != initial_gdk_geometry.y ||
        current.width != initial_gdk_geometry.width ||
        current.height != initial_gdk_geometry.height) {
        finish("monitor_changed");
        return G_SOURCE_REMOVE;
    }
    if (g_get_monotonic_time() - last_heartbeat > LEASE_US) {
        finish("lease_expired");
        return G_SOURCE_REMOVE;
    }
    return G_SOURCE_CONTINUE;
}

static void monitor_removed(GdkDisplay *display, GdkMonitor *monitor, gpointer unused) {
    (void)display; (void)unused;
    if (monitor == target) target_valid = FALSE;
}

static gboolean interrupted(gpointer unused) {
    (void)unused;
    finish("signal");
    return G_SOURCE_REMOVE;
}

static gboolean identifier(const char *value) {
    if (!value || !*value || strlen(value) > 128) return FALSE;
    for (const char *p = value; *p; p++)
        if (!g_ascii_isalnum(*p) && *p != '_' && *p != '-' && *p != '.') return FALSE;
    return TRUE;
}

int main(int argc, char **argv) {
    if ((argc != 7 && argc != 8) || !identifier(argv[1]) || !identifier(argv[2])) {
        fprintf(stderr, "usage: renderer OUTPUT TASK_ID X Y LOGICAL_WIDTH LOGICAL_HEIGHT\n");
        return 2;
    }
    output_name = argv[1]; task_id = argv[2];
    if (argc == 8) {
        if (!g_utf8_validate(argv[7], -1, NULL) || g_utf8_strlen(argv[7], -1) > 120 || !*argv[7]) return 2;
        activity = argv[7];
    }
    int *values[] = {&expected.x, &expected.y, &expected.width, &expected.height};
    for (unsigned i = 0; i < G_N_ELEMENTS(values); i++) {
        char *end = NULL; errno = 0;
        long value = strtol(argv[i + 3], &end, 10);
        if (errno || !end || *end || value < -100000 || value > 100000) return 2;
        *values[i] = (int)value;
    }
    if (expected.width < 320 || expected.height < 200) return 2;
    /* Never fall back to X11 or let the compositor select an output. */
    g_setenv("GDK_BACKEND", "wayland", TRUE);
    if (!gtk_init_check(NULL, NULL) || !gtk_layer_is_supported()) {
        fprintf(stderr, "Wayland layer-shell unavailable\n"); return 3;
    }
    GdkDisplay *display = gdk_display_get_default();
    for (int i = 0; i < gdk_display_get_n_monitors(display); i++) {
        GdkMonitor *candidate = gdk_display_get_monitor(display, i);
        GdkRectangle geometry; gdk_monitor_get_geometry(candidate, &geometry);
        if (monitor_geometry_matches(geometry.x, geometry.y, geometry.width, geometry.height,
                                     expected.x, expected.y, expected.width, expected.height)) {
            if (target) { fprintf(stderr, "Ambiguous monitor geometry\n"); return 4; }
            target = candidate;
            initial_gdk_geometry = geometry;
        }
    }
    if (!target) { fprintf(stderr, "No monitor matches selected output geometry\n"); return 4; }
    g_object_ref(target);
    g_signal_connect(display, "monitor-removed", G_CALLBACK(monitor_removed), NULL);
    GtkCssProvider *css = gtk_css_provider_new();
    const char *style =
        "#computer-use-edge { background: #2788ff; min-width: 0; min-height: 0; padding: 0; margin: 0; border: none; }"
        "#computer-use-label { background: #16243a; color: white; border: 1px solid #4698ff; border-radius: 16px; padding: 10px 18px; margin: 0; }"
        "#computer-use-label label { color: white; font: 500 14px sans-serif; }"
        "#abort-shortcut { color: #b8d8ff; font: 600 12px sans-serif; }";
    GError *error = NULL;
    if (!gtk_css_provider_load_from_data(css, style, -1, &error)) {
        fprintf(stderr, "Indicator style invalid\n"); return 5;
    }
    gtk_style_context_add_provider_for_screen(gdk_screen_get_default(), GTK_STYLE_PROVIDER(css),
                                             GTK_STYLE_PROVIDER_PRIORITY_APPLICATION);
    surfaces[0] = surface(4, -1, TRUE, FALSE, TRUE, TRUE, FALSE);
    surfaces[1] = surface(4, -1, FALSE, TRUE, TRUE, TRUE, FALSE);
    surfaces[2] = surface(-1, 4, TRUE, TRUE, TRUE, FALSE, FALSE);
    surfaces[3] = surface(-1, 4, TRUE, TRUE, FALSE, TRUE, FALSE);
    surfaces[4] = surface(-1, -1, FALSE, FALSE, TRUE, FALSE, TRUE);
    last_heartbeat = g_get_monotonic_time();
    if (fcntl(STDIN_FILENO, F_SETFL, fcntl(STDIN_FILENO, F_GETFL) | O_NONBLOCK) < 0) return 6;
    g_unix_fd_add(STDIN_FILENO, G_IO_IN | G_IO_HUP | G_IO_ERR | G_IO_NVAL, input_ready, NULL);
    g_timeout_add(100, timer, NULL);
    g_unix_signal_add(SIGTERM, interrupted, NULL);
    g_unix_signal_add(SIGINT, interrupted, NULL);
    gtk_main();
    for (unsigned i = 0; i < G_N_ELEMENTS(surfaces); i++) gtk_widget_destroy(surfaces[i]);
    g_object_unref(css); g_object_unref(target);
    return 0;
}
