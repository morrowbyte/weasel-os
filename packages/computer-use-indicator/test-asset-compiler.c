/* SPDX-License-Identifier: MIT
 * Offscreen source-selection/alpha test; no display server or desktop input. */
#define main asset_compiler_main
#include "asset-compiler.c"
#undef main

int main(void) {
    GdkPixbuf *base = gdk_pixbuf_new(GDK_COLORSPACE_RGB, TRUE, 8, 256, 512);
    GdkPixbuf *strip = gdk_pixbuf_new(GDK_COLORSPACE_RGB, TRUE, 8, 256, 128);
    GdkPixbuf *atlas = gdk_pixbuf_new(GDK_COLORSPACE_RGB, TRUE, 8, 1024, 2048);
    gdk_pixbuf_fill(base, 0xff000004); /* Invisible RGB must not become a halo. */
    gdk_pixbuf_fill(strip, 0xff000004);
    gdk_pixbuf_fill(atlas, 0);
    for (int input = 0; input < 2; ++input) {
        GdkPixbuf *image = input ? strip : base;
        guchar *pixels = gdk_pixbuf_get_pixels(image);
        int stride = gdk_pixbuf_get_rowstride(image);
        for (int row = 0; row < (input ? 2 : 8); ++row)
            for (int frame = 0; frame < 4; ++frame)
                for (int y = 10; y < 60; ++y)
                    for (int x = 24; x < 40; ++x) {
                        guchar *p = pixels + (row * 64 + y) * stride + (frame * 64 + x) * 4;
                        p[0] = input ? 200 + row * 20 : 20 + row * 20;
                        p[1] = frame * 30; p[2] = 100; p[3] = 255;
                    }
    }
    for (int row = 0; row < 8; ++row)
        for (int frame = 0; frame < 4; ++frame) {
            gboolean replacement = row == 5 || row == 6;
            g_assert_true(normalize_cell(replacement ? strip : base, replacement ? 2 : 8,
                                         replacement ? row - 5 : row, frame,
                                         replacement ? .94 : 1, atlas, row));
            int stride = gdk_pixbuf_get_rowstride(atlas);
            const guchar *pixels = gdk_pixbuf_read_pixels(atlas);
            const guchar *body = pixels + (row * 256 + 160) * stride + (frame * 256 + 128) * 4;
            const int expected[] = {20, 40, 60, 80, 100, 200, 220, 160};
            g_assert_cmpint(body[0], ==, expected[row]);
            g_assert_cmpint(body[1], ==, frame * 30);
            g_assert_cmpint(body[3], ==, 255);
            const guchar *outside = pixels + (row * 256 + 20) * stride + frame * 256 * 4;
            for (int c = 0; c < 4; ++c) g_assert_cmpint(outside[c], ==, 0);
            const guchar *below_feet = pixels + (row * 256 + 250) * stride + (frame * 256 + 128) * 4;
            g_assert_cmpint(below_feet[3], ==, 0);
        }
    g_object_unref(base); g_object_unref(strip); g_object_unref(atlas);
    puts("PASS: six original rows, exact question/answer rows, frame identity, alpha floor and foot registration");
    return 0;
}
