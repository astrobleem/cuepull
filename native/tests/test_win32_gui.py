"""Exercise only CuePull32's own HWNDs on the active Windows host."""
import argparse
import ctypes
from ctypes import wintypes as wt
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import time

p = argparse.ArgumentParser()
p.add_argument('--exe', required=True)
p.add_argument('--report', required=True)
args = p.parse_args()
assert os.name == 'nt'
u = ctypes.WinDLL('user32', use_last_error=True)
k = ctypes.WinDLL('kernel32', use_last_error=True)
u.GetDlgItem.argtypes = [wt.HWND, ctypes.c_int]; u.GetDlgItem.restype = wt.HWND
u.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u.SetWindowTextA.argtypes = [wt.HWND, wt.LPCSTR]
u.SendMessageA.argtypes = [wt.HWND, wt.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
u.SendMessageA.restype = ctypes.c_ssize_t
u.PostMessageA.argtypes = [wt.HWND, wt.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
u.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
u.GetClassNameA.argtypes = [wt.HWND, wt.LPSTR, ctypes.c_int]
u.IsWindowEnabled.argtypes = [wt.HWND]
callback_type = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, ctypes.c_ssize_t)
u.EnumWindows.argtypes = [callback_type, ctypes.c_ssize_t]
k.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]; k.GlobalAlloc.restype = wt.HGLOBAL
k.GlobalLock.argtypes = [wt.HGLOBAL]; k.GlobalLock.restype = wt.LPVOID
k.GlobalUnlock.argtypes = [wt.HGLOBAL]


def text(hwnd):
    buffer = ctypes.create_string_buffer(1024)
    u.SendMessageA(hwnd, 0x0d, len(buffer), ctypes.addressof(buffer))
    return buffer.value.decode('mbcs')


def set_text(hwnd, value):
    buffer = ctypes.create_string_buffer(os.fsencode(str(value)))
    u.SendMessageA(hwnd, 0x0c, 0, ctypes.addressof(buffer))


def find_window(proc):
    found = []
    @callback_type
    def visit(hwnd, unused):
        pid = wt.DWORD(); u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        name = ctypes.create_string_buffer(128); u.GetClassNameA(hwnd, name, len(name))
        if pid.value == proc.pid and name.value == b'CuePull32Window': found.append(hwnd)
        return True
    end = time.monotonic() + 15
    while time.monotonic() < end:
        u.EnumWindows(visit, 0)
        if found: return found[0]
        if proc.poll() is not None: raise AssertionError('GUI exited before showing a window')
        time.sleep(.03)
    raise AssertionError('GUI window did not appear')


def wait_done(hwnd, proc):
    end = time.monotonic() + 30
    while time.monotonic() < end:
        if u.IsWindowEnabled(u.GetDlgItem(hwnd, 104)):
            # A completion status is read from the dedicated static child.
            return status(hwnd)
        if proc.poll() is not None: raise AssertionError('GUI exited during extraction')
        time.sleep(.03)
    raise AssertionError('GUI worker did not finish')


def status(hwnd):
    values = []
    @callback_type
    def visit(child, unused):
        value = text(child)
        if value: values.append(value)
        return True
    u.EnumChildWindows.argtypes = [wt.HWND, callback_type, ctypes.c_ssize_t]
    u.EnumChildWindows(hwnd, visit, 0)
    return '\n'.join(values)


def drop(hwnd, path):
    set_text(u.GetDlgItem(hwnd, 101), path)
    # The application creates its own HDROP, then invokes the actual handler.
    # Arbitrary process-private HGLOBAL values cannot be sent cross-process.
    u.SendMessageA(hwnd, 0x8004, 0, 0)


def close(hwnd, proc):
    u.PostMessageA(hwnd, 0x10, 0, 0)
    proc.wait(timeout=10)
    assert proc.returncode == 0


# Independent tiny ISO fixture, importing definitions without running unittest.
source = Path(__file__).with_name('test_native.py').read_text()
fixture = source[source.index('def both32'):source.index('class NativeTests')]
namespace = {'struct': struct, 'SYNC': b'\0' + b'\xff' * 10 + b'\0'}
exec(fixture, namespace)
report = {'host': 'Windows host; XP/98 qualification remains pending', 'scenarios': []}
with tempfile.TemporaryDirectory() as temp:
    root = Path(temp).resolve()
    image = root / 'Demo Disc.bin'
    payload = b'Native GUI fixture\n' * 4096
    image.write_bytes(namespace['wrap'](namespace['iso']([('HELLO.TXT;1', payload)]), 'MODE1/2352'))
    env = os.environ.copy()
    env.update(PATH=os.environ['SystemRoot'] + r'\System32', PYTHONHOME=r'C:\no-python', PYTHONPATH=r'C:\no-python')
    processes = []
    try:
        proc = subprocess.Popen([args.exe], env=env); processes.append(proc)
        hwnd = find_window(proc)
        # Open and cancel the application's own common file dialog.
        u.PostMessageA(hwnd, 0x111, 103, 0)
        dialogs = []
        @callback_type
        def visit_dialog(child, unused):
            pid = wt.DWORD(); u.GetWindowThreadProcessId(child, ctypes.byref(pid))
            name = ctypes.create_string_buffer(128); u.GetClassNameA(child, name, len(name))
            if pid.value == proc.pid and name.value == b'#32770': dialogs.append(child)
            return True
        end = time.monotonic() + 15
        while time.monotonic() < end and not dialogs:
            u.EnumWindows(visit_dialog, 0); time.sleep(.03)
        assert dialogs, 'file picker did not open'
        u.PostMessageA(dialogs[0], 0x111, 2, 0)
        time.sleep(.1)
        report['scenarios'].append('classic file picker opens and cancels')
        drop(hwnd, image)
        assert text(u.GetDlgItem(hwnd, 101)) == str(image)
        assert not image.with_suffix('').exists(), 'in-window drop must only select'
        report['scenarios'].append('in-window WM_DROPFILES selects only')
        u.SendMessageA(hwnd, 0x111, 104, 0)
        result = wait_done(hwnd, proc)
        assert 'Complete:' in result, result
        assert (image.with_suffix('') / 'HELLO.TXT').read_bytes() == payload
        report['scenarios'].append('Extract streams and verifies correct bytes without Python')
        # Explicit existing folder must fail without altering its contents.
        set_text(u.GetDlgItem(hwnd, 102), image.with_suffix(''))
        u.SendMessageA(hwnd, 0x111, 104, 0)
        result = wait_done(hwnd, proc)
        assert 'already exists' in result, result
        report['scenarios'].append('existing explicit output refused')
        # Cancel is requested immediately; it must return to an idle UI.
        set_text(u.GetDlgItem(hwnd, 102), '')
        u.SendMessageA(hwnd, 0x111, 104, 0)
        u.SendMessageA(hwnd, 0x111, 105, 0)
        result = wait_done(hwnd, proc)
        assert 'Cancelled' in result, result
        report['scenarios'].append('cooperative cancellation returns to idle')
        close(hwnd, proc)
        proc = subprocess.Popen([args.exe, str(image)], env=env); processes.append(proc)
        hwnd = find_window(proc)
        # WM_AUTO may still be queued; wait for either busy or completion first.
        end = time.monotonic() + 15
        while time.monotonic() < end and 'Complete:' not in status(hwnd): time.sleep(.03)
        result = status(hwnd); assert 'Complete:' in result, result
        assert (root / 'Demo Disc (2)' / 'HELLO.TXT').read_bytes() == payload
        report['scenarios'].append('Explorer argv auto-extracts to safe sibling suffix')
        close(hwnd, proc)
        report['passed'] = True
    finally:
        for proc in processes:
            if proc.poll() is None: proc.terminate(); proc.wait(timeout=10)
Path(args.report).write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
