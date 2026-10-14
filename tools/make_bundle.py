"""Build a media-free ready source bundle using the hash-pinned PyPI wheel.

Run from the source root after downloading the wheel with requirements.txt.
The destination and ZIP must not exist. No downloads happen in this script.
"""
import argparse
import hashlib
import shutil
import zipfile
from pathlib import Path

WHEEL_SHA256 = "a905827335f0066af3fd416c5cf9b1f29dffaf4d0914b714555213d1809f38d4"
SOURCE_NAMES = {"LICENSE", ".gitignore", "THIRD_PARTY.md", "README.md",
                "requirements.txt", "pyproject.toml", "CuePull.cmd", "run.py"}
EXCLUDED_DIRS = {".git", "__pycache__", "dependencies", ".venv", "dist", "build"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--zip", dest="archive", type=Path, required=True)
    args = parser.parse_args()
    source = Path(__file__).resolve().parent.parent
    destination = args.destination.absolute()
    archive = args.archive.absolute()
    if destination.exists() or archive.exists():
        parser.error("Destination and ZIP must both be new paths")
    if not destination.parent.is_dir() or not archive.parent.is_dir():
        parser.error("Destination and ZIP parent directories must exist")
    wheel_bytes = args.wheel.read_bytes()
    if hashlib.sha256(wheel_bytes).hexdigest() != WHEEL_SHA256:
        parser.error("Dependency wheel does not match the pinned official PyPI SHA-256")
    selected = []
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        if any(part in EXCLUDED_DIRS for part in relative.parts):
            continue
        permitted = ((len(relative.parts) == 1 and path.name in SOURCE_NAMES)
                     or (relative.parts[0] in {"cuepull", "tests", "tools"} and path.suffix == ".py")
                     or (relative.parts[0] == ".github" and path.suffix == ".yml")
                     or (relative.parts[0] == "licenses" and path.name == "pycdlib-LGPL-2.1.txt"))
        if path.is_file() and permitted:
            selected.append((path, relative))
    destination.mkdir()
    for path, relative in selected:
        output = destination / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, output)
    with zipfile.ZipFile(args.wheel) as wheel:
        for name in wheel.namelist():
            parts = Path(name).parts
            if name.startswith("pycdlib/") and name.endswith(".py") and ".." not in parts:
                output = destination / "dependencies" / name
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(wheel.read(name))
        license_name = "pycdlib-1.14.0.dist-info/COPYING"
        licenses = destination / "licenses"
        licenses.mkdir(exist_ok=True)
        (licenses / "pycdlib-LGPL-2.1.txt").write_bytes(wheel.read(license_name))
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as output:
        for path in sorted(destination.rglob("*")):
            if path.is_file():
                relative = path.relative_to(destination)
                info = zipfile.ZipInfo((Path("cuepull") / relative).as_posix(), (1980, 1, 1, 0, 0, 0))
                info.external_attr = 0o100644 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                output.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    print(f"Ready source: {destination}")
    print(f"Media-free ZIP: {archive} ({archive.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
