/* SPDX-License-Identifier: MIT
 * Pure display-text regression; no GTK session, desktop input or capture. */
#define main pet_renderer_main
#include "pet-renderer.c"
#undef main

int main(void) {
    const struct { const char *original, *visible; } cases[] = {
        { "Thread 1 · Question: Welche Datei?", "Welche Datei?" },
        { "Thread 42 · Result: Die Notiz ist gespeichert.", "Die Notiz ist gespeichert." },
        { "Thread 9 · Result: Thread 2 · Question: bleibt Inhalt", "Thread 2 · Question: bleibt Inhalt" },
        { "Thread 4 · Question: ", "Thread 4 · Question: " },
        { "Thread 4 · Result:", "Thread 4 · Result:" },
        { "Thread 0 · Question: Frage", "Thread 0 · Question: Frage" },
        { "Thread 01 · Result: Text", "Thread 01 · Result: Text" },
        { "Thread x · Question: Frage", "Thread x · Question: Frage" },
        { "Thread 4 · question: Frage", "Thread 4 · question: Frage" },
        { "Thread 4 - Result: Text", "Thread 4 - Result: Text" },
        { " Thread 4 · Result: Text", " Thread 4 · Result: Text" },
        { "Thread 4 · Approval: Bitte prüfen", "Thread 4 · Approval: Bitte prüfen" },
        { "Die Datei enthält Result: und bleibt unverändert.", "Die Datei enthält Result: und bleibt unverändert." },
        { "", "" },
    };
    for (unsigned i = 0; i < G_N_ELEMENTS(cases); ++i) {
        char *original = g_strdup(cases[i].original);
        g_assert_cmpstr(visible_summary(original), ==, cases[i].visible);
        g_assert_cmpstr(original, ==, cases[i].original);
        g_free(original);
    }
    g_assert_cmpstr(visible_summary(NULL), ==, "");
    puts("PASS: 15 presentation cases; original summaries preserved");
    return 0;
}
