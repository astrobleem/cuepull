/* All source handles are read-only. All output files use exclusive create. */
#if !defined(_WIN32) && !defined(__NT__)
#define _FILE_OFFSET_BITS 64
#define _POSIX_C_SOURCE 200809L
#endif
#include "platform.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <ctype.h>
#if defined(_WIN32) || defined(__NT__)
#define CP_WINDOWS 1
#include <windows.h>
struct cp_file { HANDLE handle; };
#else
#include <unistd.h>
#include <fcntl.h>
#include <sys/types.h>
#include <sys/stat.h>
struct cp_file { int handle; };
#endif

static int fail(char *error, const char *message)
{
    strcpy(error, message);
    return 0;
}

#ifdef CP_WINDOWS
static int windows_component(const char *text, size_t length)
{
    char base[16];
    size_t i, n;
    if (!length || text[length - 1] == '.' || text[length - 1] == ' ')
        return 0;
    for (i = 0; i < length; ++i)
        if ((unsigned char)text[i] < 32 || strchr(":<>\"|?*", text[i])) return 0;
    n = 0;
    while (n < length && text[n] != '.' && n < sizeof(base) - 1) {
        unsigned c;
        c = (unsigned char)text[n];
        base[n] = (char)(c >= 'a' && c <= 'z' ? c - ('a' - 'A') : c); ++n;
    }
    base[n] = 0;
    if (!strcmp(base, "CON") || !strcmp(base, "NUL") || !strcmp(base, "PRN") ||
        !strcmp(base, "AUX") || !strcmp(base, "CLOCK$") ||
        !strcmp(base, "CONIN$") || !strcmp(base, "CONOUT$")) return 0;
    if (strlen(base) == 4 && (!strncmp(base, "COM", 3) || !strncmp(base, "LPT", 3)) &&
        base[3] >= '1' && base[3] <= '9') return 0;
    return 1;
}
#endif

int cp_absolute(const char *path, char *out, char *error)
{
    size_t length;
    if (!path || !*path || strlen(path) >= CP_PATH - 1)
        return fail(error, "Path is empty or exceeds the 259-byte limit.");
#ifdef CP_WINDOWS
    {
        DWORD n;
        if (!strncmp(path, "\\\\", 2) || strchr(path, '/'))
            return fail(error, "UNC/device paths and forward slashes are unsupported on Windows.");
        n = GetFullPathNameA(path, CP_PATH, out, NULL);
        if (!n || n >= CP_PATH || out[1] != ':' || out[2] != '\\')
            return fail(error, "Expected a local drive path shorter than MAX_PATH.");
        if (strchr(out + 2, ':'))
            return fail(error, "Alternate streams/device paths are forbidden.");
    }
#else
    if (path[0] == '/') strcpy(out, path);
    else {
        if (!getcwd(out, CP_PATH))
            return fail(error, "Cannot resolve current directory.");
        if (strlen(out) + strlen(path) + 2 >= CP_PATH)
            return fail(error, "Absolute path exceeds the 259-byte limit.");
        strcat(out, "/"); strcat(out, path);
    }
#endif
    length = strlen(out);
    while (length > 3 && (out[length - 1] == '/' || out[length - 1] == '\\'))
        out[--length] = 0;
    return 1;
}

/* Check every existing ancestor, including the file itself. Reject '..'. */
int cp_safe_ancestry(const char *path, int allow_missing, char *error)
{
    char part[CP_PATH];
    size_t i, from;
    int missing;
    if (strlen(path) >= CP_PATH) return fail(error, "Path too long.");
    strcpy(part, path);
#ifdef CP_WINDOWS
    from = 3;
#else
    from = 1;
#endif
    missing = 0;
    for (i = from; ; ++i) {
        char saved;
        size_t j;
        if (part[i] && part[i] != '/' && part[i] != '\\') continue;
        j = i;
        while (j > from && part[j - 1] != '/' && part[j - 1] != '\\') --j;
        if (i - j == 2 && part[j] == '.' && part[j + 1] == '.')
            return fail(error, "Parent traversal in a path is forbidden.");
#ifdef CP_WINDOWS
        if (!windows_component(part + j, i - j))
            return fail(error, "Invalid/trailing-dot/space/device component in Windows path.");
#endif
        saved = part[i]; part[i] = 0;
#ifdef CP_WINDOWS
        {
            DWORD attributes = GetFileAttributesA(part);
            if (attributes == INVALID_FILE_ATTRIBUTES) {
                DWORD code = GetLastError();
                if (code != ERROR_FILE_NOT_FOUND && code != ERROR_PATH_NOT_FOUND)
                    return fail(error, "Cannot inspect path ancestry.");
                missing = 1;
            } else {
                if (attributes & FILE_ATTRIBUTE_REPARSE_POINT)
                    return fail(error, "Symlinks/junctions/reparse points are forbidden.");
                if (missing || (saved && !(attributes & FILE_ATTRIBUTE_DIRECTORY)))
                    return fail(error, "A path ancestor is not a directory.");
            }
        }
#else
        {
            struct stat info;
            if (lstat(part, &info)) {
                if (errno != ENOENT) return fail(error, "Cannot inspect path ancestry.");
                missing = 1;
            } else {
                if (S_ISLNK(info.st_mode)) return fail(error, "Symlinks are forbidden.");
                if (missing || (saved && !S_ISDIR(info.st_mode)))
                    return fail(error, "A path ancestor is not a directory.");
            }
        }
#endif
        part[i] = saved;
        if (!saved) break;
    }
    if (missing && !allow_missing) return fail(error, "Source path does not exist.");
    return 1;
}

int cp_path_exists(const char *path)
{
#ifdef CP_WINDOWS
    return GetFileAttributesA(path) != INVALID_FILE_ATTRIBUTES;
#else
    struct stat info;
    return lstat(path, &info) == 0;
#endif
}

cp_file *cp_file_open(const char *path, int write, char *error)
{
    cp_file *file;
    if (!cp_safe_ancestry(path, write, error)) return NULL;
    file = (cp_file *)malloc(sizeof(*file));
    if (!file) { fail(error, "Out of memory opening file."); return NULL; }
#ifdef CP_WINDOWS
    file->handle = CreateFileA(path, write ? GENERIC_WRITE : GENERIC_READ,
        write ? 0 : FILE_SHARE_READ, NULL,
        write ? CREATE_NEW : OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (file->handle == INVALID_HANDLE_VALUE) {
        free(file); fail(error, write ? "Cannot create output; it may already exist."
                                      : "Cannot open source read-only."); return NULL;
    }
    {
        BY_HANDLE_FILE_INFORMATION info;
        if (GetFileType(file->handle) != FILE_TYPE_DISK ||
            !GetFileInformationByHandle(file->handle, &info) ||
            (info.dwFileAttributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT))) {
            CloseHandle(file->handle); free(file);
            fail(error, "Expected a regular local file."); return NULL;
        }
    }
#else
    {
        int flags;
        struct stat info;
        flags = write ? O_WRONLY | O_CREAT | O_EXCL : O_RDONLY;
#ifdef O_NOFOLLOW
        flags |= O_NOFOLLOW;
#endif
        file->handle = open(path, flags, 0600);
        if (file->handle < 0 || fstat(file->handle, &info) || !S_ISREG(info.st_mode)) {
            if (file->handle >= 0) close(file->handle);
            free(file); fail(error, "Cannot open a regular file exclusively/read-only.");
            return NULL;
        }
    }
#endif
    return file;
}

int cp_file_size(cp_file *file, cp_u64 *size, char *error)
{
#ifdef CP_WINDOWS
    DWORD low, high, code;
    SetLastError(NO_ERROR);
    low = GetFileSize(file->handle, &high); code = GetLastError();
    if (low == INVALID_FILE_SIZE && code != NO_ERROR)
        return fail(error, "Cannot determine source size.");
    *size = ((cp_u64)high << 32) | low;
#else
    struct stat info;
    if (fstat(file->handle, &info) || info.st_size < 0)
        return fail(error, "Cannot determine source size.");
    *size = (cp_u64)info.st_size;
#endif
    return 1;
}

int cp_file_read(cp_file *file, cp_u64 offset, void *buffer,
                 unsigned length, char *error)
{
#ifdef CP_WINDOWS
    LONG high;
    DWORD low, got;
    high = (LONG)(offset >> 32); SetLastError(NO_ERROR);
    low = SetFilePointer(file->handle, (LONG)(offset & 0xffffffffUL), &high, FILE_BEGIN);
    if (low == INVALID_SET_FILE_POINTER && GetLastError() != NO_ERROR)
        return fail(error, "Cannot seek source.");
    if (!ReadFile(file->handle, buffer, length, &got, NULL) || got != length)
        return fail(error, "Truncated file or read failure.");
#else
    unsigned done;
    ssize_t got;
    if (offset > (((cp_u64)1 << 63) - 1) ||
        lseek(file->handle, (off_t)offset, SEEK_SET) < 0)
        return fail(error, "Cannot seek source.");
    done = 0;
    while (done < length) {
        got = read(file->handle, (char *)buffer + done, length - done);
        if (got < 0 && errno == EINTR) continue;
        if (got <= 0) return fail(error, "Truncated file or read failure.");
        done += (unsigned)got;
    }
#endif
    return 1;
}

int cp_file_write(cp_file *file, const void *buffer, unsigned length, char *error)
{
#ifdef CP_WINDOWS
    DWORD wrote;
    if (!WriteFile(file->handle, buffer, length, &wrote, NULL) || wrote != length)
        return fail(error, "Output write failed (check available disk space).");
#else
    unsigned done;
    ssize_t wrote;
    done = 0;
    while (done < length) {
        wrote = write(file->handle, (const char *)buffer + done, length - done);
        if (wrote < 0 && errno == EINTR) continue;
        if (wrote <= 0) return fail(error, "Output write failed.");
        done += (unsigned)wrote;
    }
#endif
    return 1;
}

int cp_file_close(cp_file *file, char *error)
{
    int ok;
    if (!file) return 1;
#ifdef CP_WINDOWS
    ok = CloseHandle(file->handle) != 0;
#else
    ok = close(file->handle) == 0;
#endif
    free(file);
    return ok ? 1 : fail(error, "File close failed.");
}

int cp_make_directory(const char *path, char *error)
{
    if (!cp_safe_ancestry(path, 1, error)) return 0;
#ifdef CP_WINDOWS
    if (!CreateDirectoryA(path, NULL))
#else
    if (mkdir(path, 0700))
#endif
        return fail(error, "Cannot create new directory; existing outputs are refused.");
    return 1;
}
