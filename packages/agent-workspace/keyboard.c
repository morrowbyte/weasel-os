/* Private Wayland seat initialization and single keyboard chords. */
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <wayland-client.h>
#include <xkbcommon/xkbcommon.h>
#include "keyboard-protocol.h"
#include "pointer-protocol.h"

static struct wl_seat *seat;
static unsigned seat_count;
static struct zwp_virtual_keyboard_manager_v1 *manager;
static struct zwlr_virtual_pointer_manager_v1 *pointer_manager;

static void fail(const char *reason) {
    fprintf(stderr, "%s\n", reason);
    exit(1);
}

static void global(void *data, struct wl_registry *registry, uint32_t name,
                   const char *interface, uint32_t version) {
    const bool hold_seat = *(const bool *)data;
    (void)version;
    if (!strcmp(interface, wl_seat_interface.name)) {
        if (++seat_count == 1)
            seat = wl_registry_bind(registry, name, &wl_seat_interface, 1);
    } else if (!strcmp(interface, zwp_virtual_keyboard_manager_v1_interface.name)) {
        manager = wl_registry_bind(registry, name, &zwp_virtual_keyboard_manager_v1_interface, 1);
    } else if (hold_seat && !strcmp(interface, zwlr_virtual_pointer_manager_v1_interface.name)) {
        pointer_manager = wl_registry_bind(registry, name, &zwlr_virtual_pointer_manager_v1_interface, 1);
    }
}

static void removed(void *data, struct wl_registry *registry, uint32_t name) {
    (void)data;
    (void)registry;
    (void)name;
}

/* Keep the physical layout intact for accelerators and terminal key handling.
 * For a named keysym absent from this layout, map just the spare F24 key. */
static struct xkb_keymap *make_map(struct xkb_context *context, xkb_keysym_t extra) {
    char override[96] = "";
    if (extra != XKB_KEY_NoSymbol)
        snprintf(override, sizeof(override),
                 "replace key <FK24> { type=\"ONE_LEVEL\", [ 0x%08x ] };", extra);
    char source[512];
    snprintf(source, sizeof(source),
             "xkb_keymap {"
             "xkb_keycodes { include \"evdev+aliases(qwerty)\" };"
             "xkb_types { include \"complete\" };"
             "xkb_compatibility { include \"complete\" };"
             "xkb_symbols { include \"pc+us+inet(evdev)+level3(ralt_switch)\" %s };"
             "};", override);
    struct xkb_keymap *map = xkb_keymap_new_from_string(context, source,
        XKB_KEYMAP_FORMAT_TEXT_V1, XKB_KEYMAP_COMPILE_NO_FLAGS);
    if (!map) fail("Cannot compile private XKB keymap");
    return map;
}

static xkb_keycode_t find_symbol(struct xkb_keymap *map, xkb_keysym_t symbol,
                                 bool *needs_shift) {
    struct xkb_state *state = xkb_state_new(map);
    if (!state) fail("Cannot create private XKB state");
    xkb_keycode_t code = XKB_KEYCODE_INVALID;
    for (int shift = 0; shift <= 1 && code == XKB_KEYCODE_INVALID; ++shift) {
        if (shift)
            xkb_state_update_key(state, xkb_keymap_key_by_name(map, "LFSH"), XKB_KEY_DOWN);
        for (xkb_keycode_t k = xkb_keymap_min_keycode(map); k <= xkb_keymap_max_keycode(map); ++k) {
            if (xkb_state_key_get_one_sym(state, k) == symbol) {
                code = k;
                *needs_shift = shift;
                break;
            }
        }
    }
    xkb_state_unref(state);
    return code;
}

static const struct {
    const char *name;
    const char *physical;
} modifiers[] = {
    {"ctrl", "LCTL"}, {"shift", "LFSH"}, {"alt", "LALT"},
    {"logo", "LWIN"}, {"altgr", "RALT"},
};

static void send_key(struct zwp_virtual_keyboard_v1 *keyboard, struct xkb_state *state,
                     xkb_keycode_t code, bool down) {
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    uint32_t ms = (uint32_t)((uint64_t)now.tv_sec * 1000 + now.tv_nsec / 1000000);
    zwp_virtual_keyboard_v1_key(keyboard, ms, code - 8,
        down ? WL_KEYBOARD_KEY_STATE_PRESSED : WL_KEYBOARD_KEY_STATE_RELEASED);
    /* Virtual keyboard key events do not update wlroots' XKB state. */
    if (xkb_state_update_key(state, code, down ? XKB_KEY_DOWN : XKB_KEY_UP)) {
        zwp_virtual_keyboard_v1_modifiers(keyboard,
            xkb_state_serialize_mods(state, XKB_STATE_MODS_DEPRESSED),
            xkb_state_serialize_mods(state, XKB_STATE_MODS_LATCHED),
            xkb_state_serialize_mods(state, XKB_STATE_MODS_LOCKED),
            xkb_state_serialize_layout(state, XKB_STATE_LAYOUT_EFFECTIVE));
    }
}

static size_t chord_keys(int argc, char **argv, struct xkb_context *context,
                         struct xkb_keymap **keymap, xkb_keycode_t keys[6]) {
    xkb_keysym_t symbol = xkb_keysym_from_name(argv[1], XKB_KEYSYM_CASE_INSENSITIVE);
    if (symbol == XKB_KEY_NoSymbol) fail("Unknown XKB keysym");
    struct xkb_keymap *map = *keymap;
    bool needs_shift = false;
    xkb_keycode_t key = find_symbol(map, symbol, &needs_shift);
    if (key == XKB_KEYCODE_INVALID) {
        xkb_keymap_unref(map);
        map = make_map(context, symbol);
        *keymap = map;
        key = xkb_keymap_key_by_name(map, "FK24");
    }
    if (key == XKB_KEYCODE_INVALID) fail("No keycode for requested symbol");

    size_t count = 0;
    for (size_t i = 0; i < sizeof(modifiers) / sizeof(modifiers[0]); ++i) {
        bool held = needs_shift && !strcmp(modifiers[i].name, "shift");
        for (int arg = 2; arg < argc; ++arg)
            if (!strcmp(argv[arg], modifiers[i].name)) held = true;
        if (held) {
            xkb_keycode_t code = xkb_keymap_key_by_name(map, modifiers[i].physical);
            if (code == XKB_KEYCODE_INVALID) fail("Missing modifier in private XKB map");
            if (code != key) keys[count++] = code;
        }
    }
    keys[count++] = key;
    return count;
}

static void seat_capabilities(void *data, struct wl_seat *wl_seat, uint32_t caps) {
    (void)wl_seat;
    *(uint32_t *)data = caps;
}

int main(int argc, char **argv) {
    if (argc < 2 || argc > 7) return 2;
    bool hold_seat = argc == 2 && !strcmp(argv[1], "--hold-seat");
    struct xkb_context *context = xkb_context_new(XKB_CONTEXT_NO_FLAGS);
    if (!context) fail("Cannot create private XKB context");
    struct xkb_keymap *map = make_map(context, XKB_KEY_NoSymbol);
    xkb_keycode_t keys[6];
    size_t count = hold_seat ? 0 : chord_keys(argc, argv, context, &map, keys);
    char *source = xkb_keymap_get_as_string(map, XKB_KEYMAP_FORMAT_TEXT_V1);
    if (!source) fail("Cannot serialize private XKB keymap");
    size_t size = strlen(source) + 1;
    FILE *file = tmpfile();
    if (!file || fwrite(source, 1, size, file) != size || fflush(file) != 0)
        fail("Cannot write private XKB keymap");
    free(source);

    struct wl_display *display = wl_display_connect(NULL);
    if (!display) fail("Cannot connect to private Wayland display");
    struct wl_registry *registry = wl_display_get_registry(display);
    const struct wl_registry_listener listener = {global, removed};
    wl_registry_add_listener(registry, &listener, &hold_seat);
    if (wl_display_roundtrip(display) < 0 || seat_count != 1 || !manager)
        fail("Expected a single private seat with virtual-keyboard support");
    uint32_t caps = 0;
    const struct wl_seat_listener seat_listener = {.capabilities = seat_capabilities};
    if (hold_seat) {
        if (!pointer_manager) fail("Private compositor lacks virtual-pointer support");
        wl_seat_add_listener(seat, &seat_listener, &caps);
        if (!zwlr_virtual_pointer_manager_v1_create_virtual_pointer(pointer_manager, seat))
            fail("Cannot create private pointer");
    }
    struct zwp_virtual_keyboard_v1 *keyboard =
        zwp_virtual_keyboard_manager_v1_create_virtual_keyboard(manager, seat);
    zwp_virtual_keyboard_v1_keymap(keyboard, WL_KEYBOARD_KEYMAP_FORMAT_XKB_V1, fileno(file), size);
    if (wl_display_roundtrip(display) < 0) fail("Private compositor rejected keyboard setup");
    fclose(file);

    if (hold_seat) {
        const uint32_t required = WL_SEAT_CAPABILITY_POINTER | WL_SEAT_CAPABILITY_KEYBOARD;
        if ((caps & required) != required) fail("Private seat did not announce keyboard and pointer");
        puts("ready");
        if (fflush(stdout) != 0) fail("Cannot report private seat readiness");
        while (wl_display_dispatch(display) >= 0) {
            if ((caps & required) != required) fail("Private seat lost keyboard or pointer");
        }
        fail("Private seat disconnected");
    }

    struct xkb_state *state = xkb_state_new(map);
    if (!state) fail("Cannot create private XKB state");
    for (size_t i = 0; i < count; ++i) send_key(keyboard, state, keys[i], true);
    while (count) send_key(keyboard, state, keys[--count], false);
    if (wl_display_roundtrip(display) < 0) fail("Private compositor lost during chord");
    zwp_virtual_keyboard_v1_destroy(keyboard);
    if (wl_display_flush(display) < 0) fail("Cannot release private virtual keyboard");
    wl_display_disconnect(display);
    xkb_state_unref(state);
    xkb_keymap_unref(map);
    xkb_context_unref(context);
    return 0;
}
