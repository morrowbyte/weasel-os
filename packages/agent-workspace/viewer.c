#include <gtk/gtk.h>
#include <vncdisplay.h>
#include <fcntl.h>
#include <stdlib.h>
#include <sys/socket.h>
#include <sys/time.h>

typedef struct {
    GtkWindow *window;
    VncDisplay *display;
    const char *name;
    int control;
    gboolean initialized;
    int status;
} View;

static void title(View *view, const char *state)
{
    char *text = g_strdup_printf("%s · %s", view->name, state);
    gtk_window_set_title(view->window, text);
    g_free(text);
}

/* The broker serializes this grant with batch admission. Disable local input
 * before releasing it, including pointer events over an unfocused window. */
static void update_focus(View *view)
{
    if (!view->initialized)
        return;
    const gboolean focused = gtk_window_is_active(view->window);
    const char request = focused ? 'F' : 'B';
    char reply;
    vnc_display_set_read_only(view->display, TRUE);
    if (send(view->control, &request, 1, MSG_NOSIGNAL) != 1 ||
        recv(view->control, &reply, 1, 0) != 1 || (reply != 'F' && reply != 'B')) {
        view->status = 1;
        gtk_main_quit();
        return;
    }
    if (focused && reply == 'F') {
        vnc_display_set_read_only(view->display, FALSE);
        title(view, "Manuelle Eingabe");
    } else {
        title(view, focused ? "Agent arbeitet" : "Private Arbeitsfläche");
    }
}

static gboolean retry_busy_view(gpointer data)
{
    View *view = data;
    if (view->initialized && gtk_window_is_active(view->window) &&
        vnc_display_get_read_only(view->display))
        update_focus(view);
    return G_SOURCE_CONTINUE;
}

static void initialized(View *view)
{
    view->initialized = TRUE;
    gtk_widget_grab_focus(GTK_WIDGET(view->display));
    update_focus(view);
}

static void failed(VncDisplay *display, const char *message, View *view)
{
    (void)display;
    g_printerr("Private Arbeitsfläche: %s\n", message);
    view->status = 1;
}

int main(int argc, char **argv)
{
    if (argc != 4)
        return 2;
    View view = {.name = argv[1], .control = atoi(argv[2])};
    int stream = atoi(argv[3]);
    if (fcntl(view.control, F_SETFD, FD_CLOEXEC) < 0 ||
        fcntl(stream, F_SETFD, FD_CLOEXEC) < 0)
        return 1;
    struct timeval timeout = {.tv_sec = 2};
    if (setsockopt(view.control, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout)) < 0)
        return 1;

    g_set_prgname("weasel-workspace-view");
    gdk_set_allowed_backends("wayland");
    if (!gtk_init_check(NULL, NULL)) {
        g_printerr("Kein Wayland-Display für das angeforderte Fenster erreichbar.\n");
        return 1;
    }
    view.window = GTK_WINDOW(gtk_window_new(GTK_WINDOW_TOPLEVEL));
    view.display = VNC_DISPLAY(vnc_display_new());
    gtk_window_set_default_size(view.window, 960, 540);
    title(&view, "Verbinden");
    vnc_display_set_read_only(view.display, TRUE);
    vnc_display_set_pointer_grab(view.display, FALSE);
    vnc_display_set_keyboard_grab(view.display, FALSE);
    vnc_display_set_force_size(view.display, FALSE);
    vnc_display_set_scaling(view.display, TRUE);
    vnc_display_set_allow_resize(view.display, FALSE);
    gtk_container_add(GTK_CONTAINER(view.window), GTK_WIDGET(view.display));

    /* GtkVnc only emits server-cut-text. There is deliberately no clipboard
     * handler in either direction, and no host input injection backend. */
    g_signal_connect_swapped(view.window, "notify::is-active", G_CALLBACK(update_focus), &view);
    g_signal_connect_swapped(view.window, "destroy", G_CALLBACK(gtk_main_quit), NULL);
    g_signal_connect_swapped(view.display, "vnc-initialized", G_CALLBACK(initialized), &view);
    g_signal_connect_swapped(view.display, "vnc-disconnected", G_CALLBACK(gtk_main_quit), NULL);
    g_signal_connect(view.display, "vnc-error", G_CALLBACK(failed), &view);
    if (!vnc_display_open_fd(view.display, stream))
        return 1;
    gtk_widget_show_all(GTK_WIDGET(view.window));
    g_timeout_add(250, retry_busy_view, &view);
    gtk_main();
    return view.initialized ? view.status : 1;
}
