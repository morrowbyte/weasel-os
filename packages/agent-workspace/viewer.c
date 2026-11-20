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

static gboolean position_before_button(GtkWidget *widget, GdkEvent *event, gpointer data)
{
    View *view = data;
    if (event->type != GDK_BUTTON_PRESS && event->type != GDK_BUTTON_RELEASE &&
        event->type != GDK_SCROLL)
        return FALSE;
    if (!view->initialized || vnc_display_get_read_only(view->display) ||
        !vnc_display_is_pointer_absolute(view->display))
        return FALSE;

    /* GTK-VNC 1.5 sends button and wheel packets at the last motion position.
     * Wayland can deliver enter and press without a motion in between. Feed
     * the real event position through the widget's normal absolute transform
     * before its button handler runs, retaining its current button mask. */
    GdkEventMotion position = { .type = GDK_MOTION_NOTIFY, .window = event->any.window };
    if (!gdk_event_get_coords(event, &position.x, &position.y))
        return FALSE;
    GdkWindow *window = gtk_widget_get_window(widget);
    const int width = gdk_window_get_width(window);
    const int height = gdk_window_get_height(window);
    const int fb_width = vnc_display_get_width(view->display);
    const int fb_height = vnc_display_get_height(view->display);
    if (width < 1 || height < 1 || fb_width < 1 || fb_height < 1)
        return TRUE;
    /* An implicit grab can deliver a drag release outside the image. Cap its
     * coordinates within the final framebuffer pixel before caching them. */
    position.x = CLAMP(position.x, 0.0, width - 0.5 * width / fb_width);
    position.y = CLAMP(position.y, 0.0, height - 0.5 * height / fb_height);
    GTK_WIDGET_GET_CLASS(widget)->motion_notify_event(widget, &position);
    return FALSE;
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
    const int output_width = 1280, output_height = 720;
    gtk_window_set_default_size(view.window, output_width, output_height);
    /* Keep the letterbox outside the input widget. GtkVnc then draws and
     * translates pointer coordinates in the same framebuffer-shaped area. */
    GtkWidget *frame = gtk_aspect_frame_new(NULL, 0.5f, 0.5f,
        (gfloat)output_width / output_height, FALSE);
    gtk_frame_set_shadow_type(GTK_FRAME(frame), GTK_SHADOW_NONE);
    title(&view, "Verbinden");
    vnc_display_set_read_only(view.display, TRUE);
    vnc_display_set_pointer_grab(view.display, FALSE);
    vnc_display_set_keyboard_grab(view.display, FALSE);
    vnc_display_set_force_size(view.display, FALSE);
    vnc_display_set_scaling(view.display, TRUE);
    vnc_display_set_allow_resize(view.display, FALSE);
    gtk_container_add(GTK_CONTAINER(frame), GTK_WIDGET(view.display));
    gtk_container_add(GTK_CONTAINER(view.window), frame);

    /* GtkVnc only emits server-cut-text. There is deliberately no clipboard
     * handler in either direction, and no host input injection backend. */
    g_signal_connect_swapped(view.window, "notify::is-active", G_CALLBACK(update_focus), &view);
    g_signal_connect_swapped(view.window, "destroy", G_CALLBACK(gtk_main_quit), NULL);
    g_signal_connect_swapped(view.display, "vnc-initialized", G_CALLBACK(initialized), &view);
    g_signal_connect_swapped(view.display, "vnc-disconnected", G_CALLBACK(gtk_main_quit), NULL);
    g_signal_connect(view.display, "vnc-error", G_CALLBACK(failed), &view);
    g_signal_connect(view.display, "event", G_CALLBACK(position_before_button), &view);
    if (!vnc_display_open_fd(view.display, stream))
        return 1;
    gtk_widget_show_all(GTK_WIDGET(view.window));
    g_timeout_add(250, retry_busy_view, &view);
    gtk_main();
    return view.initialized ? view.status : 1;
}
