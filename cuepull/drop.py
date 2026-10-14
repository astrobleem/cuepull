"""File-drop selection and the native Windows WM_DROPFILES bridge."""
from pathlib import Path
import sys
from .core import CuePullError, reject_links, validate_component


def select_drop(paths):
    files = []
    for value in paths:
        path = Path(value).absolute()
        reject_links(path)
        if path.suffix.lower() not in {".bin", ".cue", ".img", ".iso"} or not path.is_file():
            raise CuePullError("Choose an existing BIN, CUE, IMG, or ISO file.")
        if path not in files:
            files.append(path)
    if len(files) == 1:
        return files[0]
    if len(files) == 2:
        cue = next((p for p in files if p.suffix.lower() == ".cue"), None)
        other = next((p for p in files if p.suffix.lower() != ".cue"), None)
        if (cue and other and cue.parent.resolve() == other.parent.resolve()
                and cue.stem.casefold() == other.stem.casefold()):
            return cue
    raise CuePullError("Drop one image, or its matching BIN/CUE pair. Several unrelated files are ambiguous.")


def suggested_output(source):
    source = Path(source).absolute()
    validate_component(source.stem)
    destination = source.with_name(source.stem)
    number = 2
    while destination.exists() or destination.is_symlink():
        destination = source.with_name(f"{source.stem} ({number})")
        number += 1
    return destination


class WindowsDrop:
    """Keep callback alive; Shell32 frees each received DROPFILES allocation."""
    def __init__(self, root, callback):
        import ctypes
        from ctypes import wintypes as w
        self.ctypes, self.callback = ctypes, callback
        self.user, self.shell = ctypes.WinDLL("user32", use_last_error=True), ctypes.WinDLL("shell32", use_last_error=True)
        self.user.GetAncestor.argtypes = [w.HWND, w.UINT]
        self.user.GetAncestor.restype = w.HWND
        self.hwnd = self.user.GetAncestor(root.winfo_id(), 2)
        self.user.SetWindowLongPtrW.argtypes = [w.HWND, ctypes.c_int, ctypes.c_void_p]
        self.user.SetWindowLongPtrW.restype = ctypes.c_void_p
        self.user.CallWindowProcW.argtypes = [ctypes.c_void_p, w.HWND, w.UINT, w.WPARAM, w.LPARAM]
        self.user.CallWindowProcW.restype = ctypes.c_ssize_t
        self.shell.DragAcceptFiles.argtypes = [w.HWND, w.BOOL]
        self.shell.DragQueryFileW.argtypes = [w.HANDLE, w.UINT, w.LPWSTR, w.UINT]
        self.shell.DragQueryFileW.restype = w.UINT
        self.shell.DragFinish.argtypes = [w.HANDLE]
        prototype = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)

        def receive(hwnd, message, wp, lp):
            if message == 0x233:
                paths = []
                try:
                    count = self.shell.DragQueryFileW(wp, 0xFFFFFFFF, None, 0)
                    for index in range(count):
                        length = self.shell.DragQueryFileW(wp, index, None, 0)
                        buffer = ctypes.create_unicode_buffer(length + 1)
                        self.shell.DragQueryFileW(wp, index, buffer, length + 1)
                        paths.append(buffer.value)
                finally:
                    self.shell.DragFinish(wp)
                root.after(0, self.callback, paths)
                return 0
            return self.user.CallWindowProcW(self.previous, hwnd, message, wp, lp)

        self.procedure = prototype(receive)
        self.previous = self.user.SetWindowLongPtrW(self.hwnd, -4, ctypes.cast(self.procedure, ctypes.c_void_p))
        if not self.previous:
            raise ctypes.WinError(ctypes.get_last_error())
        self.shell.DragAcceptFiles(self.hwnd, True)

    def close(self):
        self.shell.DragAcceptFiles(self.hwnd, False)
        self.user.SetWindowLongPtrW(self.hwnd, -4, self.previous)


def attach_drop(root, callback):
    if sys.platform != "win32":
        return None
    root.update_idletasks()
    return WindowsDrop(root, callback)


def send_native_drop(bridge, paths):
    """Dispatch a real Unicode WM_DROPFILES only to this app's HWND for tests."""
    import ctypes
    from ctypes import wintypes as w
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GlobalAlloc.argtypes = [w.UINT, ctypes.c_size_t]
    kernel.GlobalAlloc.restype = w.HGLOBAL
    kernel.GlobalLock.argtypes = [w.HGLOBAL]
    kernel.GlobalLock.restype = ctypes.c_void_p
    kernel.GlobalUnlock.argtypes = [w.HGLOBAL]
    class DropFiles(ctypes.Structure):
        _fields_ = [("pFiles", w.DWORD), ("pt", w.POINT), ("fNC", w.BOOL), ("fWide", w.BOOL)]
    header = DropFiles(ctypes.sizeof(DropFiles), w.POINT(0, 0), False, True)
    payload = ("\0".join(str(p) for p in paths) + "\0\0").encode("utf-16-le")
    handle = kernel.GlobalAlloc(0x42, ctypes.sizeof(header) + len(payload))
    pointer = kernel.GlobalLock(handle)
    if not pointer:
        raise ctypes.WinError(ctypes.get_last_error())
    ctypes.memmove(pointer, ctypes.byref(header), ctypes.sizeof(header))
    ctypes.memmove(pointer + ctypes.sizeof(header), payload, len(payload))
    kernel.GlobalUnlock(handle)
    bridge.user.SendMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
    bridge.user.SendMessageW.restype = ctypes.c_ssize_t
    bridge.user.SendMessageW(bridge.hwnd, 0x233, handle, 0)
