/* One pointer on the single private output. Coordinates are validated by the broker. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <linux/input-event-codes.h>
#include <wayland-client.h>
#include "pointer-protocol.h"

static struct zwlr_virtual_pointer_manager_v1 *manager;

static void global(void *data, struct wl_registry *registry, uint32_t name,
                   const char *interface, uint32_t version) {
    (void)data;
    (void)version;
    if (strcmp(interface, zwlr_virtual_pointer_manager_v1_interface.name) == 0)
        manager = wl_registry_bind(registry, name, &zwlr_virtual_pointer_manager_v1_interface, 1);
}

static void removed(void *data, struct wl_registry *registry, uint32_t name) {
    (void)data;
    (void)registry;
    (void)name;
}

int main(int argc, char **argv) {
    if (argc != 5 && argc != 6) return 2;
    struct wl_display *display = wl_display_connect(NULL);
    if (!display) return 1;
    struct wl_registry *registry = wl_display_get_registry(display);
    const struct wl_registry_listener listener = {global, removed};
    wl_registry_add_listener(registry, &listener, NULL);
    if (wl_display_roundtrip(display) < 0 || !manager) {
        fputs("Private compositor lacks virtual-pointer support\n", stderr);
        return 1;
    }
    struct zwlr_virtual_pointer_v1 *pointer =
        zwlr_virtual_pointer_manager_v1_create_virtual_pointer(manager, NULL);
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    uint32_t ms = (uint32_t)((uint64_t)now.tv_sec * 1000 + now.tv_nsec / 1000000);
    zwlr_virtual_pointer_v1_motion_absolute(pointer, ms, atoi(argv[2]), atoi(argv[3]), 1280, 720);
    zwlr_virtual_pointer_v1_frame(pointer);

    const char *action = argv[4];
    if (strcmp(argv[1], "click") == 0 && argc == 5) {
        uint32_t button;
        if (strcmp(action, "left") == 0) button = BTN_LEFT;
        else if (strcmp(action, "right") == 0) button = BTN_RIGHT;
        else if (strcmp(action, "middle") == 0) button = BTN_MIDDLE;
        else return 2;
        zwlr_virtual_pointer_v1_button(pointer, ms, button, WL_POINTER_BUTTON_STATE_PRESSED);
        zwlr_virtual_pointer_v1_frame(pointer);
        zwlr_virtual_pointer_v1_button(pointer, ms, button, WL_POINTER_BUTTON_STATE_RELEASED);
    } else if (strcmp(argv[1], "scroll") == 0 && argc == 6) {
        int steps = atoi(argv[5]);
        uint32_t axis;
        if (!strcmp(action, "up") || !strcmp(action, "down")) axis = WL_POINTER_AXIS_VERTICAL_SCROLL;
        else if (!strcmp(action, "left") || !strcmp(action, "right")) axis = WL_POINTER_AXIS_HORIZONTAL_SCROLL;
        else return 2;
        if (!strcmp(action, "up") || !strcmp(action, "left")) steps = -steps;
        zwlr_virtual_pointer_v1_axis_source(pointer, WL_POINTER_AXIS_SOURCE_WHEEL);
        zwlr_virtual_pointer_v1_axis_discrete(pointer, ms, axis, wl_fixed_from_int(steps * 15), steps);
    } else return 2;
    zwlr_virtual_pointer_v1_frame(pointer);
    int result = wl_display_roundtrip(display);
    zwlr_virtual_pointer_v1_destroy(pointer);
    wl_display_disconnect(display);
    return result < 0;
}
