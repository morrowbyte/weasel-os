/* One pointer on the single private output. Coordinates are validated by the broker. */
#include <stdint.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <linux/input-event-codes.h>
#include <wayland-client.h>
#include "pointer-protocol.h"

static struct zwlr_virtual_pointer_manager_v1 *manager;
static volatile sig_atomic_t cancelled;

static void cancel_drag(int signum) {
    (void)signum;
    cancelled = 1;
}

static uint32_t timestamp(void) {
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return (uint32_t)((uint64_t)now.tv_sec * 1000 + now.tv_nsec / 1000000);
}

static void move(struct zwlr_virtual_pointer_v1 *pointer, int x, int y) {
    zwlr_virtual_pointer_v1_motion_absolute(pointer, timestamp(), x, y, 1280, 720);
    zwlr_virtual_pointer_v1_frame(pointer);
}

static void set_button(struct zwlr_virtual_pointer_v1 *pointer, uint32_t button,
                       enum wl_pointer_button_state state) {
    zwlr_virtual_pointer_v1_button(pointer, timestamp(), button, state);
    zwlr_virtual_pointer_v1_frame(pointer);
}

static uint32_t button_code(const char *button) {
    if (!strcmp(button, "left")) return BTN_LEFT;
    if (!strcmp(button, "middle")) return BTN_MIDDLE;
    if (!strcmp(button, "right")) return BTN_RIGHT;
    return 0;
}

static int drag(struct wl_display *display, struct zwlr_virtual_pointer_v1 *pointer,
                int x, int y, int end_x, int end_y, uint32_t button) {
    int dx = end_x - x, dy = end_y - y;
    int distance = abs(dx) > abs(dy) ? abs(dx) : abs(dy);
    int steps = (distance + 15) / 16;
    if (wl_display_roundtrip(display) < 0 || cancelled) return 1;
    set_button(pointer, button, WL_POINTER_BUTTON_STATE_PRESSED);
    // Deliver separate motion frames so the source can start the gesture
    // and the destination can receive motion before the final button-up.
    for (int step = 1; step <= steps; ++step) {
        if (wl_display_roundtrip(display) < 0 || cancelled) break;
        nanosleep(&(struct timespec){.tv_nsec = 8000000}, NULL);
        if (cancelled) break;
        move(pointer, x + dx * step / steps, y + dy * step / steps);
    }
    int delivered = wl_display_roundtrip(display);
    set_button(pointer, button, WL_POINTER_BUTTON_STATE_RELEASED);
    return wl_display_roundtrip(display) < 0 || delivered < 0 || cancelled;
}

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
    int is_move = argc == 4 && !strcmp(argv[1], "move");
    int is_click = argc == 5 && !strcmp(argv[1], "click");
    int is_scroll = argc == 6 && !strcmp(argv[1], "scroll");
    int is_drag = argc == 7 && !strcmp(argv[1], "drag");
    if (!is_move && !is_click && !is_scroll && !is_drag) return 2;
    uint32_t button = 0;
    if (is_click || is_drag) {
        button = button_code(argv[argc - 1]);
        if (!button) return 2;
    }
    if (is_drag) {
        struct sigaction stop = {.sa_handler = cancel_drag};
        sigemptyset(&stop.sa_mask);
        if (sigaction(SIGTERM, &stop, NULL) < 0) return 1;
    }
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
    int x = atoi(argv[2]), y = atoi(argv[3]);
    int result = 0;
    if (!cancelled) move(pointer, x, y);
    if (is_drag) {
        result = drag(display, pointer, x, y, atoi(argv[4]), atoi(argv[5]), button);
    } else if (is_click) {
        set_button(pointer, button, WL_POINTER_BUTTON_STATE_PRESSED);
        set_button(pointer, button, WL_POINTER_BUTTON_STATE_RELEASED);
    } else if (is_scroll) {
        const char *action = argv[4];
        int steps = atoi(argv[5]);
        uint32_t axis;
        if (!strcmp(action, "up") || !strcmp(action, "down")) axis = WL_POINTER_AXIS_VERTICAL_SCROLL;
        else if (!strcmp(action, "left") || !strcmp(action, "right")) axis = WL_POINTER_AXIS_HORIZONTAL_SCROLL;
        else return 2;
        if (!strcmp(action, "up") || !strcmp(action, "left")) steps = -steps;
        zwlr_virtual_pointer_v1_axis_source(pointer, WL_POINTER_AXIS_SOURCE_WHEEL);
        zwlr_virtual_pointer_v1_axis_discrete(pointer, timestamp(), axis, wl_fixed_from_int(steps * 15), steps);
        zwlr_virtual_pointer_v1_frame(pointer);
    }
    if (!is_drag) result = wl_display_roundtrip(display) < 0;
    zwlr_virtual_pointer_v1_destroy(pointer);
    wl_display_disconnect(display);
    return result;
}
