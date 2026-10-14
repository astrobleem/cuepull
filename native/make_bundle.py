"""Allowlisted, media-free private candidate packaging. No downloads."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import zipfile
from pe_audit import audit

SOURCE_FILES = ('cuepull.h', 'platform.h', 'core.c', 'platform.c', 'cli.c',
                'win32.c', 'README.md', 'THIRD_PARTY.md', 'Makefile',
                'build-watcom.cmd', 'pe_audit.py', 'make_bundle.py',
                'tests/test_native.py', 'tests/test_win32_gui.py',
                'licenses/OpenWatcom-1.9.txt')


def zip_files(path, entries):
    with zipfile.ZipFile(path, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, content in sorted(entries.items()):
            item = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            item.external_attr = 0o100644 << 16
            item.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(item, content)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--source-zip', type=Path, required=True)
    parser.add_argument('--windows-zip', type=Path, required=True)
    parser.add_argument('--build', type=Path, default=Path(__file__).parent / 'build')
    args = parser.parse_args()
    for path in (args.destination, args.source_zip, args.windows_zip):
        if path.exists() or not path.parent.is_dir():
            parser.error('All destinations must be new with existing parents: ' + str(path))
    root = Path(__file__).resolve().parent
    source = {}
    for name in SOURCE_FILES:
        p = root / name
        if p.is_symlink() or not p.is_file(): parser.error('Missing/linked source: ' + name)
        source[name] = p.read_bytes()
    source['LICENSE'] = (root.parent / 'LICENSE').read_bytes()
    audit_report = [audit(args.build / name) for name in ('CuePull32.exe', 'cuepull32-cli.exe')]
    assert {r['subsystem'] for r in audit_report} == {2, 3}
    binaries = {name: (args.build / name).read_bytes() for name in ('CuePull32.exe', 'cuepull32-cli.exe')}
    binaries.update({name: source[name] for name in ('LICENSE', 'README.md', 'THIRD_PARTY.md', 'licenses/OpenWatcom-1.9.txt')})
    binaries['pe-audit.json'] = (json.dumps(audit_report, indent=2) + '\n').encode()
    binaries.update({'source/' + name: content for name, content in source.items()})
    args.destination.mkdir()
    for name, content in binaries.items():
        p = args.destination / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(content)
    zip_files(args.source_zip, {'cuepull32/' + name: content for name, content in source.items()})
    zip_files(args.windows_zip, {'CuePull32/' + name: content for name, content in binaries.items()})
    checksums = '\n'.join(hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + p.name
                          for p in (args.source_zip, args.windows_zip)) + '\n'
    (args.destination / 'SHA256SUMS.txt').write_text(checksums)
    print(checksums, end='')
    print('Private candidate: ' + str(args.destination))


if __name__ == '__main__': main()
