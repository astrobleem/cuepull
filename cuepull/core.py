"""CUE interpretation and raw-sector adaptation; ISO parsing uses PyCdlib.

All image handles are opened read-only. CUE timestamps are file-relative CD
frames, not the BCD addresses in raw sector headers. PREGAP creates no bytes.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import stat
import struct
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Callable

import pycdlib

SYNC = b"\x00" + b"\xff" * 10 + b"\x00"
MODES = {"MODE1/2048": 2048, "MODE1/2352": 2352,
         "MODE2/2352": 2352, "MODE2/2336": 2336, "AUDIO": 2352}
CHUNK = 512 * 1024
MAX_READ = 8 * 1024 * 1024
MANIFEST = "cuepull-manifest.json"


class CuePullError(Exception):
    """An actionable image, format, or destination error."""


class ExtractionCancelled(CuePullError):
    """Cooperative cancellation leaves the manifest incomplete."""


def is_link(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & 0x400)


def reject_links(path: Path) -> None:
    for part in (path, *path.parents):
        if part.exists() or part.is_symlink():
            if is_link(part):
                raise CuePullError(f"Symlink/reparse point is not allowed: {part}")


def frames(value: str) -> int:
    match = re.fullmatch(r"(\d+):(\d{2}):(\d{2})", value)
    if not match:
        raise CuePullError(f"Invalid CUE time: {value}")
    m, s, f = map(int, match.groups())
    if s >= 60 or f >= 75:
        raise CuePullError(f"Invalid CUE time: {value}")
    return (m * 60 + s) * 75 + f


@dataclass
class Track:
    number: int
    mode: str
    image: Path
    indexes: dict[int, int] = field(default_factory=dict)
    pregap: int = 0
    postgap: int = 0
    end: int = 0

    @property
    def sector_size(self) -> int:
        return MODES[self.mode]

    @property
    def start(self) -> int:
        return self.indexes[1]

    @property
    def sectors(self) -> int:
        return self.end - self.start

    def summary(self) -> dict:
        return {"track": self.number, "mode": self.mode,
                "image": self.image.name, "image_bytes": self.image.stat().st_size,
                "sector_bytes": self.sector_size, "indexes": self.indexes,
                "index01_frame": self.start, "end_frame_exclusive": self.end,
                "file_offset_bytes": self.start * self.sector_size,
                "stored_sectors": self.sectors,
                "virtual_pregap_frames": self.pregap,
                "virtual_postgap_frames": self.postgap}


def parse_cue(cue: Path, images: list[Path] | None = None) -> list[Track]:
    """Only matching-stem local images are implicit; explicit images whitelist.

    FILE references must be plain sibling filenames, even when whitelisted.
    Same-file tracks of different physical widths are intentionally rejected.
    """
    cue = Path(cue).absolute()
    reject_links(cue)
    if not cue.is_file() or cue.stat().st_size > 1024 * 1024:
        raise CuePullError("CUE must be a local file of at most 1 MiB")
    try:
        text = cue.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = cue.read_text(encoding="cp1252")
    allowed = {}
    for image in images or []:
        image = Path(image).absolute()
        reject_links(image)
        if image.parent.resolve() != cue.parent.resolve() or not image.is_file():
            raise CuePullError("Explicit image must be a regular CUE sibling file")
        if image.name.casefold() in allowed:
            raise CuePullError("Duplicate explicit image")
        allowed[image.name.casefold()] = image
    tracks: list[Track] = []
    current_image = None
    track = None
    used_images = set()
    for line_no, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        command = line.split()[0].upper()
        if command == "REM":
            if re.search(r"\bSESSION\b", line, re.I):
                raise CuePullError("Multi-session CUEs are not supported")
            continue
        if command in {"TITLE", "PERFORMER", "SONGWRITER", "CATALOG",
                       "ISRC", "FLAGS"}:
            continue
        try:
            if command == "FILE":
                match = re.fullmatch(r'FILE\s+(?:"([^"\r\n]+)"|(\S+))\s+BINARY',
                                     line, re.I)
                if not match:
                    raise CuePullError("Only FILE ... BINARY is supported")
                name = match.group(1) or match.group(2)
                validate_component(name)
                if not name.lower().endswith((".bin", ".img", ".iso")):
                    raise CuePullError("Image must have .bin, .img, or .iso extension")
                if images:
                    current_image = allowed.get(name.casefold())
                    if current_image is None:
                        raise CuePullError(f"FILE is not in --image whitelist: {name}")
                else:
                    if Path(name).stem.casefold() != cue.stem.casefold():
                        raise CuePullError("Unrelated FILE reference; select it with --image")
                    matches = [p for p in cue.parent.iterdir()
                               if p.name.casefold() == name.casefold()]
                    if len(matches) != 1:
                        raise CuePullError(f"Missing or ambiguous image: {name}")
                    current_image = matches[0]
                reject_links(current_image)
                if not current_image.is_file():
                    raise CuePullError("Image is not a regular file")
                if current_image in used_images:
                    raise CuePullError("Repeated FILE sections are not supported")
                used_images.add(current_image)
                track = None
            elif command == "TRACK":
                match = re.fullmatch(r"TRACK\s+(\d{2})\s+(\S+)", line, re.I)
                if not match or current_image is None:
                    raise CuePullError("TRACK requires a preceding FILE")
                number, mode = int(match.group(1)), match.group(2).upper()
                if not 1 <= number <= 99 or (tracks and number <= tracks[-1].number):
                    raise CuePullError("Track numbers must increase within 1..99")
                if mode not in MODES:
                    raise CuePullError(f"Unsupported track mode: {mode}")
                track = Track(number, mode, current_image)
                tracks.append(track)
            elif command == "INDEX":
                match = re.fullmatch(r"INDEX\s+(\d{2})\s+(\S+)", line, re.I)
                if not match or track is None:
                    raise CuePullError("INDEX requires a TRACK")
                index = int(match.group(1))
                if index in track.indexes or (track.indexes and index <= max(track.indexes)):
                    raise CuePullError("Duplicate or unordered INDEX")
                value = frames(match.group(2))
                if track.indexes and value < max(track.indexes.values()):
                    raise CuePullError("INDEX times must increase")
                track.indexes[index] = value
            elif command in {"PREGAP", "POSTGAP"}:
                parts = line.split()
                if track is None or len(parts) != 2:
                    raise CuePullError("Gap requires TRACK and one time")
                attr = command.lower()
                if getattr(track, attr):
                    raise CuePullError("Duplicate gap")
                setattr(track, attr, frames(parts[1]))
            else:
                raise CuePullError(f"Unsupported CUE directive: {command}")
        except CuePullError as exc:
            raise CuePullError(f"CUE line {line_no}: {exc}") from exc
    if not tracks:
        raise CuePullError("CUE has no tracks")
    for i, track in enumerate(tracks):
        if 1 not in track.indexes:
            raise CuePullError(f"Track {track.number} has no INDEX 01")
        if track.pregap and 0 in track.indexes:
            raise CuePullError("PREGAP together with stored INDEX 00 is ambiguous")
        widths = {t.sector_size for t in tracks if t.image == track.image}
        if len(widths) != 1:
            raise CuePullError("Different physical sector sizes in one FILE are unsupported")
        size = track.image.stat().st_size
        if size % track.sector_size:
            raise CuePullError(f"Image has a partial final sector: {track.image.name}")
        following = tracks[i + 1] if i + 1 < len(tracks) else None
        track.end = (following.indexes.get(0, following.indexes.get(1, -1))
                     if following and following.image == track.image
                     else size // track.sector_size)
        if not 0 <= track.start < track.end <= size // track.sector_size:
            raise CuePullError(f"Track {track.number}: out-of-range/overlapping indexes")
        if max(track.indexes.values()) >= track.end:
            raise CuePullError(f"Track {track.number}: INDEX outside track")
    return tracks


class SectorStream(io.RawIOBase):
    """Bounded seekable 2048-byte logical stream over a single data track."""

    def __init__(self, track: Track):
        super().__init__()
        if track.mode == "AUDIO":
            raise CuePullError("An audio track has no ISO filesystem sectors")
        reject_links(track.image)
        self.track = track
        self._file = track.image.open("rb")
        self.name = str(track.image)
        self.length = track.sectors * 2048
        self.position = 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=0):
        self._checkClosed()
        bases = {0: 0, 1: self.position, 2: self.length}
        if whence not in bases or bases[whence] + offset < 0:
            raise CuePullError("Invalid logical seek")
        self.position = bases[whence] + offset
        return self.position

    def read(self, size=-1):
        self._checkClosed()
        remaining = max(0, self.length - self.position)
        size = remaining if size is None or size < 0 else min(size, remaining)
        if size > MAX_READ:
            raise CuePullError("Read exceeds 8 MiB metadata limit; use chunked reads")
        if not size:
            return b""
        first, head = divmod(self.position, 2048)
        count = (head + size + 2047) // 2048
        output = bytearray()
        while count:
            n = min(256, count)
            physical = self.track.start + first
            width = self.track.sector_size
            self._file.seek(physical * width)
            raw = self._file.read(n * width)
            if len(raw) != n * width:
                raise CuePullError(f"Truncated sector at image frame {physical}")
            for i in range(n):
                sector = raw[i * width:(i + 1) * width]
                output.extend(self._payload(sector, physical + i))
            count -= n
            first += n
        self.position += size
        return bytes(output[head:head + size])

    def readinto(self, buffer):
        data = self.read(len(buffer))
        buffer[:len(data)] = data
        return len(data)

    def _payload(self, sector, frame):
        mode = self.track.mode
        if mode == "MODE1/2048":
            return sector
        if mode != "MODE2/2336":
            if sector[:12] != SYNC:
                raise CuePullError(f"Invalid raw-sector sync at frame {frame}")
            expected = 1 if mode == "MODE1/2352" else 2
            if sector[15] != expected:
                raise CuePullError(f"Wrong sector mode at frame {frame}")
        if mode == "MODE1/2352":
            return sector[16:2064]
        offset = 16 if mode == "MODE2/2352" else 0
        if sector[offset:offset + 4] != sector[offset + 4:offset + 8]:
            raise CuePullError(f"Mismatched MODE2 subheaders at frame {frame}")
        if sector[offset + 2] & 0x20:
            raise CuePullError(f"MODE2 Form 2 is not a filesystem sector (frame {frame})")
        return sector[offset + 8:offset + 2056]

    def close(self):
        if not self.closed:
            self._file.close()
        super().close()


def both_endian(data: bytes) -> int:
    half = len(data) // 2
    little = int.from_bytes(data[:half], "little")
    if little != int.from_bytes(data[half:], "big"):
        raise CuePullError("ISO descriptor has mismatched endian fields")
    return little


def descriptor(stream: SectorStream) -> dict:
    stream.seek(16 * 2048)
    primary = None
    types = []
    for _ in range(128):
        block = stream.read(2048)
        if len(block) != 2048 or block[1:6] != b"CD001" or block[6] != 1:
            raise CuePullError("No supported ISO 9660 descriptor set at track LBA 16")
        types.append(block[0])
        if block[0] == 1:
            if primary is not None:
                raise CuePullError("Multiple primary volumes are unsupported")
            primary = block
        if block[0] == 255:
            break
    else:
        raise CuePullError("Unterminated ISO descriptor set")
    if primary is None:
        raise CuePullError("ISO primary volume descriptor is missing")
    blocks = both_endian(primary[80:88])
    block_size = both_endian(primary[128:132])
    if block_size != 2048 or not 18 <= blocks <= stream.track.sectors:
        raise CuePullError("Invalid ISO volume size or unsupported logical block size")
    if both_endian(primary[120:124]) != 1 or both_endian(primary[124:128]) != 1:
        raise CuePullError("Multi-volume ISO sets are unsupported")
    root_extent = both_endian(primary[158:166])
    root_bytes = both_endian(primary[166:174])
    if root_extent * 2048 + root_bytes > blocks * 2048:
        raise CuePullError("Root directory extends outside the ISO volume")
    return {"filesystem": "ISO 9660", "volume_id": primary[40:72].decode(
        "ascii", "replace").rstrip(), "volume_blocks": blocks,
        "logical_block_bytes": block_size, "volume_bytes": blocks * 2048,
        "descriptor_types": types, "root_extent": root_extent,
        "root_bytes": root_bytes}


DEVICE = re.compile(r"^(?:CON|PRN|AUX|NUL|CLOCK\$|CONIN\$|CONOUT\$|"
                    r"COM[1-9\u00b9\u00b2\u00b3]|LPT[1-9\u00b9\u00b2\u00b3])$", re.I)


def validate_component(name: str) -> None:
    if (not name or name in {".", ".."} or any(c in name for c in "/\\:\0")
            or PureWindowsPath(name).is_absolute() or PureWindowsPath(name).drive):
        raise CuePullError(f"Unsafe path component: {name!r}")
    base = name.rstrip(" .").split(".")[0].rstrip(" ")
    if DEVICE.fullmatch(base):
        raise CuePullError(f"Windows device name is not allowed: {name!r}")


def normalized_name(name: str, iso_version: bool = False) -> str:
    validate_component(name)
    if iso_version:
        name = re.sub(r";\d+$", "", name)
    name = unicodedata.normalize("NFC", name)
    name = "".join("_" if ord(c) < 32 or c in '<>"|?*' else c for c in name)
    name = name.rstrip(" .")
    if not name:
        name = "_unnamed"
    if len(name) > 100:
        digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]
        name = name[:80] + "--" + digest
    validate_component(name)
    return name


class NameAllocator:
    def __init__(self, reserved=()):
        self.used = {n.casefold() for n in reserved}

    def allocate(self, name: str) -> str:
        candidate = name
        suffix = 2
        while candidate.casefold() in self.used:
            stem, dot, ext = name.rpartition(".")
            candidate = (f"{stem}--{suffix}.{ext}" if dot and stem
                         else f"{name}--{suffix}")
            suffix += 1
        self.used.add(candidate.casefold())
        return candidate


@dataclass
class Entry:
    source: str
    source_name_hex: str
    output: tuple[str, ...]
    directory: bool
    size: int
    extent: int

    def summary(self):
        return {"source_path": self.source, "source_name_hex": self.source_name_hex,
                "output_path": "/".join(self.output), "directory": self.directory,
                "bytes": self.size, "extent_lba": self.extent}


class AmbiguousImageError(CuePullError):
    def __init__(self, candidates):
        self.candidates = candidates
        details = "; ".join(f"{c['mode']} start sector {c['start_sector']}"
                            for c in candidates)
        super().__init__(f"Several valid volumes found: {details}. Select --layout and --start-sector.")


class Disc:
    def __init__(self, cue: Path, track_number=None, images=None, namespace="auto",
                 layout=None, start_sector=None, scan_mib=64, _tracks=None):
        self.cue = Path(cue).absolute()
        self.inferred = self.cue.suffix.lower() != ".cue"
        self.tracks = (_tracks if _tracks is not None else
                       recover_tracks(self.cue, layout, start_sector, scan_mib)
                       if self.inferred else parse_cue(self.cue, images))
        if not self.inferred and (layout is not None or start_sector is not None):
            raise CuePullError("Layout/start overrides apply only to BIN/ISO recovery")
        data_tracks = [t for t in self.tracks if t.mode != "AUDIO"]
        if track_number is None:
            if len(data_tracks) != 1:
                raise CuePullError("Select one data track using --track NUMBER")
            self.track = data_tracks[0]
        else:
            matching = [t for t in data_tracks if t.number == track_number]
            if not matching:
                raise CuePullError("Selected track is absent or is audio")
            self.track = matching[0]
        self.stream = SectorStream(self.track)
        self.iso = pycdlib.PyCdlib()
        try:
            self.info = descriptor(self.stream)
            self.stream.seek(0)
            self.iso.open_fp(self.stream)
            has_joliet = self.iso.has_joliet()
            self.namespace = ("joliet" if has_joliet else "iso") if namespace == "auto" else namespace
            if self.namespace == "joliet" and not has_joliet:
                raise CuePullError("This volume has no Joliet directory tree")
            if self.namespace not in {"iso", "joliet"}:
                raise CuePullError("Namespace must be auto, iso, or joliet")
            self.info.update({"namespace": self.namespace,
                              "has_joliet": has_joliet,
                              "has_rock_ridge": self.iso.has_rock_ridge(),
                              "has_udf": self.iso.has_udf()})
            self.entries = self._inventory()
        except BaseException:
            self.close()
            raise

    def path_arg(self, path):
        return {self.namespace + "_path": path}

    def _inventory(self):
        entries = []
        pending = [("/", (), 0)]
        seen_dirs = set()
        count = 0
        while pending:
            source, output, depth = pending.pop()
            if depth > 64:
                raise CuePullError("Directory depth exceeds 64")
            allocator = NameAllocator([MANIFEST] if not output else [])
            seen_names = set()
            for rec in self.iso.list_children(**self.path_arg(source)):
                if rec.is_dot() or rec.is_dotdot():
                    continue
                count += 1
                if count > 200000:
                    raise CuePullError("Directory entry limit (200000) exceeded")
                raw = rec.file_identifier()
                if raw in seen_names:
                    raise CuePullError("Duplicate directory identifiers are ambiguous")
                seen_names.add(raw)
                encoding = "utf-16-be" if self.namespace == "joliet" else "ascii"
                try:
                    name = raw.decode(encoding)
                except UnicodeDecodeError as exc:
                    raise CuePullError("Undecodable directory name; select another namespace") from exc
                validate_component(name)
                if rec.is_symlink():
                    raise CuePullError(f"Symlinks are unsupported: {source}{name}")
                if (rec.file_unit_size or rec.interleave_gap_size or rec.xattr_len
                        or rec.file_flags & 0x80 or rec.data_continuation is not None):
                    raise CuePullError(f"Interleaved, extended-attribute, or multi-extent entry: {name}")
                extent = rec.extent_location()
                size = rec.data_length
                if extent < 0 or size < 0 or extent * 2048 + size > self.info["volume_bytes"]:
                    raise CuePullError(f"Entry extends outside volume: {name}")
                out_name = allocator.allocate(normalized_name(name, self.namespace == "iso"))
                child_source = source.rstrip("/") + "/" + name
                child_output = output + (out_name,)
                if rec.is_dir():
                    identity = (extent, size)
                    if identity in seen_dirs:
                        raise CuePullError("Repeated/cyclic directory extents are unsupported")
                    seen_dirs.add(identity)
                    pending.append((child_source, child_output, depth + 1))
                elif not rec.is_file():
                    raise CuePullError(f"Unsupported non-regular entry: {name}")
                entries.append(Entry(child_source, raw.hex(), child_output,
                                     rec.is_dir(), size if not rec.is_dir() else 0, extent))
        return entries

    def summary(self):
        files = [e for e in self.entries if not e.directory]
        return {**self.info, "metadata_source": "inferred" if self.inferred else "cue",
                "selected_track": self.track.number,
                "file_count": len(files), "directory_count": len(self.entries) - len(files),
                "data_bytes": sum(e.size for e in files),
                "tracks": [t.summary() for t in self.tracks],
                "skipped_audio_tracks": [t.number for t in self.tracks if t.mode == "AUDIO"],
                "unclassified_bytes_outside_volume": (self.cue.stat().st_size -
                    self.track.sectors * self.track.sector_size) if self.inferred else None,
                "limitations": ["Audio is not extracted", "No multi-session address rebasing",
                                "No raw sector EDC/ECC validation", "Rock Ridge alternate names and UDF trees are not extracted"] +
                               (["Missing CUE: volume span inferred; original track numbers, audio boundaries and pregaps are unknown"] if self.inferred else [])}

    def close(self):
        try:
            self.iso.close()
        except pycdlib.pycdlibexception.PyCdlibException:
            pass
        finally:
            self.stream.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def recover_tracks(image: Path, layout=None, start_sector=None, scan_mib=64) -> list[Track]:
    """Find corroborated ISO volumes without claiming to recover a lost TOC.

    Scan at most scan_mib MiB for descriptor signatures, then corroborate
    layout, descriptor terminator, volume/root bounds and PyCdlib's tree.
    Only sector-aligned volume starts are supported. Explicit overrides still
    require a valid volume and tree; they do not disable validation.
    """
    image = Path(image).absolute()
    reject_links(image)
    if not image.is_file():
        raise CuePullError("Select an existing BIN, IMG, ISO, or CUE file")
    if not 1 <= scan_mib <= 4096:
        raise CuePullError("--scan-mib must be within 1..4096")
    if layout is not None and layout not in MODES.keys() - {"AUDIO"}:
        raise CuePullError("Unsupported recovery layout")
    if start_sector is not None and (start_sector < 0 or layout is None):
        raise CuePullError("--start-sector requires --layout and a nonnegative value")
    proposals = set()
    if layout is not None:
        proposals.add((layout, start_sector or 0))
    else:
        limit = min(image.stat().st_size, scan_mib * 1024 * 1024)
        signature = b"\x01CD001\x01"
        payload_offsets = {"MODE1/2048": 0, "MODE1/2352": 16,
                           "MODE2/2352": 24, "MODE2/2336": 8}
        with image.open("rb") as fp:
            position = 0
            tail = b""
            while position < limit:
                block = fp.read(min(CHUNK, limit - position))
                joined = tail + block
                at = joined.find(signature)
                while at >= 0:
                    absolute = position - len(tail) + at
                    for mode, payload in payload_offsets.items():
                        start_bytes = absolute - (16 * MODES[mode] + payload)
                        if start_bytes >= 0 and start_bytes % MODES[mode] == 0:
                            proposals.add((mode, start_bytes // MODES[mode]))
                    if len(proposals) > 128:
                        raise CuePullError("Too many descriptor candidates; use an explicit --layout/start-sector")
                    at = joined.find(signature, at + 1)
                position += len(block)
                tail = joined[-6:]
    valid = []
    for mode, start in sorted(proposals):
        track = Track(1, mode, image, {1: start}, end=image.stat().st_size // MODES[mode])
        if track.sectors < 18:
            continue
        try:
            with SectorStream(track) as stream:
                info = descriptor(stream)
                for lba in (0, 1, 15, info["root_extent"]):
                    stream.seek(lba * 2048)
                    if len(stream.read(2048)) != 2048:
                        raise CuePullError("Incomplete volume sample")
            track.end = start + info["volume_blocks"]
            with Disc(image, _tracks=[track], namespace="iso") as candidate:
                # Ensure the root actually parsed, including its mandatory dot entries.
                root = candidate.iso.get_record(iso_path="/")
                if not root.children or not any(r.is_dot() for r in root.children):
                    raise CuePullError("Invalid root directory")
                valid.append((track, info))
        except (CuePullError, pycdlib.pycdlibexception.PyCdlibException,
                ValueError, IndexError, UnicodeError, struct.error):
            continue
    if not valid:
        raise CuePullError(f"No supported ISO volume found in the first {scan_mib} MiB. "
                           "Try --scan-mib, or --layout and --start-sector; audio-only and unknown files cannot be extracted.")
    if len(valid) > 1:
        raise AmbiguousImageError([{"mode": t.mode, "start_sector": t.start,
                                   "volume_id": info["volume_id"]} for t, info in valid])
    return [valid[0][0]]


def safe_output(root: Path, parts: tuple[str, ...]) -> Path:
    for part in parts:
        validate_component(part)
    target = root.joinpath(*parts)
    reject_links(target)
    if not target.resolve().is_relative_to(root.resolve()):
        raise CuePullError("Output escapes the selected destination")
    return target


def source_fingerprint(paths: list[Path]) -> list[dict]:
    """Small read-only fingerprints, deliberately not a full-disc hash scan."""
    result = []
    for path in dict.fromkeys(paths):
        before = path.stat()
        size = before.st_size
        positions = sorted({0, max(0, size // 2 - 32768), max(0, size - 65536)})
        samples = []
        with path.open("rb") as fp:
            for offset in positions:
                fp.seek(offset)
                data = fp.read(65536)
                samples.append({"offset": offset, "bytes": len(data),
                                "sha256": hashlib.sha256(data).hexdigest()})
        result.append({"path": str(path), "bytes": size,
                       "mtime_ns": before.st_mtime_ns, "samples": samples})
    return result


def extract(disc: Disc, destination: Path, progress: Callable | None = None,
            cancelled: Callable | None = None) -> dict:
    destination = Path(destination).absolute()
    def check_cancelled():
        if cancelled and cancelled():
            raise ExtractionCancelled("Extraction cancelled; partial output remains.")
    check_cancelled()
    reject_links(destination)
    if destination.exists():
        raise CuePullError(f"Destination already exists; choose a new folder: {destination}")
    if not destination.parent.is_dir():
        raise CuePullError("Destination parent must already exist")
    if destination.parent.resolve() == disc.cue.parent.resolve() and destination.name.casefold() in {
            disc.cue.name.casefold(), *(t.image.name.casefold() for t in disc.tracks)}:
        raise CuePullError("Destination conflicts with a source file")
    # Validate the entire plan before creating anything.
    for entry in disc.entries:
        safe_output(destination, entry.output)
    paths = [disc.cue, *(t.image for t in disc.tracks)]
    before = source_fingerprint(paths)
    summary = disc.summary()
    manifest = {"format": "cuepull-manifest-v1", "status": "incomplete",
                "disc": summary, "source_before": before, "files": [],
                "name_map": [e.summary() for e in disc.entries]}
    destination.mkdir()
    manifest_path = safe_output(destination, (MANIFEST,))
    with manifest_path.open("x", encoding="utf-8") as fp:
        json.dump(manifest, fp, ensure_ascii=False, indent=2)
    for entry in sorted((e for e in disc.entries if e.directory), key=lambda e: len(e.output)):
        check_cancelled()
        safe_output(destination, entry.output).mkdir()
    completed_bytes = 0
    completed_files = 0
    for entry in (e for e in disc.entries if not e.directory):
        check_cancelled()
        path = safe_output(destination, entry.output)
        digest = hashlib.sha256()
        written = 0
        with path.open("xb") as output, disc.iso.open_file_from_iso(
                **disc.path_arg(entry.source)) as source:
            while written < entry.size:
                check_cancelled()
                data = source.read(min(CHUNK, entry.size - written))
                if not data:
                    raise CuePullError(f"Short file read: {entry.source}")
                output.write(data)
                digest.update(data)
                written += len(data)
                completed_bytes += len(data)
                if progress:
                    progress(completed_files, completed_bytes, summary, entry.source)
        if path.stat().st_size != entry.size:
            raise CuePullError(f"Length mismatch: {entry.source}")
        completed_files += 1
        manifest["files"].append({**entry.summary(), "sha256": digest.hexdigest()})
        if progress:
            progress(completed_files, completed_bytes, summary, entry.source)
    after = source_fingerprint(paths)
    check_cancelled()
    if before != after:
        raise CuePullError("Source size/mtime or targeted fingerprints changed during extraction")
    manifest.update({"status": "complete", "source_after": after,
                     "completed_files": completed_files, "completed_bytes": completed_bytes})
    reject_links(manifest_path)
    with manifest_path.open("w", encoding="utf-8") as fp:
        json.dump(manifest, fp, ensure_ascii=False, indent=2)
    return manifest


def verify(disc: Disc, destination: Path, progress: Callable | None = None) -> dict:
    """Compare every length and targeted bytes against raw source extents.

    Every file's first/last block and midpoint are checked. No extracted code
    is executed. Zero-byte files still receive length checks.
    """
    destination = Path(destination).absolute()
    reject_links(destination)
    with safe_output(destination, (MANIFEST,)).open(encoding="utf-8") as fp:
        manifest = json.load(fp)
    if manifest.get("status") != "complete":
        raise CuePullError("Extraction manifest is incomplete")
    expected_files = [e for e in disc.entries if not e.directory]
    expected_map = [e.summary() for e in expected_files]
    actual_map = [{k: item.get(k) for k in expected_map[0]} for item in manifest["files"]] if expected_map else []
    if expected_map != actual_map or disc.summary() != manifest["disc"]:
        # JSON object keys in indexes become strings; normalize for comparison.
        if expected_map != actual_map or json.dumps(disc.summary(), sort_keys=True) != json.dumps(manifest["disc"], sort_keys=True):
            raise CuePullError("Manifest does not match the selected image/tree")
    checked = 0
    total = 0
    for entry in expected_files:
        path = safe_output(destination, entry.output)
        if not path.is_file() or path.stat().st_size != entry.size:
            raise CuePullError(f"Output missing or wrong length: {path}")
        offsets = sorted({0, max(0, entry.size // 2 - 1024), max(0, entry.size - 2048)})
        with path.open("rb") as fp:
            for offset in offsets:
                length = min(2048, entry.size - offset)
                disc.stream.seek(entry.extent * 2048 + offset)
                expected = disc.stream.read(length)
                fp.seek(offset)
                actual = fp.read(length)
                if actual != expected:
                    raise CuePullError(f"Source extent comparison failed: {path} at byte {offset}")
                checked += length
        total += 1
        if progress:
            progress(total, checked, {"file_count": len(expected_files), "data_bytes": 0}, entry.source)
    return {"verified_files": total, "sampled_content_bytes": checked,
            "method": "all file lengths and first/midpoint/last bytes vs source extents"}
