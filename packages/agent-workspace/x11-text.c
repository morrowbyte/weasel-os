/* Type through the private X server's current keymap. The Sway broker supplies
 * the managed XID. X clients can focus a child of their managed window. */
#include <limits.h>
#include <locale.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <xdo.h>

static volatile sig_atomic_t cancelled;

static void cancel(int number) {
    (void)number;
    cancelled = 1;
}

int main(int argc, char **argv) {
    if (argc != 2 || !setlocale(LC_CTYPE, "C.UTF-8")) return 2;
    char *end;
    Window expected = strtoul(argv[1], &end, 10);
    if (!expected || *end) return 2;
    struct sigaction action = {.sa_handler = cancel};
    sigemptyset(&action.sa_mask);
    if (sigaction(SIGTERM, &action, NULL) < 0) return 1;

    xdo_t *xdo = xdo_new(NULL);
    if (!xdo) return 1;
    int status = 0;
    wint_t symbol;
    while (!cancelled && (symbol = fgetwc(stdin)) != WEOF) {
        Window focus, client;
        if (xdo_get_focused_window(xdo, &focus) || focus == None || focus == PointerRoot ||
            (focus != expected && (xdo_find_window_client(xdo, focus, &client, XDO_FIND_PARENTS) ||
                                   client != expected))) {
            fputs("Private X input focus left the requested application\n", stderr);
            status = 1;
            break;
        }
        if (cancelled) break;
        const char *special = symbol == '\n' || symbol == '\r' ? "Return" :
                              symbol == '\t' ? "Tab" : symbol == 27 ? "Escape" : NULL;
        if (special) {
            status = xdo_send_keysequence_window(xdo, focus, special, 0);
        } else {
            mbstate_t state = {0};
            char bytes[MB_LEN_MAX + 1];
            size_t length = wcrtomb(bytes, symbol, &state);
            if (length == (size_t)-1) { status = 1; break; }
            bytes[length] = '\0';
            /* libxdo reuses the server map, borrowing an unused X keycode
             * for an absent keysym. Leave time for the X client to read that
             * mapping; ASCII on the private US map needs no mapping change. */
            status = xdo_enter_text_window(xdo, focus, bytes, symbol < 128 ? 0 : 12000);
        }
        if (status) break;
    }
    if (ferror(stdin) || cancelled) status = 1;
    xdo_free(xdo);
    return status;
}
