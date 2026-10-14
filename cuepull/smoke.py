"""Packaged-app smoke tests and actual-window screenshots, using our fixtures.

Only this application's own Tk widgets and HWND are exercised. No other app,
user disc, clipboard, or desktop pixels are read. Public fixtures are generated
from original text and deterministic bytes, not supplied Windows media.
"""
import ctypes
import hashlib
import json
import os
import struct
import sys
import threading
import time
import traceback
import zlib
from pathlib import Path


def fixture(folder, stem, raw=True, large=False):
    import pycdlib
    import io
    folder.mkdir(parents=True, exist_ok=True)
    files = {"README.TXT": b"CuePull synthetic demonstration disc. Original test data, dedicated to the public domain.\r\n",
             "GUIDE.TXT": b"These are generated example files. No commercial disc contents are included.\r\n",
             "SAMPLES/INDEX.TXT": b"Example binary pattern and text notes.\r\n",
             "SAMPLES/NOTES.TXT": b"Safe to copy, modify and redistribute this generated fixture.\r\n",
             "SAMPLES/PATTERN.DAT": bytes(range(256)) * (131072 if large else 32)}
    iso = pycdlib.PyCdlib()
    iso.new(interchange_level=3, vol_ident="CUEPULL_DEMO")
    iso.add_directory(iso_path="/SAMPLES")
    holders = []
    for name, data in files.items():
        fp = io.BytesIO(data)
        holders.append(fp)
        iso.add_fp(fp, len(data), iso_path="/" + name + ";1")
    cooked = folder / (stem + ".iso")
    iso.write(str(cooked))
    iso.close()
    if raw:
        from .core import SYNC
        image = folder / (stem + ".bin")
        with cooked.open("rb") as source, image.open("xb") as out:
            while block := source.read(2048):
                out.write(SYNC + b"\0\x02\0\x01" + block + b"\0" * 288)
    else:
        image = cooked
    cue = folder / (stem + ".cue")
    cue.write_text(f'FILE "{image.name}" BINARY\n TRACK 01 {"MODE1/2352" if raw else "MODE1/2048"}\n INDEX 01 00:00:00\n', encoding="utf-8")
    return image, cue, files


def capture_window(root, path):
    """PNG encode pixels returned by PrintWindow for this exact Tk window."""
    from ctypes import wintypes as w
    user, gdi = ctypes.WinDLL("user32", use_last_error=True), ctypes.WinDLL("gdi32", use_last_error=True)
    user.GetAncestor.argtypes = [w.HWND, w.UINT]; user.GetAncestor.restype = w.HWND
    hwnd = user.GetAncestor(root.winfo_id(), 2)
    user.GetWindowRect.argtypes = [w.HWND, ctypes.POINTER(w.RECT)]
    rect = w.RECT(); user.GetWindowRect(hwnd, ctypes.byref(rect))
    width, height = rect.right - rect.left, rect.bottom - rect.top
    user.GetWindowDC.argtypes = [w.HWND]; user.GetWindowDC.restype = w.HDC
    user.ReleaseDC.argtypes = [w.HWND, w.HDC]
    user.PrintWindow.argtypes = [w.HWND, w.HDC, w.UINT]
    gdi.CreateCompatibleDC.argtypes = [w.HDC]; gdi.CreateCompatibleDC.restype = w.HDC
    gdi.CreateCompatibleBitmap.argtypes = [w.HDC, ctypes.c_int, ctypes.c_int]; gdi.CreateCompatibleBitmap.restype = w.HBITMAP
    gdi.SelectObject.argtypes = [w.HDC, w.HGDIOBJ]; gdi.SelectObject.restype = w.HGDIOBJ
    gdi.DeleteObject.argtypes = [w.HGDIOBJ]; gdi.DeleteDC.argtypes = [w.HDC]
    class Header(ctypes.Structure):
        _fields_ = [("size", w.DWORD), ("width", w.LONG), ("height", w.LONG),
                    ("planes", w.WORD), ("bits", w.WORD), ("compression", w.DWORD),
                    ("image", w.DWORD), ("x", w.LONG), ("y", w.LONG), ("used", w.DWORD), ("important", w.DWORD)]
    header = Header(40, width, -height, 1, 32, 0, width * height * 4, 0, 0, 0, 0)
    gdi.GetDIBits.argtypes = [w.HDC, w.HBITMAP, w.UINT, w.UINT, ctypes.c_void_p, ctypes.c_void_p, w.UINT]
    hdc = user.GetWindowDC(hwnd)
    memory = gdi.CreateCompatibleDC(hdc)
    bitmap = gdi.CreateCompatibleBitmap(hdc, width, height)
    previous = gdi.SelectObject(memory, bitmap)
    try:
        if not user.PrintWindow(hwnd, memory, 2):
            raise ctypes.WinError(ctypes.get_last_error())
        pixels = ctypes.create_string_buffer(width * height * 4)
        if gdi.GetDIBits(memory, bitmap, 0, height, pixels, ctypes.byref(header), 0) != height:
            raise RuntimeError("Window screenshot failed")
        bgra = pixels.raw
        rows = bytearray()
        for y in range(height):
            rows.append(0)
            start = y * width * 4
            for x in range(width):
                at = start + x * 4
                rows.extend((bgra[at + 2], bgra[at + 1], bgra[at]))
        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        png += chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b"")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png)
    finally:
        gdi.SelectObject(memory, previous)
        gdi.DeleteObject(bitmap); gdi.DeleteDC(memory); user.ReleaseDC(hwnd, hdc)


def run(report_folder):
    """No external Python/modules are used when called inside the frozen EXE."""
    report_folder = report_folder.absolute()
    report_folder.mkdir(parents=True, exist_ok=True)
    report = {"status": "running", "version": "0.2.0", "frozen": bool(getattr(sys, "frozen", False)), "checks": []}
    report_file = report_folder / "smoke-report.json"
    app = None
    try:
        import tkinter as tk
        from unittest import mock
        from .gui import App
        from .drop import send_native_drop
        # Synthetic path visible in public screenshots; never a user's directory.
        fixture_root = Path(os.environ.get("CUEPULL_DEMO_ROOT", str(report_folder / "demo"))).absolute()
        raw, raw_cue, expected = fixture(fixture_root / "raw", "Demo Disc \u03a9", large=True)
        raw_cue.unlink()  # This generated test cue is deliberately absent.
        original_hash = hashlib.sha256(raw.read_bytes()).hexdigest()
        cue_image, cue, small = fixture(fixture_root / "cue", "CUE Example")
        root = tk.Tk()
        root.geometry("700x420+80+80")
        gate = threading.Event()
        hold = {"enabled": True, "minimum": 8 * 1048576}
        def hook(count, size, summary, cancelled):
            if hold["enabled"] and size >= hold["minimum"]:
                while not gate.wait(0.03) and not cancelled.is_set():
                    pass
        app = App(root, progress_hook=hook)

        def pump_until(condition, timeout=25):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                root.update()
                if condition():
                    return
                time.sleep(0.015)
            raise AssertionError(f"GUI timeout: {app.status.get()} / {app.detail.get()}")

        root.update()
        send_native_drop(app.bridge, [raw])
        pump_until(lambda: app.image.get() == str(raw))
        assert not app.busy and not (raw.parent / raw.stem).exists()
        report["checks"].append("native WM_DROPFILES selects Unicode/spaced raw BIN without auto-extraction")
        root.update()
        capture_window(root, report_folder / "screenshots" / "choose-image.png")
        # This is the same App startup path used by Explorer-dropped argv.
        app.accept_drop([raw], auto=True)
        pump_until(lambda: app.last_progress[1] >= 1048576)
        assert app.busy and 0 < app.progress["value"] < 100
        root.update()
        capture_window(root, report_folder / "screenshots" / "extracting.png")
        gate.set(); hold["enabled"] = False
        pump_until(lambda: app.result_kind == "done")
        out = raw.parent / raw.stem
        assert app.output == out
        for name, content in expected.items():
            assert (out / name).read_bytes() == content
        assert hashlib.sha256(raw.read_bytes()).hexdigest() == original_hash
        root.update()
        capture_window(root, report_folder / "screenshots" / "finished.png")
        report["checks"].append("raw BIN recovery, stem sibling folder, streamed progress, output content and source preservation")
        (out / "keep.txt").write_text("unrelated")
        app.accept_drop([raw], auto=True)
        pump_until(lambda: app.result_kind == "done")
        assert app.output == raw.parent / (raw.stem + " (2)")
        assert (out / "keep.txt").read_text() == "unrelated"
        report["checks"].append("existing sibling folder preserved; safe automatic suffix")
        send_native_drop(app.bridge, [cue_image, cue])
        pump_until(lambda: app.image.get() == str(cue))
        assert not app.busy
        app.start(); pump_until(lambda: app.result_kind == "done")
        assert (app.output / "README.TXT").read_bytes() == small["README.TXT"]
        report["checks"].append("matching BIN+CUE native drop chooses one CUE operation; CUE extraction")
        hold["enabled"] = True; hold["minimum"] = 1; gate.clear()
        app.accept_drop([raw], auto=True)
        pump_until(lambda: app.busy and app.last_progress[1] > 0)
        app.cancel(); pump_until(lambda: app.result_kind == "cancelled")
        m = json.loads((app.output / "cuepull-manifest.json").read_text())
        assert m["status"] == "incomplete"
        hold["enabled"] = False; gate.set()
        report["checks"].append("cooperative cancellation leaves incomplete manifest and responsive window")
        app.accept_drop([raw, cue], auto=True)
        assert app.result_kind == "error" and not app.busy
        unsupported = fixture_root / "notes.txt"; unsupported.write_text("test")
        send_native_drop(app.bridge, [unsupported])
        pump_until(lambda: app.result_kind == "error" and "existing BIN" in app.detail.get())
        report["checks"].append("ambiguous and unsupported drops show persistent errors")
        app.accept_drop([cue])
        app.destination.set(str(out))
        app.start(); pump_until(lambda: app.result_kind == "error")
        assert "already exists" in app.detail.get()
        report["checks"].append("explicit existing output refused without overwrite")
        with mock.patch.object(app.filedialog, "askopenfilename", return_value=str(raw)):
            app.select_image()
        assert app.image.get() == str(raw) and not app.busy
        report["checks"].append("file-picker selection callback")
        report["status"] = "passed"
        report["screenshot_method"] = "PrintWindow pixels from the actual application HWND; synthetic fixture only"
        report["parser_source"] = str(Path(sys.modules["pycdlib"].__file__).relative_to(Path(sys.executable).parent)) if report["frozen"] else "source environment"
    except Exception:
        report["status"] = "failed"
        report["error"] = traceback.format_exc()
    finally:
        if app:
            app.destroy()
        report_file.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if report["status"] != "passed":
        raise SystemExit(1)
