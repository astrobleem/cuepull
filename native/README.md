# CuePull32 (private candidate)

CuePull32 is a small native C BIN/CUE extractor with a portable command line
and a classic Win32 interface. It reads image files directly, needs no Python
at runtime, never mounts a drive, and never runs extracted programs. The source
uses the parent project's MIT license. No media is included.

**Windows XP and Windows 98 are intended targets, not qualified platforms yet.**
The current candidate has been compiled with the already installed Open Watcom
1.9 and tested on Windows 10. A 32-bit PE and old API imports alone cannot prove
that it works on XP/98. Keep the existing modern CuePull v0.2.0 release unchanged
until actual guest or hardware tests pass. This is a separate native application;
it does not wrap the modern Python runtime.

## Use

On Windows, put `CuePull32.exe` wherever convenient and open it. Browse for a BIN,
CUE, ISO or IMG, or drop one image into its window, then click **Extract**. Leave
Output blank to create a sibling folder using the image's filename without its
extension. Existing automatic folders get ` (2)`, ` (3)`, etc. An explicit Output
must name a new folder whose parent already exists.

Dragging one BIN or CUE onto `CuePull32.exe` in Explorer starts extraction
automatically. In-window drops select the image and wait for Extract. A BIN is
scanned independently, even when a sibling CUE exists; choose the CUE to retain
known audio boundaries and track information.

```
cuepull32-cli inspect "C:\images\demo.cue"
cuepull32-cli list "C:\images\demo.bin"
cuepull32-cli extract "C:\images\demo.cue" "C:\output\demo"
cuepull32-cli extract "C:\images\demo.bin"
cuepull32-cli verify "C:\images\demo.cue" "C:\output\demo"
cuepull32-cli inspect "C:\images\demo.bin" --layout MODE1/2352 --start 150
```

The portable command line takes the same arguments on Linux/macOS. `verify`
checks every file length and first/middle/last samples against source extents,
not a full cryptographic content scan. Extraction runs that check automatically.
Progress goes to stderr. Errors have a nonzero exit status. GUI Cancel preserves
partial output with an incomplete manifest; choose a new folder to retry.

## Supported scope and limits

* Primary ISO9660, 2048-byte logical blocks, one volume, printable ASCII primary
  names, ordinary single-extent files/directories. Supplementary volumes are
  reported but their Joliet names are not used. Rock Ridge/SUSP is rejected.
* MODE1/2048, MODE1/2352, MODE2/2352 Form1, MODE2/2336 Form1. Raw sync/mode and
  duplicate MODE2 subheaders are checked; Form2 is rejected. EDC/ECC and BCD
  sector addresses are not verified.
* One matching-stem sibling BIN/ISO/IMG `FILE ... BINARY` in a CUE, up to 99 tracks, INDEX
  00/01 and virtual PREGAP. Stored INDEX00 bytes bound the previous track;
  PREGAP adds no bytes. Audio tracks are skipped. The one readable data volume
  must be unambiguous. Case-exact FILE spelling is needed on case-sensitive
  hosts. No external reference whitelist is provided in this first candidate.
* Without CUE, scan the first 64 MiB for descriptor signatures in the four
  layouts and corroborate candidates with a complete bounded directory walk.
  Multiple valid volumes require explicit `--layout` and `--start`. Track/audio
  boundaries cannot be recovered from BIN alone. The GUI has no override fields;
  use the CLI for an ambiguous image.
* Opaque non-SUSP system-use vendor bytes are ignored and never interpreted as
  links or alternate extents. This supports the older metadata on TANDYDEMO2.
  Two directories on that disc have stale smaller `..` length fields; the native
  walk requires the correct parent extent, accepts a smaller bounded redundant
  length, and always uses the parent record's authoritative size.
* Unsupported: multisession rebasing, multiple FILE CUEs, POSTGAP, indexes above
  01, UDF-only/HFS images, associated files, extended-attribute records,
  interleaving, multi-extent files, directory aliases/cycles, SUSP/Rock Ridge
  links/relocation, supplementary name parity, non-ASCII ISO primary names.
* Bounds: 16,384 entries including root, 32 levels, 8 MiB per directory, 100
  characters per normalized name, 259 bytes per full path. At most one directory
  buffer is live. File content streams through 64 KiB and one physical sector;
  the disc is never loaded into RAM. Checked 64-bit positions handle large image
  offsets. Files must fit the destination filesystem; FAT32 cannot hold a file
  of 4 GiB or more.

This intentionally small ECMA-119 reader is not a general filesystem parser.
It was chosen for the installed C89-era compiler and legacy runtime target.
The modern app continues to use the independent openly licensed PyCdlib parser
for broader ISO/Joliet support. The tests include a PyCdlib-produced oracle
fixture and real-disc differential checks; no third-party parser code was
copied into this C implementation.

## Output safety and naming

Extraction inventories and validates the full supported tree, allocates names
and checks all destination path lengths before creating output. It rejects
absolute/component traversal, separators, streams, NUL/control characters and
Windows device names. File version suffixes are removed, Windows-invalid
printable characters become `_`, trailing dots/spaces are stripped, long names
are shortened, and ASCII case collisions get `--2`, `--3` suffixes. Files,
directories and the reserved manifest name share the collision namespace.

`cuepull32-manifest.tsv` records type, normalized relative path, file bytes,
ISO extent and the full original path as slash-separated hex-encoded primary
name components. This preserves names even when normalization is needed. The
final `# COMPLETE` line is written only after all file writes and source-size
checks succeed. Verification errors after that line are reported separately;
the line confirms extraction writes, not a full integrity proof.

Only a new destination is accepted; every file uses exclusive create. Existing
symlinks and Windows reparse points in source/output ancestry are rejected and
ancestry is rechecked during extraction. Do not let another process modify the
destination tree during extraction: this path-based legacy API implementation
does not claim protection against a malicious concurrent directory replacement.
Windows source handles share reads only, preventing ordinary concurrent writes.

The Win32 candidate deliberately uses ANSI APIs available to older systems.
Input/output paths must be representable in the active Windows code page.
It does not promise full Unicode, long-path, UNC/device-path or network support.
ASCII disc names remain identical across supported hosts.

## Build

No installed third-party parser or GUI library is required. Linux/macOS:

```
cd native
make
python3 tests/test_native.py --exe ./cuepull32-cli
```

Use a C99 compiler for a standard `unsigned long long`; the implementation style
is C89. On Open Watcom/MSVC it uses their `unsigned __int64` extension. The
platform boundary isolates Win32 file APIs from POSIX `lstat/open/lseek` APIs.
For Windows with Open Watcom 1.9 already installed:

```
cd native
set WATCOM=C:\WATCOM
build-watcom.cmd
python tests\test_native.py --exe build\cuepull32-cli.exe
```

Builds use a static CRT. `build/` is private ignored output. Do not silently
substitute a modern UCRT or MinGW runtime and call the result Win98-compatible.
Audit executable imports and test on actual guests after any toolchain change.
CuePull32's independently authored source is MIT. The Watcom-built executable
also contains unmodified compiler C runtime/startup code under separate terms;
see [THIRD_PARTY.md](THIRD_PARTY.md) and the included Watcom license/source link.
The frontend uses ANSI APIs, while the compiler runtime also imports several
wide-character API entry points. Guest qualification must check those runtime
paths as well as the application code.

## Qualification before a legacy release

Run the candidate on actual Windows 98 SE and XP SP3 guests or hardware, using
only synthetic fixture discs: launch/file picker, in-window and Explorer drops,
all layouts and nonzero starts, no-CUE recovery, progress/cancel, disk-full/write
errors, existing-folder refusal, case/device-name safety, and byte comparison.
On XP also exercise junction rejection; on 98 verify FAT32 and active-code-page
paths. Record OS/service pack, compiler, binary hashes, tests and genuine app
screenshots. Never publish the user's disc or use downloaded unlicensed OS media.

Specification: [ECMA-119](https://ecma-international.org/publications-and-standards/standards/ecma-119/).
Parser oracle: [PyCdlib](https://github.com/clalancette/pycdlib), LGPL-2.1.
Build tool: [Open Watcom](https://openwatcom.org/).
