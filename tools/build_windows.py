"""Build portable Windows GUI/CLI, unmodified dynamic parser, and licenses."""
import ast
import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION = "0.2.0"


def main():
    if sys.platform != "win32" or sys.maxsize <= 2**32:
        raise SystemExit("This release build requires 64-bit Windows Python.")
    os.chdir(ROOT)
    out = ROOT / "dist"
    wheels = ROOT / "build" / "wheels"
    wheels.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, "-m", "pip", "download", "--no-deps", "--only-binary=:all:",
                    "--require-hashes", "-r", "requirements.txt", "-d", str(wheels)], check=True)
    source = out / "CuePull-source"
    out.mkdir(exist_ok=True)
    subprocess.run([sys.executable, "tools/make_bundle.py", "--wheel", str(wheels / "pycdlib-1.14.0-py2.py3-none-any.whl"),
                    "--destination", str(source), "--zip", str(out / f"cuepull-{VERSION}-source.zip")], check=True)
    # Parser stays as replaceable, unmodified LGPL source outside the EXEs.
    imports = set()
    for path in (source / "dependencies" / "pycdlib").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imports.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                imports.add(node.module.split(".")[0])
    imports -= {"pycdlib", "__future__"}
    options = ["--noconfirm", "--clean", "--onedir", "--noupx", "--exclude-module", "pycdlib"]
    for module in sorted(imports):
        options += ["--hidden-import", module]
    subprocess.run([sys.executable, "-m", "PyInstaller", *options, "--windowed", "--name", "CuePull", "desktop.py"], check=True)
    subprocess.run([sys.executable, "-m", "PyInstaller", *options, "--console", "--name", "CuePull-cli", "cli_entry.py"], check=True)
    app = out / "CuePull"
    cli = out / "CuePull-cli"
    shutil.copyfile(cli / "CuePull-cli.exe", app / "CuePull-cli.exe")
    shutil.copytree(cli / "_internal", app / "_internal", dirs_exist_ok=True)
    shutil.copytree(source / "dependencies", app / "dependencies")
    shutil.copytree(source / "licenses", app / "licenses")
    for name in ("LICENSE", "THIRD_PARTY.md", "README.md", "build-requirements.txt", "requirements.txt"):
        shutil.copyfile(ROOT / name, app / name)
    # Include complete project sources so this combined distribution remains
    # inspectable and its LGPL dependency can be replaced independently.
    source_code = app / "source"
    source_code.mkdir()
    for path in source.rglob("*"):
        if path.is_file() and "dependencies" not in path.relative_to(source).parts:
            target = source_code / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    for package in ("pyinstaller", "altgraph", "packaging", "pefile", "pyinstaller-hooks-contrib", "pywin32-ctypes", "setuptools"):
        dist = importlib.metadata.distribution(package)
        for item in dist.files or []:
            if any(word in item.name.lower() for word in ("license", "copying", "copyright")) and dist.locate_file(item).is_file():
                target = app / "licenses" / package / item.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(dist.locate_file(item), target)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.exists():
        raise RuntimeError("Python redistribution license was not found")
    shutil.copyfile(python_license, app / "licenses" / "Python-PSF.txt")
    import tkinter
    tcl = Path(tkinter.Tcl().eval("info library"))
    for name, folder in (("Tcl", tcl), ("Tk", tcl.parent / "tk8.6")):
        license_file = folder / "license.terms"
        if not license_file.exists():
            raise RuntimeError(f"{name} redistribution license was not found")
        shutil.copyfile(license_file, app / "licenses" / (name + "-license.terms.txt"))
    (app / "START-HERE.txt").write_text(
        "CuePull 0.2.0 for Windows 10/11 x64\n\n"
        "Drag a BIN or CUE onto CuePull.exe. Extraction starts immediately in\n"
        "a sibling folder named after your image. Existing folders get (2),\n"
        "(3), etc. Missing CUE is supported for identifiable ISO volumes.\n\n"
        "Or double-click CuePull.exe, drop/choose an image, then click Extract.\n"
        "Keep this entire folder together. No Python installation is needed.\n"
        "CuePull-cli.exe is optional for command-line use.\n\n"
        "This build is unsigned. Windows may show an unknown-publisher or\n"
        "SmartScreen warning. Verify the official release and SHA256SUMS.txt;\n"
        "follow your organization's security policy. CuePull does not bypass\n"
        "security warnings or change security settings.\n", encoding="utf-8")
    manifest = {"version": VERSION, "python": sys.version, "platform": sys.platform,
                "pyinstaller": importlib.metadata.version("pyinstaller"),
                "parser": "pycdlib 1.14.0: unmodified dynamic Python source",
                "dependencies": {p: importlib.metadata.version(p) for p in
                    ("pyinstaller", "altgraph", "packaging", "pefile", "pyinstaller-hooks-contrib", "pywin32-ctypes", "setuptools")}}
    (app / "BUILD-INFO.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    archive = out / f"CuePull-{VERSION}-windows-x64.zip"
    with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path in sorted(app.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                info = zipfile.ZipInfo((Path("CuePull") / path.relative_to(app)).as_posix(), (1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    assets = sorted(out.glob("*.zip"))
    (out / "SHA256SUMS.txt").write_text("".join(hashlib.sha256(p.read_bytes()).hexdigest() + "  " + p.name + "\n" for p in assets), encoding="ascii")
    print("Built", archive, archive.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
