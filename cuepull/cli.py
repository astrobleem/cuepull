"""Friendly cross-platform commands; diagnostics stay off JSON stdout."""
import argparse
import json
import sys
import time
import struct
from pathlib import Path

from . import __version__
from .core import CuePullError, Disc, MODES, extract, verify
import pycdlib


class Progress:
    def __init__(self, label="Extracting", write=None):
        self.label = label
        self.last = 0
        self.write = write or (lambda s: print(s, file=sys.stderr, flush=True))

    def __call__(self, count, size, summary, name):
        now = time.monotonic()
        if now - self.last >= 0.5 or count == summary["file_count"]:
            self.last = now
            total = summary["data_bytes"]
            percent = f" ({size / total:.0%})" if total else ""
            self.write(f"{self.label}: {count}/{summary['file_count']} files, "
                       f"{size / 1048576:.1f} MiB{percent}")


def parser():
    root = argparse.ArgumentParser(description="Extract CD files directly from BIN/CUE images, without mounting.")
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("inspect", "list", "extract", "verify"):
        cmd = commands.add_parser(name, help={"inspect": "Show layout and filesystem summary",
            "list": "List disc files", "extract": "Extract to a new folder",
            "verify": "Check extracted lengths and sampled bytes against source extents"}[name])
        cmd.add_argument("input", type=Path, help="CUE, or BIN/IMG/ISO when CUE is missing")
        cmd.add_argument("--track", type=int, help="Select a CUE data track")
        cmd.add_argument("--image", action="append", type=Path, help="Explicitly whitelist a sibling CUE image")
        cmd.add_argument("--namespace", choices=("auto", "iso", "joliet"), default="auto")
        cmd.add_argument("--layout", choices=sorted(MODES.keys() - {"AUDIO"}), help="BIN recovery layout override")
        cmd.add_argument("--start-sector", type=int, help="BIN recovery start, in physical sectors")
        cmd.add_argument("--scan-mib", type=int, default=64, help="BIN recovery scan limit (default 64 MiB)")
        if name in {"extract", "verify"}:
            cmd.add_argument("destination", type=Path, help="New output folder" if name == "extract" else "Existing extracted folder")
            cmd.add_argument("--quiet", action="store_true", help="Hide progress")
        if name in {"inspect", "list"}:
            cmd.add_argument("--json", action="store_true", help="Machine-readable output")
    commands.add_parser("gui", help="Open the simple file picker and Extract window")
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    if args.command == "gui":
        from .gui import main as gui_main
        return gui_main()
    try:
        with Disc(args.input, args.track, args.image, args.namespace,
                  args.layout, args.start_sector, args.scan_mib) as disc:
            if args.command == "inspect":
                summary = disc.summary()
                if args.json:
                    print(json.dumps(summary, indent=2))
                else:
                    print(f"{summary['filesystem']} volume: {summary['volume_id']}")
                    print(f"{summary['file_count']:,} files in {summary['directory_count']} directories; {summary['data_bytes']:,} file bytes")
                    print(f"Layout: {disc.track.mode}; metadata: {summary['metadata_source']}; namespace: {disc.namespace}")
                    for track in summary["tracks"]:
                        print(f"Track {track['track']:02}: {track['mode']}, INDEX 01={track['index01_frame']}, {track['stored_sectors']:,} sectors, virtual pregap={track['virtual_pregap_frames']}")
                    if summary["skipped_audio_tracks"]:
                        print("Audio tracks skipped: " + ", ".join(map(str, summary["skipped_audio_tracks"])))
                    if disc.inferred:
                        print("Lost CUE recovery: original audio tracks/pregaps are unknown; only the ISO volume span is inferred.")
            elif args.command == "list":
                if args.json:
                    print(json.dumps([e.summary() for e in disc.entries], ensure_ascii=False, indent=2))
                else:
                    for e in disc.entries:
                        print(f"{'DIR' if e.directory else str(e.size):>12}  {'/'.join(e.output)}")
            elif args.command == "extract":
                result = extract(disc, args.destination, None if args.quiet else Progress())
                print(f"Extracted {result['completed_files']:,} files ({result['completed_bytes']:,} bytes) to {args.destination.absolute()}")
            elif args.command == "verify":
                result = verify(disc, args.destination, None if args.quiet else Progress("Verifying"))
                print(json.dumps(result, indent=2))
        return 0
    except (CuePullError, pycdlib.pycdlibexception.PyCdlibException,
            OSError, ValueError, UnicodeError, struct.error, IndexError,
            RecursionError) as exc:
        print(f"CuePull: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("CuePull: interrupted. Partial output remains; choose a new folder to retry.", file=sys.stderr)
        return 130
