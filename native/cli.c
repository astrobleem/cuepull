#include "cuepull.h"
#include <stdio.h>
#include <string.h>
#include <time.h>

static int show_progress(void *unused, cp_u64 done, cp_u64 total,
                         const char *path)
{
    static time_t previous;
    time_t now;
    char a[32], b[32];
    (void)unused; now = time(NULL);
    if (now != previous || (total && done == total)) {
        previous = now; cp_decimal(done, a); cp_decimal(total, b);
        fprintf(stderr, "\r%s / %s bytes  %.100s\n", a, b, path);
    }
    return 1;
}

static void usage(void)
{
    puts("CuePull32 " CP_VERSION " - portable native BIN/CUE extractor");
    puts("Usage: cuepull32-cli inspect|list|extract|verify IMAGE [DEST]");
    puts("       [--layout MODE1/2048|MODE1/2352|MODE2/2352|MODE2/2336]");
    puts("       [--start FRAME] (requires --layout, for BIN without CUE)");
    puts("Extract defaults to a new sibling folder named after IMAGE.");
    puts("Existing automatic folders receive (2), (3), etc.; never overwritten.");
    puts("Primary ISO9660 ASCII single-extent files only. Audio is skipped.");
    puts(cp_build_notice());
}

int cp_cli(int argc, char **argv)
{
    cp_disc disc;
    const char *layout, *destination, *command;
    char automatic[CP_PATH], number[32], total[32], path[CP_PATH];
    cp_u64 start;
    int i, ok, has_start;
    unsigned n;
    if (argc < 3) { usage(); return argc == 2 && !strcmp(argv[1], "--help") ? 0 : 2; }
    command = argv[1]; layout = NULL; destination = NULL; start = 0; has_start = 0;
    if (strcmp(command, "inspect") && strcmp(command, "list") &&
        strcmp(command, "extract") && strcmp(command, "verify")) {
        usage(); return 2;
    }
    for (i = 3; i < argc; ++i) {
        if (!strcmp(argv[i], "--layout") && i + 1 < argc) layout = argv[++i];
        else if (!strcmp(argv[i], "--start") && i + 1 < argc) {
            has_start = 1;
            if (!cp_parse_u64(argv[++i], &start)) { fputs("Invalid frame offset.\n", stderr); return 2; }
        } else if (argv[i][0] != '-' && !destination) destination = argv[i];
        else { usage(); return 2; }
    }
    if ((has_start && !layout) || (destination && !strcmp(command, "inspect")) ||
        (destination && !strcmp(command, "list")) || (!destination && !strcmp(command, "verify"))) {
        usage(); return 2;
    }
    ok = cp_open(&disc, argv[2], layout, start, show_progress, NULL);
    if (ok) {
        cp_decimal(disc.data_bytes, total);
        printf("ISO9660 primary volume: %s\n", disc.volume);
        printf("Files: %u  Directories: %u  File bytes: %s\n", disc.files, disc.directories, total);
        cp_decimal(disc.tracks[disc.selected].start, number);
        printf("Data layout: %s  INDEX 01/start: %s\n", cp_mode_name(disc.tracks[disc.selected].mode), number);
        printf("Audio tracks skipped: %u  Layout recovered without CUE: %s\n", disc.audio_count, disc.inferred ? "yes" : "no");
        if (disc.joliet_present) puts("Supplementary volume present; using primary names (no Joliet parity).");
        if (disc.opaque_metadata) puts("Opaque vendor system-use metadata ignored; primary names/extents used.");
        for (n = 0; n < disc.track_count; ++n) {
            cp_decimal(disc.tracks[n].start, number); cp_decimal(disc.tracks[n].end, total);
            printf("Track %02u %s start=%s end=%s", disc.tracks[n].number, cp_mode_name(disc.tracks[n].mode), number, total);
            cp_decimal(disc.tracks[n].pregap, total); printf(" virtual_pregap=%s\n", total);
        }
        if (!strcmp(command, "list")) {
            for (n = 1; ok && n < disc.count; ++n) {
                ok = cp_entry_path(&disc, n, "", path);
                cp_decimal(disc.entries[n].size, number);
                if (ok) printf("%c\t%s\t%s\n", disc.entries[n].directory ? 'D' : 'F', number, path + 1);
            }
        } else if (!strcmp(command, "extract")) {
            if (!destination) {
                ok = cp_default_destination(&disc, automatic); destination = automatic;
            }
            if (ok) ok = cp_extract(&disc, destination);
            if (ok) ok = cp_verify(&disc, destination);
            if (ok) printf("Extraction complete and extent samples verified: %s\n", destination);
        } else if (!strcmp(command, "verify")) {
            ok = cp_verify(&disc, destination);
            if (ok) puts("All file lengths and first/middle/last samples match source extents.");
        }
    }
    if (!ok) fprintf(stderr, "CuePull32: %s\n", disc.error);
    cp_close(&disc); return ok ? 0 : 1;
}

#ifndef CP_GUI
int main(int argc, char **argv) { return cp_cli(argc, argv); }
#endif
