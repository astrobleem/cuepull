#ifndef CP_PLATFORM_H
#define CP_PLATFORM_H
#include "cuepull.h"
typedef struct cp_file cp_file;
cp_file *cp_file_open(const char *, int, char *);
int cp_file_size(cp_file *, cp_u64 *, char *);
int cp_file_read(cp_file *, cp_u64, void *, unsigned, char *);
int cp_file_write(cp_file *, const void *, unsigned, char *);
int cp_file_close(cp_file *, char *);
int cp_absolute(const char *, char *, char *);
int cp_safe_ancestry(const char *, int, char *);
int cp_path_exists(const char *);
int cp_make_directory(const char *, char *);
#endif
