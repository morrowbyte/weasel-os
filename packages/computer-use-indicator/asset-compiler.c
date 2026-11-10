/* Normalize the bundled generated 4x8 atlas to the integer-cell pack ABI.
 * This is a build tool, never a screen reader or network client. */
#include <gdk-pixbuf/gdk-pixbuf.h>
#include <stdio.h>

int main(int argc, char **argv) {
    if (argc != 3) return 2;
    int width = 0, height = 0;
    GdkPixbufFormat *format = gdk_pixbuf_get_file_info(argv[1], &width, &height);
    if (!format || width < 256 || height < 512 || width > 2048 || height > 4096 || height != 2 * width) return 3;
    GError *error = NULL;
    GdkPixbuf *source = gdk_pixbuf_new_from_file(argv[1], &error);
    if (!source || !gdk_pixbuf_get_has_alpha(source)) {
        g_clear_object(&source); g_clear_error(&error); return 4;
    }
    GdkPixbuf *atlas = gdk_pixbuf_scale_simple(source, 1024, 2048, GDK_INTERP_BILINEAR);
    gboolean saved = atlas && gdk_pixbuf_save(atlas, argv[2], "png", &error, NULL);
    g_clear_object(&source); g_clear_object(&atlas); g_clear_error(&error);
    return saved ? 0 : 5;
}
