/* Normalize the bundled generated 4x8 atlas to the integer-cell pack ABI.
 * This is a build tool, never a screen reader or network client. */
#include <gdk-pixbuf/gdk-pixbuf.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

/* Generated background noise is alpha 1..15. Remove that floor before
 * interpolation; preserve graded fur edges and translucent foreground pixels.
 * Each cell is sampled independently in premultiplied alpha, so neither noise
 * RGB nor a neighbouring animation frame can bleed into a visible edge. */
static double clean_alpha(unsigned value) {
    return value <= 16 ? 0 : (value - 16) / 239.0;
}

static GdkPixbuf *load_rgba(const char *path, int rows) {
    int width = 0, height = 0;
    if (!gdk_pixbuf_get_file_info(path, &width, &height) || width < 256 || width > 2048 ||
        height < 128 || height > 4096 || height * 4 != width * rows) return NULL;
    GError *error = NULL;
    GdkPixbuf *image = gdk_pixbuf_new_from_file(path, &error);
    g_clear_error(&error);
    if (image && (!gdk_pixbuf_get_has_alpha(image) || gdk_pixbuf_get_n_channels(image) != 4 ||
                  gdk_pixbuf_get_bits_per_sample(image) != 8)) g_clear_object(&image);
    return image;
}

static gboolean normalize_cell(GdkPixbuf *source, int rows, int row, int column,
                               double relative_scale, GdkPixbuf *atlas, int target_row) {
    int width = gdk_pixbuf_get_width(source), height = gdk_pixbuf_get_height(source);
    int left = column * width / 4, right = (column + 1) * width / 4;
    int top = row * height / rows, bottom = (row + 1) * height / rows;
    int stride = gdk_pixbuf_get_rowstride(source), floor_y = -1;
    const guchar *pixels = gdk_pixbuf_read_pixels(source);
    for (int y = top; y < bottom; ++y)
        for (int x = left; x < right; ++x)
            if (pixels[y * stride + x * 4 + 3] >= 64) floor_y = y;
    if (floor_y < 0) return FALSE;
    double scale = 1024.0 / width * relative_scale;
    int foot_band = MAX(1, (int)ceil(6.0 / scale));
    double foot_weight = 0, foot_x = 0;
    for (int y = MAX(top, floor_y - foot_band + 1); y <= floor_y; ++y) {
        for (int x = left; x < right; ++x) {
            unsigned alpha = pixels[y * stride + x * 4 + 3];
            if (alpha < 64) continue;
            foot_weight += alpha;
            foot_x += (x - left + .5) * alpha;
        }
    }
    if (!foot_weight) return FALSE;
    double translate_x = 128.0 - foot_x / foot_weight * scale;
    double translate_y = 248.0 - (floor_y - top + 1) * scale;
    guchar *out = gdk_pixbuf_get_pixels(atlas);
    int out_stride = gdk_pixbuf_get_rowstride(atlas);
    for (int y = 0; y < 256; ++y) {
        for (int x = 0; x < 256; ++x) {
            double sx = (x + .5 - translate_x) / scale - .5;
            double sy = (y + .5 - translate_y) / scale - .5;
            int ix = (int)floor(sx), iy = (int)floor(sy);
            double fx = sx - ix, fy = sy - iy, alpha = 0, rgb[3] = {0};
            for (int dy = 0; dy < 2; ++dy) {
                for (int dx = 0; dx < 2; ++dx) {
                    int px = left + ix + dx, py = top + iy + dy;
                    if (px < left || px >= right || py < top || py >= bottom) continue;
                    const guchar *sample = pixels + py * stride + px * 4;
                    double weight = (dx ? fx : 1 - fx) * (dy ? fy : 1 - fy);
                    double a = clean_alpha(sample[3]) * weight;
                    alpha += a;
                    for (int channel = 0; channel < 3; ++channel) rgb[channel] += sample[channel] * a;
                }
            }
            guchar *target = out + (target_row * 256 + y) * out_stride + (column * 256 + x) * 4;
            target[3] = (guchar)CLAMP(lround(alpha * 255), 0, 255);
            for (int channel = 0; channel < 3; ++channel)
                target[channel] = target[3] ? (guchar)CLAMP(lround(rgb[channel] / alpha), 0, 255) : 0;
        }
    }
    printf("{\"targetRow\":%d,\"frame\":%d,\"sourceRow\":%d,\"scale\":%.8f,"
           "\"footX\":%.4f,\"footY\":%d,\"targetFootY\":248}\n",
           target_row, column, row, scale, foot_x / foot_weight, floor_y - top + 1);
    return TRUE;
}

static int compile_sudo(const char *base_path, const char *strip_path, const char *output) {
    GdkPixbuf *base = load_rgba(base_path, 8), *strip = load_rgba(strip_path, 2);
    if (!base || !strip) { g_clear_object(&base); g_clear_object(&strip); return 3; }
    GdkPixbuf *atlas = gdk_pixbuf_new(GDK_COLORSPACE_RGB, TRUE, 8, 1024, 2048);
    gdk_pixbuf_fill(atlas, 0);
    gboolean valid = TRUE;
    for (int row = 0; row < 8; ++row)
        for (int column = 0; column < 4; ++column) {
            gboolean replacement = row == 5 || row == 6;
            /* The strip's standing figure is about 6% taller within its cell.
             * One shared scale preserves gestures; only feet are registered.
             * Other six rows always come from the original 32-frame source. */
            valid &= normalize_cell(replacement ? strip : base, replacement ? 2 : 8,
                                    replacement ? row - 5 : row, column,
                                    replacement ? .94 : 1.0, atlas, row);
        }
    GError *error = NULL;
    gboolean saved = valid && gdk_pixbuf_save(atlas, output, "png", &error, NULL);
    g_clear_error(&error); g_object_unref(atlas); g_object_unref(base); g_object_unref(strip);
    return saved ? 0 : 5;
}

static int proof_atlas(const char *path, const char *output) {
    GdkPixbuf *atlas = load_rgba(path, 8);
    if (!atlas || gdk_pixbuf_get_width(atlas) != 1024) { g_clear_object(&atlas); return 3; }
    GdkPixbuf *proof = gdk_pixbuf_new(GDK_COLORSPACE_RGB, FALSE, 8, 1152, 1152);
    gdk_pixbuf_fill(proof, 0xf7f4efff);
    guchar *pixels = gdk_pixbuf_get_pixels(proof);
    int stride = gdk_pixbuf_get_rowstride(proof);
    for (int y = 0; y < 1152; ++y)
        for (int x = 576; x < 1152; ++x) {
            guchar *p = pixels + y * stride + x * 3; p[0] = 29; p[1] = 32; p[2] = 38;
        }
    for (int row = 0; row < 8; ++row)
        for (int frame = 0; frame < 4; ++frame) {
            GdkPixbuf *cell = gdk_pixbuf_new_subpixbuf(atlas, frame * 256, row * 256, 256, 256);
            for (int theme = 0; theme < 2; ++theme) {
                int x = (theme * 4 + frame) * 144, y = row * 144;
                gdk_pixbuf_composite(cell, proof, x, y, 144, 144, x, y,
                                     144.0 / 256, 144.0 / 256, GDK_INTERP_BILINEAR, 255);
            }
            g_object_unref(cell);
        }
    GError *error = NULL;
    gboolean saved = gdk_pixbuf_save(proof, output, "png", &error, NULL);
    g_clear_error(&error); g_object_unref(proof); g_object_unref(atlas);
    return saved ? 0 : 5;
}

static int inspect_atlas(const char *path) {
    int width = 0, height = 0;
    GdkPixbufFormat *format = gdk_pixbuf_get_file_info(path, &width, &height);
    int rows = height == 2 * width ? 8 : width == 2 * height ? 2 : 0;
    if (!format || width < 256 || height < 128 || width > 2048 || height > 4096 || !rows) return 3;
    GError *error = NULL;
    GdkPixbuf *source = gdk_pixbuf_new_from_file(path, &error);
    if (!source || !gdk_pixbuf_get_has_alpha(source) || gdk_pixbuf_get_n_channels(source) != 4 ||
        gdk_pixbuf_get_bits_per_sample(source) != 8) {
        g_clear_object(&source); g_clear_error(&error); return 4;
    }
    const guchar *pixels = gdk_pixbuf_read_pixels(source);
    int stride = gdk_pixbuf_get_rowstride(source);
    unsigned long histogram[256] = {0};
    for (int y = 0; y < height; ++y)
        for (int x = 0; x < width; ++x) ++histogram[pixels[y * stride + x * 4 + 3]];
    printf("{\"width\":%d,\"height\":%d,\"alphaHistogram\":[", width, height);
    for (unsigned i = 0; i < 256; ++i) printf("%s%lu", i ? "," : "", histogram[i]);
    printf("],\"coreThreshold\":32,\"cells\":[");
    for (int row = 0; row < rows; ++row) {
        for (int column = 0; column < 4; ++column) {
            int left = column * width / 4, right = (column + 1) * width / 4;
            int top = row * height / rows, bottom = (row + 1) * height / rows;
            int min_x = right - left, min_y = bottom - top, max_x = -1, max_y = -1;
            unsigned long count = 0;
            for (int y = top; y < bottom; ++y) {
                for (int x = left; x < right; ++x) {
                    if (pixels[y * stride + x * 4 + 3] < 32) continue;
                    if (x - left < min_x) min_x = x - left;
                    if (x - left > max_x) max_x = x - left;
                    if (y - top < min_y) min_y = y - top;
                    if (y - top > max_y) max_y = y - top;
                    ++count;
                }
            }
            printf("%s{\"row\":%d,\"frame\":%d,\"cellWidth\":%d,\"cellHeight\":%d,"
                   "\"coreBox\":[%d,%d,%d,%d],\"corePixels\":%lu}", row || column ? "," : "",
                   row, column, right - left, bottom - top, min_x, min_y, max_x, max_y, count);
        }
    }
    puts("]}");
    g_object_unref(source);
    return 0;
}

int main(int argc, char **argv) {
    if (argc == 5 && !strcmp(argv[1], "--sudo")) return compile_sudo(argv[2], argv[3], argv[4]);
    if (argc == 4 && !strcmp(argv[1], "--proof")) return proof_atlas(argv[2], argv[3]);
    if (argc != 3) return 2;
    if (!strcmp(argv[1], "--inspect")) return inspect_atlas(argv[2]);
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
