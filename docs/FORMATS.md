# CuePull

[Download the ready source bundle](https://github.com/astrobleem/cuepull/releases/latest)
· [Platform tests](https://github.com/astrobleem/cuepull/actions/workflows/tests.yml)

Free, open-source CD image extraction. Choose an image, choose a new folder,
and extract. No drive mounting, accounts, ads, telemetry, or update nags.

CuePull reads BIN/CUE images directly and streams files to disk. If the CUE
sheet is missing, it can recover a confidently identifiable ISO data volume
from the BIN alone. The original images are opened read-only. Extracted
programs are never launched.

Python 3.10 or newer runs the same CLI on Windows, Linux, and macOS. The
optional GUI uses Tkinter. Python was chosen for portable deployment and the
established pure-Python PyCdlib filesystem parser; CuePull implements the CUE
and sector adapter instead of implementing another filesystem parser.

## Quick start

In the ready-to-run source ZIP, an unmodified PyCdlib dependency is bundled.
Unpack the folder and, on Windows, double-click **CuePull.cmd**. Choose a BIN
or CUE, accept or edit the suggested new output folder, and click **Extract**.
When a matching CUE exists beside a chosen BIN, the GUI uses it automatically.
No CUE is needed for a single confidently detected volume. Python with Tkinter
is required; the Windows python.org installer includes Tkinter.

From an unpacked ready bundle on any supported system:

```sh
python run.py gui
python run.py inspect /path/to/disc.cue
python run.py list /path/to/disc.cue
python run.py extract /path/to/disc.cue /path/to/disc-extracted
python run.py verify /path/to/disc.cue /path/to/disc-extracted
```

For a source checkout, install the exact parser version from official PyPI:

```sh
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: . .venv/bin/activate
python -m pip install --only-binary=:all: --require-hashes -r requirements.txt
python -m cuepull inspect /path/to/disc.cue
python -m cuepull gui
```

An optional `python -m pip install .` installs the `cuepull` command. On some
Linux distributions Tkinter is provided separately as a system Python Tk
package; the CLI works without it. Images are not mounted and administrator
access is not needed.

## Lost CUE sheet

```sh
python run.py inspect /path/to/disc.bin
python run.py extract /path/to/disc.bin /path/to/disc-extracted
```

Recovery scans the first **64 MiB** for ISO primary descriptors. It requires
sector alignment, valid raw headers where applicable, matching descriptor
endian fields, a descriptor terminator, volume/root bounds, and a filesystem
tree successfully parsed by PyCdlib. The detected volume is confined to the
size recorded in its primary descriptor. A signature by itself is insufficient.

The manifest labels recovered metadata as **inferred**. It records bytes
outside the detected volume as unclassified. Original track numbers, audio
boundaries, pregaps, and sessions cannot be reconstructed from this process.
Trailing audio is never treated as filesystem data. Audio-only/unknown images
receive a clear error. A lost CUE with several valid volumes requires a choice;
the error lists candidates. Advanced commands let you choose a layout/start or
increase the bounded scan:

```sh
python run.py inspect disc.bin --scan-mib 256
python run.py extract disc.bin recovered --layout MODE1/2352 --start-sector 150
```

`--start-sector` is a file-relative physical sector index, not a byte offset or
the BCD address in the raw sector header. `--layout` without a start uses zero.
Overrides still validate the filesystem. Scans can be 1–4096 MiB; only starts
aligned to the chosen sector width are supported. The ordinary GUI keeps these
options out of the way and directs ambiguous cases to the CLI.

## Supported formats

| Layout | Behavior |
|---|---|
| MODE1/2048 | Cooked 2048-byte data sectors |
| MODE1/2352 | Validates sync/mode; reads 2048 bytes at offset 16 |
| MODE2/2352 Form 1 | Validates sync/mode and duplicate XA subheaders; reads at offset 24 |
| MODE2/2336 Form 1 | Validates duplicate XA subheaders; reads at offset 8 |
| AUDIO | Track metadata preserved; audio samples are skipped |
| ISO 9660 | Regular contiguous files and directory trees |
| Joliet | Auto-selected when available; `--namespace iso` selects plain names |

CUE FILE BINARY sections, file-relative INDEX 01, stored INDEX 00 pregaps, and
virtual PREGAP/POSTGAP are distinguished. A following INDEX 00 marks the end of
the preceding track. A PREGAP adds no bytes to the source and does not change
INDEX offsets. Multiple same-width tracks can share a file. Separate local
files with different widths are supported through an explicit image whitelist.
More than one data track requires `--track NUMBER`.

```sh
python run.py extract disc.cue output --track 1 --image data.bin --image audio.bin
python run.py inspect disc.cue --json
python run.py list disc.cue --json
```

Implicit CUE references must match the CUE stem and refer to a regular sibling
BIN/IMG/ISO. Explicit `--image` options whitelist other intended sibling files.
Absolute paths, parent traversal, network references, symlinks/reparse points,
and arbitrary unrelated references are rejected. References are matched without
case sensitivity and ambiguous matches are rejected.

Unsupported: MODE2 Form 2, mixed physical widths inside one FILE, WAVE/MP3/CDG
tracks, multi-session address rebasing, multi-volume ISO sets, HFS/High Sierra,
UDF-only filesystems, Rock Ridge alternate names, symlinks, interleaved files,
extended-attribute data, and multi-extent files. Hybrid UDF/ISO media uses only
its ISO/Joliet tree. Sector EDC/ECC repair or validation is not implemented.
The raw header address is not used to invent session offsets. CUE session
directives are rejected; a CUE does not provide enough information to prove
that an image has only one session. Unsupported structures fail clearly.

## Output safety and verification

The destination must be a **new folder** whose parent exists. CuePull refuses
existing destinations and opens extracted files with exclusive creation.
It preflights the entire file plan before creating the destination. Traversal,
absolute/drive paths, alternate data streams, device names, and symlink/reparse
components are rejected. It never creates disc symlinks.
Use physical directory paths when a system exposes an alias through a
symlink (for example macOS `/var` and `/tmp` aliases); `realpath` shows the
physical path. This keeps the same link checks on every platform.

For portability, invalid Windows filename characters become underscores,
trailing dots/spaces are removed, long names are shortened with a stable hash,
and case-insensitive collisions receive numeric suffixes. ISO version suffixes
such as `;1` are removed. `cuepull-manifest.json` maps every original path and raw
identifier to its output name. It also records track layout, source sizes,
targeted fingerprints, file lengths, and SHA-256 hashes computed while streaming
the files. This is private extraction metadata, not part of the reusable source.

Progress reports file counts and bytes, including within large files. Reads
are bounded: file copying uses 512 KiB chunks and sector reads use batches of
256 sectors. PyCdlib retains directory metadata; this is not constant-memory
parsing of arbitrarily huge/malicious trees. Application limits include 8 MiB
per metadata read, 200,000 entries, and depth 64.

`verify` checks every file length and compares first/midpoint/last bytes directly
to its source extent. This targeted check is useful on slower hardware and is
not a complete content hash rescan. Extraction records first/middle/last source
fingerprints and checks sizes/mtimes before and after; source handles are
read-only. A full original-image hash scan is deliberately not required.

If interrupted or an error occurs, partial files and an `incomplete` manifest
remain for diagnosis. Choose a new destination to retry. Verification rejects
incomplete manifests. Keep the selected source and destination directories free
of concurrent changes; path checks do not promise protection against a separate
privileged process racing filesystem changes.

## Tests and contributing

```sh
python -m unittest discover -s tests -v
```

Tests synthesize their own media. They cover all supported sector layouts,
unaligned reads, track/gap offsets, malformed CUE/filesystem structures, recovery
ambiguity/false positives, traversal/device/case safety, overwrite refusal,
Joliet, symlink rejection, content verification, and source preservation.
Optional private integration tests use `CUEPULL_REAL_CUE` and
`CUEPULL_REAL_OUTPUT`; never upload those images, outputs, or manifests.

The GitHub Actions workflow tests Windows, Linux, and macOS with Python
3.10 and 3.12. See the platform test link above for current results. The GUI
test runs when Tk and a display are available; headless runners skip it.
The two private real-image tests are optional and are skipped in public CI.
No public repository or release is created by running CuePull.

To reproduce the ready bundle, download the pinned wheel and build into new
paths. `make_bundle.py` verifies its official PyPI SHA-256, copies an explicit
source-file allowlist, and includes unmodified parser source and its license.
ZIP members use fixed timestamps. Media formats and extraction manifests are
excluded from this source bundle.

```sh
python -m pip download --no-deps --only-binary=:all: --require-hashes -r requirements.txt -d /tmp/cuepull-wheels
python tools/make_bundle.py --wheel /tmp/cuepull-wheels/pycdlib-1.14.0-py2.py3-none-any.whl --destination /tmp/cuepull-ready --zip /tmp/cuepull-source.zip
```

CuePull builds on existing open-source CD tooling. It does not claim to be the
first BIN/CUE extractor. This project's focus is convenient, safe, direct file
extraction with a simple Windows entry point.

## License

CuePull is MIT licensed. PyCdlib is LGPL 2.1 and remains a separate dependency.
See [LICENSE](LICENSE) and [THIRD_PARTY.md](THIRD_PARTY.md).
