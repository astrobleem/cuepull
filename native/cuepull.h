/* CuePull32: MIT licensed, conservative C89 plus a 64-bit integer type. */
#ifndef CUEPULL_H
#define CUEPULL_H
#include <stddef.h>
#if defined(__WATCOMC__) || defined(_MSC_VER)
typedef unsigned __int64 cp_u64;
#else
typedef unsigned long long cp_u64;
#endif
typedef unsigned int cp_u32;
typedef char cp_u32_must_be_32_bits[sizeof(cp_u32) == 4 ? 1 : -1];

#define CP_PATH 260
#define CP_ERROR 512
#define CP_MAX_ENTRIES 16384
#define CP_MAX_DEPTH 32
#define CP_MANIFEST "cuepull32-manifest.tsv"
#define CP_VERSION "0.1.0-candidate"

typedef struct cp_entry {
    unsigned parent;
    cp_u32 extent;
    cp_u32 size;
    unsigned depth;
    int directory;
    char original[256];
    char name[128];
} cp_entry;

typedef struct cp_track {
    unsigned number;
    int mode;
    cp_u64 start;
    cp_u64 end;
    cp_u64 index00;
    cp_u64 pregap;
    int has00;
    int has01;
} cp_track;

typedef int (*cp_progress)(void *, cp_u64, cp_u64, const char *);
typedef struct cp_disc {
    void *image;
    char input[CP_PATH];
    char image_path[CP_PATH];
    char error[CP_ERROR];
    char volume[33];
    cp_u64 image_bytes;
    cp_u64 data_bytes;
    cp_u32 volume_blocks;
    cp_u32 root_extent;
    cp_u32 root_size;
    cp_track tracks[99];
    unsigned track_count;
    unsigned selected;
    unsigned audio_count;
    unsigned files;
    unsigned directories;
    unsigned count;
    unsigned capacity;
    cp_entry *entries;
    int inferred;
    int joliet_present;
    int opaque_metadata;
    cp_u64 cached_sector;
    int cache_valid;
    unsigned char sector[2352];
    cp_progress progress;
    void *progress_user;
} cp_disc;

/* NULL layout selects automatic recovery; start is meaningful with layout. */
int cp_open(cp_disc *, const char *, const char *, cp_u64,
            cp_progress, void *);
void cp_close(cp_disc *);
int cp_extract(cp_disc *, const char *);
int cp_verify(cp_disc *, const char *);
int cp_entry_path(cp_disc *, unsigned, const char *, char *);
int cp_default_destination(cp_disc *, char *);
const char *cp_mode_name(int);
const char *cp_build_notice(void);
void cp_decimal(cp_u64, char *);
int cp_parse_u64(const char *, cp_u64 *);
int cp_cli(int, char **);
#endif
