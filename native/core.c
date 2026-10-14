/*
 * Bounded ISO9660 primary-volume subset, based on ECMA-119 (not a full
 * filesystem implementation). No third-party code is incorporated here.
 * Both-endian fields, extents, record boundaries and names are validated
 * before any output is created. See README for deliberately rejected cases.
 */
#include "cuepull.h"
#include "platform.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#define CP_DIR_LIMIT (8UL * 1024UL * 1024UL)
#define CP_SCAN_LIMIT (64UL * 1024UL * 1024UL)
#define CP_CHUNK (64U * 1024U)

const char *cp_build_notice(void)
{
#ifdef __WATCOMC__
    return "CuePull32 source: MIT. This build includes unmodified Open Watcom 1.9 C runtime/startup code, under Sybase Open Watcom Public License 1.0. Runtime source: https://openwatcom.org/ftp/source/open_watcom_1.9.0-src.zip . See THIRD_PARTY.md and licenses/OpenWatcom-1.9.txt.";
#else
    return "CuePull32 source: MIT. System/compiler runtime terms apply to this build.";
#endif
}

static int fail(cp_disc *disc, const char *message)
{
    strncpy(disc->error, message, CP_ERROR - 1);
    disc->error[CP_ERROR - 1] = 0;
    return 0;
}

static int same(const char *a, const char *b)
{
    while (*a && *b) {
        if (toupper((unsigned char)*a++) != toupper((unsigned char)*b++))
            return 0;
    }
    return *a == *b;
}

void cp_decimal(cp_u64 value, char *out)
{
    char reverse[32];
    unsigned n, i;
    n = 0;
    do { reverse[n++] = (char)('0' + value % 10); value /= 10; } while (value);
    for (i = 0; i < n; ++i) out[i] = reverse[n - i - 1];
    out[n] = 0;
}

int cp_parse_u64(const char *text, cp_u64 *out)
{
    cp_u64 value, maximum;
    unsigned digit;
    if (!*text) return 0;
    value = 0; maximum = ~(cp_u64)0;
    while (*text) {
        if (*text < '0' || *text > '9') return 0;
        digit = (unsigned)(*text++ - '0');
        if (value > (maximum - digit) / 10) return 0;
        value = value * 10 + digit;
    }
    *out = value; return 1;
}

const char *cp_mode_name(int mode)
{
    static const char *names[] = {"AUDIO", "MODE1/2048", "MODE1/2352",
                                 "MODE2/2352", "MODE2/2336"};
    return mode >= 0 && mode <= 4 ? names[mode] : "UNKNOWN";
}

static unsigned width(int mode)
{
    return mode == 1 ? 2048 : mode == 4 ? 2336 : 2352;
}

static int mode_number(const char *text)
{
    int i;
    for (i = 0; i <= 4; ++i) if (same(text, cp_mode_name(i))) return i;
    return -1;
}

static char *base_name(char *path)
{
    char *p, *base;
    base = path;
    for (p = path; *p; ++p) if (*p == '/' || *p == '\\') base = p + 1;
    return base;
}

static int device_name(const char *text)
{
    char base[16];
    unsigned i;
    i = 0;
    while (*text && *text != '.' && i < sizeof(base) - 1)
        base[i++] = (char)toupper((unsigned char)*text++);
    base[i] = 0;
    if (!strcmp(base, "CON") || !strcmp(base, "PRN") ||
        !strcmp(base, "AUX") || !strcmp(base, "NUL") ||
        !strcmp(base, "CLOCK$") || !strcmp(base, "CONIN$") ||
        !strcmp(base, "CONOUT$")) return 1;
    return strlen(base) == 4 &&
        ((!strncmp(base, "COM", 3)) || (!strncmp(base, "LPT", 3))) &&
        base[3] >= '1' && base[3] <= '9';
}

static int component_ok(const char *name)
{
    const unsigned char *p;
    if (!*name || !strcmp(name, ".") || !strcmp(name, "..") ||
        device_name(name)) return 0;
    for (p = (const unsigned char *)name; *p; ++p)
        if (*p < 32 || *p == 127 || strchr("/\\:", *p)) return 0;
    return 1;
}

static int stem_same(const char *left, const char *right)
{
    char a[CP_PATH], b[CP_PATH], *p;
    strcpy(a, left); strcpy(b, right);
    p = strrchr(a, '.'); if (p) *p = 0;
    p = strrchr(b, '.'); if (p) *p = 0;
    return same(a, b);
}

static int frames(const char *text, cp_u64 *out)
{
    char minutes[32];
    const char *colon;
    cp_u64 m;
    unsigned s, f;
    size_t n;
    colon = strchr(text, ':');
    if (!colon) return 0;
    n = (size_t)(colon - text);
    if (!n || n >= sizeof(minutes) || strlen(colon) != 6 || colon[3] != ':')
        return 0;
    memcpy(minutes, text, n); minutes[n] = 0;
    if (!cp_parse_u64(minutes, &m) || m > 1000000 ||
        !isdigit((unsigned char)colon[1]) || !isdigit((unsigned char)colon[2]) ||
        !isdigit((unsigned char)colon[4]) || !isdigit((unsigned char)colon[5]))
        return 0;
    s = (unsigned)(colon[1] - '0') * 10 + (unsigned)(colon[2] - '0');
    f = (unsigned)(colon[4] - '0') * 10 + (unsigned)(colon[5] - '0');
    if (s >= 60 || f >= 75) return 0;
    *out = (m * 60 + s) * 75 + f; return 1;
}

/* Strict bounded tokenizer, quoted FILE names and ordinary metadata. */
static int tokens(char *line, char **items, unsigned *count)
{
    char *p;
    unsigned n;
    p = line; n = 0;
    while (*p) {
        while (*p && isspace((unsigned char)*p)) ++p;
        if (!*p) break;
        if (n == 8) return 0;
        if (*p == '"') {
            items[n++] = ++p;
            while (*p && *p != '"') ++p;
            if (!*p) return 0;
            *p++ = 0;
            if (*p && !isspace((unsigned char)*p)) return 0;
        } else {
            items[n++] = p;
            while (*p && !isspace((unsigned char)*p)) ++p;
            if (*p) *p++ = 0;
        }
    }
    *count = n; return 1;
}

static int cue_parse(cp_disc *disc)
{
    cp_file *cue;
    cp_u64 bytes, value;
    char *text, *line, *next, *args[8];
    char cue_name[CP_PATH], image_name[CP_PATH];
    unsigned n, line_number, i;
    int have_file, mode;
    cp_track *track;
    cue = cp_file_open(disc->input, 0, disc->error);
    if (!cue) return 0;
    if (!cp_file_size(cue, &bytes, disc->error) || bytes > 1024UL * 1024UL) {
        cp_file_close(cue, disc->error);
        return fail(disc, "CUE exceeds the 1 MiB limit or cannot be read.");
    }
    text = (char *)malloc((size_t)bytes + 1);
    if (!text) { cp_file_close(cue, disc->error); return fail(disc, "Out of memory."); }
    if (!cp_file_read(cue, 0, text, (unsigned)bytes, disc->error)) {
        free(text); cp_file_close(cue, disc->error); return 0;
    }
    cp_file_close(cue, disc->error);
    if (memchr(text, 0, (size_t)bytes)) { free(text); return fail(disc, "NUL in CUE text."); }
    text[(size_t)bytes] = 0; line = text;
    if (bytes >= 3 && (unsigned char)text[0] == 0xef &&
        (unsigned char)text[1] == 0xbb && (unsigned char)text[2] == 0xbf) line += 3;
    strcpy(cue_name, base_name(disc->input));
    have_file = 0; track = NULL; line_number = 0;
    while (line && *line) {
        ++line_number; next = strchr(line, '\n');
        if (next) *next++ = 0;
        if (strlen(line) > 1023 || !tokens(line, args, &n)) goto malformed;
        if (!n) { line = next; continue; }
        if (same(args[0], "REM")) {
            for (i = 1; i < n; ++i) if (same(args[i], "SESSION")) goto unsupported;
        } else if (same(args[0], "FILE")) {
            if (have_file || n != 3 || !same(args[2], "BINARY")) goto unsupported;
            if (strlen(args[1]) >= CP_PATH || !component_ok(args[1]) ||
                !stem_same(args[1], cue_name) ||
                strchr(args[1], '*') || strchr(args[1], '?') ||
                args[1][strlen(args[1]) - 1] == '.') goto unsafe;
            strcpy(image_name, args[1]);
            strcpy(disc->image_path, disc->input);
            i = (unsigned)(base_name(disc->image_path) - disc->image_path);
            if (i + strlen(image_name) >= CP_PATH) goto unsafe;
            strcpy(disc->image_path + i, image_name); have_file = 1;
        } else if (same(args[0], "TRACK")) {
            if (!have_file || n != 3 || disc->track_count == 99 ||
                !cp_parse_u64(args[1], &value) || value < 1 || value > 99 ||
                (disc->track_count && value <= disc->tracks[disc->track_count - 1].number))
                goto malformed;
            mode = mode_number(args[2]); if (mode < 0) goto unsupported;
            track = &disc->tracks[disc->track_count++];
            memset(track, 0, sizeof(*track)); track->number = (unsigned)value;
            track->mode = mode;
        } else if (same(args[0], "INDEX")) {
            cp_u64 index;
            if (!track || n != 3 || !cp_parse_u64(args[1], &index) || index > 1 ||
                !frames(args[2], &value)) goto malformed;
            if (index == 0) {
                if (track->has00 || track->has01 || track->pregap) goto malformed;
                track->has00 = 1; track->index00 = value;
            } else {
                if (track->has01 || (track->has00 && value < track->index00)) goto malformed;
                track->has01 = 1; track->start = value;
            }
        } else if (same(args[0], "PREGAP")) {
            if (!track || n != 2 || track->pregap || track->has00 ||
                !frames(args[1], &value)) goto malformed;
            track->pregap = value;
        } else if (same(args[0], "TITLE") || same(args[0], "PERFORMER") ||
                   same(args[0], "SONGWRITER") || same(args[0], "CATALOG") ||
                   same(args[0], "ISRC") || same(args[0], "FLAGS")) {
            /* Metadata does not affect file-relative sector offsets. */
        } else goto unsupported;
        line = next;
    }
    free(text);
    if (!have_file || !disc->track_count) return fail(disc, "CUE contains no tracks.");
    disc->image = cp_file_open(disc->image_path, 0, disc->error);
    if (!disc->image || !cp_file_size((cp_file *)disc->image, &disc->image_bytes, disc->error)) return 0;
    mode = disc->tracks[0].mode;
    if (disc->image_bytes % width(mode)) return fail(disc, "Image ends with a partial sector.");
    for (i = 0; i < disc->track_count; ++i) {
        track = &disc->tracks[i];
        if (width(track->mode) != width(mode))
            return fail(disc, "Mixed physical widths within one FILE are unsupported.");
        if (!track->has01) return fail(disc, "Every track needs INDEX 01.");
        track->end = i + 1 < disc->track_count ?
            (disc->tracks[i + 1].has00 ? disc->tracks[i + 1].index00 : disc->tracks[i + 1].start) :
            disc->image_bytes / width(mode);
        if (track->start >= track->end || track->end > disc->image_bytes / width(mode) ||
            (i && (track->has00 ? track->index00 : track->start) < disc->tracks[i - 1].start))
            return fail(disc, "Track indexes overlap, decrease, or exceed the image.");
        if (!track->mode) ++disc->audio_count;
    }
    return 1;
malformed:
    free(text); return fail(disc, "Malformed CUE (track/index/time/line limit).");
unsupported:
    free(text); return fail(disc, "Unsupported CUE: one BINARY FILE, INDEX 00/01, supported modes only; no sessions/POSTGAP.");
unsafe:
    free(text); return fail(disc, "Unsafe/unrelated CUE FILE reference. Only a matching-stem sibling image is allowed.");
}

static int logical_read(cp_disc *disc, cp_u64 position, void *buffer, unsigned length)
{
    cp_track *track;
    cp_u64 logical_size, sector_number, physical;
    unsigned within, take, offset, w;
    unsigned char *out, *raw;
    static const unsigned char sync[12] = {0,255,255,255,255,255,255,255,255,255,255,0};
    track = &disc->tracks[disc->selected]; w = width(track->mode);
    if (!track->mode) return fail(disc, "Audio is not a filesystem data track.");
    logical_size = (track->end - track->start) * 2048;
    if (position > logical_size || length > logical_size - position)
        return fail(disc, "Logical read exceeds track extent.");
    out = (unsigned char *)buffer;
    while (length) {
        sector_number = position / 2048;
        within = (unsigned)(position % 2048);
        physical = track->start + sector_number;
        if (!disc->cache_valid || physical != disc->cached_sector) {
            if (!cp_file_read((cp_file *)disc->image, physical * w, disc->sector, w, disc->error)) return 0;
            raw = disc->sector;
            if (track->mode == 2 || track->mode == 3) {
                if (memcmp(raw, sync, 12) || raw[15] != (track->mode == 2 ? 1 : 2))
                    return fail(disc, "Invalid raw sector sync/mode header.");
            }
            if (track->mode == 3 || track->mode == 4) {
                offset = track->mode == 3 ? 16 : 0;
                if (memcmp(raw + offset, raw + offset + 4, 4) || (raw[offset + 2] & 32))
                    return fail(disc, "MODE2 subheader mismatch or unsupported Form2 sector.");
            }
            disc->cached_sector = physical; disc->cache_valid = 1;
        }
        offset = track->mode == 1 ? 0 : track->mode == 2 ? 16 : track->mode == 3 ? 24 : 8;
        take = 2048 - within; if (take > length) take = length;
        memcpy(out, disc->sector + offset + within, take);
        out += take; length -= take; position += take;
    }
    return 1;
}

static int both32(cp_disc *disc, const unsigned char *p, cp_u32 *out)
{
    cp_u32 little, big;
    little = (cp_u32)p[0] | (cp_u32)p[1] << 8 | (cp_u32)p[2] << 16 | (cp_u32)p[3] << 24;
    big = (cp_u32)p[7] | (cp_u32)p[6] << 8 | (cp_u32)p[5] << 16 | (cp_u32)p[4] << 24;
    if (little != big) return fail(disc, "ISO both-endian field disagrees.");
    *out = little; return 1;
}

static int both16(cp_disc *disc, const unsigned char *p, unsigned *out)
{
    unsigned little, big;
    little = p[0] | (unsigned)p[1] << 8;
    big = p[3] | (unsigned)p[2] << 8;
    if (little != big) return fail(disc, "ISO both-endian field disagrees.");
    *out = little; return 1;
}

static int record(cp_disc *disc, const unsigned char *p, unsigned length,
                  cp_entry *entry, int special)
{
    unsigned name_length, sequence, system_at, i;
    cp_u64 rounded;
    if (length < 34 || p[0] != length || p[1] || p[26] || p[27] ||
        (p[25] & (4 | 8 | 16 | 32 | 64 | 128)))
        return fail(disc, "Unsupported/malformed ISO record (attributes, interleave, associated or multi-extent).");
    if (!both32(disc, p + 2, &entry->extent) || !both32(disc, p + 10, &entry->size) ||
        !both16(disc, p + 28, &sequence)) return 0;
    if (sequence != 1) return fail(disc, "Multi-volume ISO records are unsupported.");
    name_length = p[32];
    if (!name_length || 33 + name_length > length)
        return fail(disc, "ISO name exceeds record boundary.");
    system_at = 33 + name_length + (name_length % 2 == 0 ? 1 : 0);
    if (system_at > length) return fail(disc, "ISO record lacks required name padding.");
    if (name_length % 2 == 0 && p[33 + name_length])
        return fail(disc, "ISO name padding is nonzero.");
    /* ECMA-119 permits opaque system-use bytes. They do not change primary
     * names/extents. Recognized SUSP records (version 1) are rejected: this
     * includes SP/ER and Rock Ridge links/relocation, never interpreted here.
     * The Tandy disc has pre-SUSP vendor metadata beginning BA/hs/cd/EL. */
    if (length >= system_at + 4 && p[system_at + 2] >= 4 &&
        p[system_at + 2] <= length - system_at && p[system_at + 3] == 1)
        return fail(disc, "SUSP/Rock Ridge extensions are unsupported by this candidate.");
    for (i = system_at; i < length; ++i)
        if (p[i]) disc->opaque_metadata = 1;
    rounded = ((cp_u64)entry->size + 2047) / 2048;
    if (entry->extent > disc->volume_blocks || rounded > disc->volume_blocks - entry->extent)
        return fail(disc, "ISO extent exceeds declared volume.");
    entry->directory = (p[25] & 2) != 0;
    if (entry->directory && (!entry->size || entry->size % 2048 || entry->size > CP_DIR_LIMIT))
        return fail(disc, "Directory size is invalid or exceeds 8 MiB.");
    if (special) {
        if (name_length != 1 || p[33] > 1 || !entry->directory)
            return fail(disc, "Invalid ISO dot/dotdot entry.");
    } else {
        for (i = 0; i < name_length; ++i) {
            if (p[33 + i] < 32 || p[33 + i] > 126)
                return fail(disc, "Native candidate supports printable ASCII primary names only.");
            entry->original[i] = (char)p[33 + i];
        }
        entry->original[name_length] = 0;
        if (!component_ok(entry->original)) return fail(disc, "Unsafe ISO name (traversal, separator, device or stream).");
    }
    return 1;
}

static int descriptors(cp_disc *disc)
{
    unsigned char block[2048];
    unsigned i, j, block_size, volumes, sequence;
    int primary, terminated;
    cp_entry root;
    cp_track *track;
    primary = terminated = 0; disc->joliet_present = 0;
    track = &disc->tracks[disc->selected]; disc->cache_valid = 0;
    for (i = 16; i < 80; ++i) {
        if (!logical_read(disc, (cp_u64)i * 2048, block, 2048)) return 0;
        if (memcmp(block + 1, "CD001", 5) || block[6] != 1)
            return fail(disc, "Missing/corrupt ISO9660 descriptor signature.");
        if (block[0] == 255) { terminated = 1; break; }
        if (block[0] == 2) { disc->joliet_present = 1; continue; }
        if (block[0] == 0) continue;
        if (block[0] != 1 || primary) return fail(disc, "Unknown/duplicate ISO descriptor.");
        primary = 1;
        if (!both32(disc, block + 80, &disc->volume_blocks) ||
            !both16(disc, block + 120, &volumes) || !both16(disc, block + 124, &sequence) ||
            !both16(disc, block + 128, &block_size)) return 0;
        if (!disc->volume_blocks || block_size != 2048 || volumes != 1 || sequence != 1 ||
            disc->volume_blocks > track->end - track->start)
            return fail(disc, "Unsupported ISO block size, multi-volume set, or truncated volume.");
        memset(&root, 0, sizeof(root));
        if (!record(disc, block + 156, block[156], &root, 1) || block[189] != 0)
            return fail(disc, "Invalid primary root directory.");
        disc->root_extent = root.extent; disc->root_size = root.size;
        memcpy(disc->volume, block + 40, 32); disc->volume[32] = 0;
        for (j = 0; j < 32; ++j)
            if ((unsigned char)disc->volume[j] < 32 || (unsigned char)disc->volume[j] > 126)
                return fail(disc, "Volume identifier is not printable ASCII.");
        for (j = 32; j && disc->volume[j - 1] == ' '; --j) disc->volume[j - 1] = 0;
    }
    if (!primary) return fail(disc, "No primary ISO9660 volume.");
    return terminated ? 1 : fail(disc, "ISO descriptor terminator not found within 64 blocks.");
}

static int append(cp_disc *disc, const cp_entry *entry)
{
    cp_entry *grown;
    unsigned capacity;
    if (disc->count == CP_MAX_ENTRIES) return fail(disc, "Entry count exceeds native 16384 limit.");
    if (disc->count == disc->capacity) {
        capacity = disc->capacity + 256;
        grown = (cp_entry *)realloc(disc->entries, (size_t)capacity * sizeof(*grown));
        if (!grown) return fail(disc, "Out of memory reading directory inventory.");
        disc->entries = grown; disc->capacity = capacity;
    }
    disc->entries[disc->count++] = *entry; return 1;
}

static int allocate_name(cp_disc *disc, cp_entry *entry)
{
    char initial[112], source[256], candidate[128], suffix[32], *version;
    unsigned i, n, attempt, j;
    strcpy(source, entry->original);
    if (!entry->directory) {
        version = strrchr(source, ';');
        if (version) {
            cp_u64 v;
            if (!cp_parse_u64(version + 1, &v) || !v || v > 32767)
                return fail(disc, "Invalid ISO file version suffix.");
            *version = 0;
        }
    }
    n = 0;
    for (i = 0; source[i] && n < 100; ++i)
        initial[n++] = strchr("<>\"|?*;", source[i]) ? '_' : source[i];
    while (n && (initial[n - 1] == '.' || initial[n - 1] == ' ')) --n;
    initial[n] = 0;
    if (!component_ok(initial)) return fail(disc, "Name becomes unsafe after normalization.");
    for (attempt = 1; attempt <= CP_MAX_ENTRIES; ++attempt) {
        strcpy(candidate, initial);
        if (attempt > 1) {
            strcat(candidate, "--"); cp_decimal(attempt, suffix); strcat(candidate, suffix);
        }
        if (entry->parent == 0 && same(candidate, CP_MANIFEST)) continue;
        for (j = 1; j < disc->count; ++j)
            if (disc->entries[j].parent == entry->parent && same(disc->entries[j].name, candidate)) break;
        if (j == disc->count) { strcpy(entry->name, candidate); return 1; }
    }
    return fail(disc, "Cannot allocate a collision-free output name.");
}

static int inventory(cp_disc *disc)
{
    cp_entry entry, directory, dot;
    unsigned d, at, length, i, dots;
    unsigned char *data;
    memset(&entry, 0, sizeof(entry)); entry.directory = 1;
    entry.extent = disc->root_extent; entry.size = disc->root_size;
    if (!append(disc, &entry)) return 0;
    for (d = 0; d < disc->count; ++d) {
        if (!disc->entries[d].directory) continue;
        directory = disc->entries[d];
        if (disc->progress && !disc->progress(disc->progress_user, 0, 0, "Reading directory inventory..."))
            return fail(disc, "Cancelled before extraction.");
        data = (unsigned char *)malloc(directory.size);
        if (!data) return fail(disc, "Out of memory reading directory.");
        if (!logical_read(disc, (cp_u64)directory.extent * 2048, data, directory.size)) { free(data); return 0; }
        dots = 0; at = 0;
        while (at < directory.size) {
            length = data[at];
            if (!length) {
                unsigned boundary;
                boundary = (at / 2048 + 1) * 2048;
                for (i = at; i < boundary; ++i) if (data[i]) {
                    free(data); return fail(disc, "Nonzero data after directory padding.");
                }
                at = boundary; continue;
            }
            if (length > directory.size - at || length > 2048 - at % 2048) {
                free(data); return fail(disc, "Directory record crosses sector boundary.");
            }
            memset(&entry, 0, sizeof(entry));
            if (dots < 2) {
                if (!record(disc, data + at, length, &dot, 1)) { free(data); return 0; }
                if (data[at + 33] != dots ||
                    dot.extent != (dots == 0 ? directory.extent : disc->entries[directory.parent].extent) ||
                    (dots == 0 ? dot.size != directory.size :
                        dot.size > disc->entries[directory.parent].size)) {
                    free(data); return fail(disc, "Invalid directory dot/dotdot extent.");
                }
                ++dots; at += length; continue;
            }
            if (!record(disc, data + at, length, &entry, 0)) { free(data); return 0; }
            entry.parent = d; entry.depth = directory.depth + 1;
            if (entry.depth > CP_MAX_DEPTH) { free(data); return fail(disc, "Directory depth exceeds 32."); }
            for (i = 0; i < disc->count; ++i) {
                if ((i && disc->entries[i].parent == d && !strcmp(disc->entries[i].original, entry.original)) ||
                    (entry.directory && disc->entries[i].directory && disc->entries[i].extent == entry.extent)) {
                    free(data); return fail(disc, "Duplicate ISO identifier or directory cycle/alias.");
                }
            }
            if (!allocate_name(disc, &entry) || !append(disc, &entry)) { free(data); return 0; }
            if (entry.directory) ++disc->directories;
            else { ++disc->files; disc->data_bytes += entry.size; }
            at += length;
        }
        free(data);
        if (dots != 2) return fail(disc, "Directory lacks dot/dotdot records.");
    }
    return 1;
}

static void clear_inventory(cp_disc *disc)
{
    free(disc->entries); disc->entries = NULL;
    disc->count = disc->capacity = disc->files = disc->directories = 0;
    disc->data_bytes = 0;
}

static int volume(cp_disc *disc)
{
    clear_inventory(disc);
    return descriptors(disc) && inventory(disc);
}

/* Scan bounded physical bytes for primary descriptors. Full trees validate
 * candidates; multiple readable volumes fail rather than guessing a session. */
static int recover(cp_disc *disc, const char *layout, cp_u64 start)
{
    unsigned char *buffer;
    cp_u64 limit, at, found, candidates_start[16];
    int candidates_mode[16], mode, candidate_count, valid, chosen_mode;
    cp_u64 chosen_start;
    unsigned take, i, j, offset, w;
    cp_track *track;
    track = &disc->tracks[0]; disc->track_count = 1; disc->selected = 0;
    if (layout) {
        mode = mode_number(layout);
        if (mode <= 0) return fail(disc, "Explicit layout must be a supported data mode.");
        w = width(mode);
        if (disc->image_bytes % w || start >= disc->image_bytes / w)
            return fail(disc, "Explicit layout/start exceeds image or leaves a partial sector.");
        track->number = 1; track->mode = mode; track->start = start;
        track->end = disc->image_bytes / w; track->has01 = 1;
        return volume(disc);
    }
    buffer = (unsigned char *)malloc(1024UL * 1024UL + 6);
    if (!buffer) return fail(disc, "Out of memory scanning image.");
    limit = disc->image_bytes < CP_SCAN_LIMIT ? disc->image_bytes : CP_SCAN_LIMIT;
    at = 0; candidate_count = 0;
    while (at < limit) {
        take = (unsigned)((limit - at) < 1024UL * 1024UL ? limit - at : 1024UL * 1024UL);
        if (!cp_file_read((cp_file *)disc->image, at, buffer, take, disc->error)) { free(buffer); return 0; }
        for (i = 0; i + 7 <= take; ++i) {
            if (buffer[i] != 1 || memcmp(buffer + i + 1, "CD001\1", 6)) continue;
            found = at + i;
            for (mode = 1; mode <= 4; ++mode) {
                w = width(mode); offset = mode == 1 ? 0 : mode == 2 ? 16 : mode == 3 ? 24 : 8;
                if (found < (cp_u64)16 * w + offset || (found - offset) % w || disc->image_bytes % w) continue;
                start = (found - offset) / w - 16;
                for (j = 0; j < (unsigned)candidate_count; ++j)
                    if (candidates_start[j] == start && candidates_mode[j] == mode) break;
                if (j < (unsigned)candidate_count) continue;
                if (candidate_count == 16) { free(buffer); return fail(disc, "Too many candidate volumes; specify --layout and --start."); }
                candidates_start[candidate_count] = start;
                candidates_mode[candidate_count++] = mode;
            }
        }
        if (disc->progress && !disc->progress(disc->progress_user, at, limit, "Recovering sector layout...")) {
            free(buffer); return fail(disc, "Cancelled while scanning.");
        }
        if (take <= 6) break;
        at += take - 6;
    }
    free(buffer); valid = 0; chosen_mode = 0; chosen_start = 0;
    for (i = 0; i < (unsigned)candidate_count; ++i) {
        memset(track, 0, sizeof(*track)); track->number = 1;
        track->mode = candidates_mode[i]; track->start = candidates_start[i];
        track->end = disc->image_bytes / width(track->mode); track->has01 = 1;
        if (volume(disc)) { ++valid; chosen_mode = track->mode; chosen_start = track->start; }
        else if (!strncmp(disc->error, "Cancelled", 9)) return 0;
    }
    if (valid != 1) return fail(disc, valid ? "Multiple readable volumes; specify --layout and --start."
                                          : "No supported primary ISO9660 volume in first 64 MiB; damaged/unsupported image.");
    memset(track, 0, sizeof(*track)); track->number = 1;
    track->mode = chosen_mode; track->start = chosen_start;
    track->end = disc->image_bytes / width(chosen_mode); track->has01 = 1;
    disc->error[0] = 0; return volume(disc);
}

int cp_open(cp_disc *disc, const char *input, const char *layout, cp_u64 start,
            cp_progress progress, void *user)
{
    char *extension;
    unsigned i, valid, chosen;
    memset(disc, 0, sizeof(*disc)); disc->progress = progress; disc->progress_user = user;
    if (!cp_absolute(input, disc->input, disc->error) ||
        !cp_safe_ancestry(disc->input, 0, disc->error)) return 0;
    extension = strrchr(base_name(disc->input), '.');
    if (extension && same(extension, ".cue")) {
        if (layout) return fail(disc, "Layout override applies only to BIN/ISO without CUE.");
        if (!cue_parse(disc)) return 0;
        valid = 0; chosen = 0;
        for (i = 0; i < disc->track_count; ++i) {
            if (!disc->tracks[i].mode) continue;
            disc->selected = i;
            if (volume(disc)) { ++valid; chosen = i; }
            else if (!strncmp(disc->error, "Cancelled", 9)) return 0;
        }
        if (valid > 1) return fail(disc, "Multiple readable data tracks are unsupported.");
        if (!valid) return disc->error[0] ? 0 : fail(disc, "No supported readable ISO9660 data track.");
        disc->selected = chosen; disc->error[0] = 0;
        return volume(disc);
    }
    strcpy(disc->image_path, disc->input);
    disc->image = cp_file_open(disc->image_path, 0, disc->error);
    if (!disc->image || !cp_file_size((cp_file *)disc->image, &disc->image_bytes, disc->error)) return 0;
    disc->inferred = 1; return recover(disc, layout, start);
}

void cp_close(cp_disc *disc)
{
    char ignored[CP_ERROR];
    cp_file_close((cp_file *)disc->image, ignored); disc->image = NULL;
    clear_inventory(disc);
}

int cp_entry_path(cp_disc *disc, unsigned index, const char *root, char *out)
{
    unsigned parents[CP_MAX_DEPTH + 1], n, i;
    size_t length;
    n = 0;
    while (index) {
        if (index >= disc->count || n == CP_MAX_DEPTH + 1)
            return fail(disc, "Invalid internal directory ancestry.");
        parents[n++] = index; index = disc->entries[index].parent;
    }
    if (strlen(root) >= CP_PATH) return fail(disc, "Output root too long.");
    strcpy(out, root); length = strlen(out);
    for (i = n; i; --i) {
        const char *name;
        name = disc->entries[parents[i - 1]].name;
        if (length + strlen(name) + 2 >= CP_PATH)
            return fail(disc, "An output path exceeds the native 259-byte limit. Choose a shorter destination.");
#if defined(_WIN32) || defined(__NT__)
        out[length++] = '\\';
#else
        out[length++] = '/';
#endif
        strcpy(out + length, name); length += strlen(name);
    }
    return 1;
}

int cp_default_destination(cp_disc *disc, char *out)
{
    char initial[CP_PATH], suffix[32], *extension;
    unsigned attempt;
    strcpy(initial, disc->input); extension = strrchr(base_name(initial), '.');
    if (extension) *extension = 0;
    if (!component_ok(base_name(initial))) return fail(disc, "Unsafe image stem for automatic destination.");
    for (attempt = 1; attempt < 10000; ++attempt) {
        strcpy(out, initial);
        if (attempt > 1) {
            cp_decimal(attempt, suffix);
            if (strlen(initial) + strlen(suffix) + 4 >= CP_PATH)
                return fail(disc, "Automatic output path too long.");
            strcat(out, " ("); strcat(out, suffix); strcat(out, ")");
        }
        if (!cp_path_exists(out)) return 1;
    }
    return fail(disc, "Cannot find an unused automatic destination.");
}

static int text_write(cp_disc *disc, cp_file *file, const char *text)
{
    return cp_file_write(file, text, (unsigned)strlen(text), disc->error);
}

static int manifest_row(cp_disc *disc, cp_file *manifest, unsigned index)
{
    char path[CP_PATH], number[32], hex[513];
    unsigned ancestry[CP_MAX_DEPTH + 1], n, i, j;
    const unsigned char *p;
    static const char digits[] = "0123456789abcdef";
    cp_entry *entry;
    entry = &disc->entries[index];
    if (!cp_entry_path(disc, index, "", path)) return 0;
    for (i = 0; path[i]; ++i) if (path[i] == '\\') path[i] = '/';
    if (!text_write(disc, manifest, entry->directory ? "D\t" : "F\t") ||
        !text_write(disc, manifest, path + 1) || !text_write(disc, manifest, "\t")) return 0;
    cp_decimal(entry->size, number);
    if (!text_write(disc, manifest, number) || !text_write(disc, manifest, "\t")) return 0;
    cp_decimal(entry->extent, number);
    if (!text_write(disc, manifest, number) || !text_write(disc, manifest, "\t")) return 0;
    n = 0;
    while (index) { ancestry[n++] = index; index = disc->entries[index].parent; }
    for (i = n; i; --i) {
        p = (const unsigned char *)disc->entries[ancestry[i - 1]].original; j = 0;
        while (*p) { hex[j++] = digits[*p >> 4]; hex[j++] = digits[*p++ & 15]; }
        hex[j] = 0;
        if (!text_write(disc, manifest, hex) ||
            (i > 1 && !text_write(disc, manifest, "/"))) return 0;
    }
    return text_write(disc, manifest, "\n");
}

int cp_extract(cp_disc *disc, const char *destination)
{
    char root[CP_PATH], path[CP_PATH], close_error[CP_ERROR];
    unsigned i, take;
    cp_u64 done, offset, source_size;
    cp_file *manifest, *output;
    unsigned char *buffer;
    int ok;
    if (!cp_absolute(destination, root, disc->error) ||
        !cp_safe_ancestry(root, 1, disc->error)) return 0;
    if (cp_path_exists(root)) return fail(disc, "Destination already exists; choose a new folder.");
    if (strlen(root) + strlen(CP_MANIFEST) + 2 >= CP_PATH)
        return fail(disc, "Destination too long for manifest.");
    for (i = 1; i < disc->count; ++i)
        if (!cp_entry_path(disc, i, root, path)) return 0;
    buffer = (unsigned char *)malloc(CP_CHUNK);
    if (!buffer) return fail(disc, "Out of memory for streaming buffer.");
    if (!cp_make_directory(root, disc->error)) { free(buffer); return 0; }
    strcpy(path, root);
#if defined(_WIN32) || defined(__NT__)
    strcat(path, "\\");
#else
    strcat(path, "/");
#endif
    strcat(path, CP_MANIFEST);
    manifest = cp_file_open(path, 1, disc->error);
    if (!manifest) { free(buffer); return 0; }
    ok = text_write(disc, manifest, "# CuePull32 " CP_VERSION "\n# status=incomplete until final COMPLETE line\n# type\toutput_path\tbytes\tiso_extent\toriginal_path_components_hex\n");
    done = 0;
    for (i = 1; ok && i < disc->count; ++i) {
        cp_entry *entry;
        entry = &disc->entries[i];
        if (!cp_entry_path(disc, i, root, path) || !cp_safe_ancestry(path, 1, disc->error)) { ok = 0; break; }
        if (disc->progress && !disc->progress(disc->progress_user, done, disc->data_bytes, path)) {
            ok = fail(disc, "Cancelled; partial output retained with incomplete manifest."); break;
        }
        if (entry->directory) {
            ok = cp_make_directory(path, disc->error);
        } else {
            output = cp_file_open(path, 1, disc->error);
            if (!output) { ok = 0; break; }
            offset = 0;
            while (ok && offset < entry->size) {
                take = entry->size - offset > CP_CHUNK ? CP_CHUNK : (unsigned)(entry->size - offset);
                ok = logical_read(disc, (cp_u64)entry->extent * 2048 + offset, buffer, take) &&
                     cp_file_write(output, buffer, take, disc->error);
                if (!ok) break;
                offset += take; done += take;
                if (disc->progress && !disc->progress(disc->progress_user, done, disc->data_bytes, path))
                    ok = fail(disc, "Cancelled; partial output retained with incomplete manifest.");
            }
            if (!cp_file_close(output, close_error) && ok) ok = fail(disc, close_error);
        }
        if (ok) ok = manifest_row(disc, manifest, i);
    }
    if (ok && (!cp_file_size((cp_file *)disc->image, &source_size, disc->error) || source_size != disc->image_bytes))
        ok = fail(disc, "Source size changed during extraction.");
    if (ok) ok = text_write(disc, manifest, "# COMPLETE\n");
    if (!cp_file_close(manifest, close_error) && ok) ok = fail(disc, close_error);
    free(buffer); return ok;
}

/* Length + first/middle/last extent samples; deliberately not a full SHA scan. */
int cp_verify(cp_disc *disc, const char *destination)
{
    char root[CP_PATH], path[CP_PATH];
    unsigned i, sample, take, j;
    cp_u64 size, offsets[3];
    unsigned char source[2048], output[2048];
    cp_file *file;
    if (!cp_absolute(destination, root, disc->error)) return 0;
    for (i = 1; i < disc->count; ++i) {
        cp_entry *entry;
        entry = &disc->entries[i]; if (entry->directory) continue;
        if (!cp_entry_path(disc, i, root, path)) return 0;
        file = cp_file_open(path, 0, disc->error); if (!file) return 0;
        if (!cp_file_size(file, &size, disc->error) || size != entry->size) {
            cp_file_close(file, disc->error); return fail(disc, "Verification failed: output length differs.");
        }
        take = size < 2048 ? (unsigned)size : 2048;
        offsets[0] = 0; offsets[1] = (size - take) / 2; offsets[2] = size - take;
        for (sample = 0; sample < 3; ++sample) {
            for (j = 0; j < sample; ++j) if (offsets[j] == offsets[sample]) break;
            if (j < sample) continue;
            if (!logical_read(disc, (cp_u64)entry->extent * 2048 + offsets[sample], source, take) ||
                !cp_file_read(file, offsets[sample], output, take, disc->error) || memcmp(source, output, take)) {
                cp_file_close(file, disc->error); return fail(disc, "Verification failed: sampled content differs from source extent.");
            }
        }
        cp_file_close(file, disc->error);
    }
    return 1;
}
