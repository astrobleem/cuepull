"""Simple desktop UI shared by source users and the windowed Windows EXE."""
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

from .core import AmbiguousImageError, Disc, ExtractionCancelled, extract
from .drop import attach_drop, select_drop, suggested_output


class App:
    def __init__(self, root, dropped=(), progress_hook=None):
        import tkinter as tk
        from tkinter import filedialog, ttk
        self.root, self.filedialog = root, filedialog
        self.progress_hook = progress_hook
        self.events = queue.Queue()
        self.cancelled = threading.Event()
        self.busy, self.closing = False, False
        self.image, self.destination = tk.StringVar(), tk.StringVar()
        self.status = tk.StringVar(value="Ready")
        self.detail = tk.StringVar(value="")
        self.result_kind = "ready"
        self.last_progress = (0, 0)
        self.output = None
        root.title("CuePull")
        root.geometry("700x420")
        root.minsize(600, 420)
        if sys.platform == "win32":
            style = ttk.Style(root)
            if "vista" in style.theme_names():
                style.theme_use("vista")
            style.configure(".", font=("Segoe UI", 10))
        panel = ttk.Frame(root, padding=20)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="Extract files from a CD image").pack(anchor="w")
        subtitle = "Drop a BIN or CUE here, or choose an image." if sys.platform == "win32" else "Choose a BIN or CUE image."
        ttk.Label(panel, text=subtitle).pack(anchor="w", pady=(5, 18))

        def row(label, variable, callback):
            ttk.Label(panel, text=label).pack(anchor="w")
            frame = ttk.Frame(panel)
            frame.pack(fill="x", pady=(5, 13))
            entry = ttk.Entry(frame, textvariable=variable)
            entry.pack(side="left", fill="x", expand=True)
            button = ttk.Button(frame, text="Browse...", command=callback)
            button.pack(side="right", padx=(9, 0))
            return entry, button

        self.image_entry, self.image_button = row("Image", self.image, self.select_image)
        self.output_entry, self.output_button = row("Output folder", self.destination, self.select_folder)
        self.controls = [self.image_entry, self.image_button, self.output_entry, self.output_button]
        self.progress = ttk.Progressbar(panel, maximum=100)
        self.progress.pack(fill="x", pady=(4, 12))
        ttk.Label(panel, textvariable=self.status, wraplength=650).pack(anchor="w")
        ttk.Label(panel, textvariable=self.detail, wraplength=650).pack(anchor="w", pady=(5, 0))
        actions = ttk.Frame(panel)
        actions.pack(side="bottom", fill="x", pady=(14, 0))
        self.folder_button = ttk.Button(actions, text="Open folder", state="disabled", command=self.open_folder)
        self.folder_button.pack(side="left")
        self.cancel_button = ttk.Button(actions, text="Cancel", state="disabled", command=self.cancel)
        self.cancel_button.pack(side="right", padx=(9, 0))
        self.extract_button = ttk.Button(actions, text="Extract", command=self.start)
        self.extract_button.pack(side="right")
        root.bind("<Alt-i>", lambda _: self.select_image())
        root.bind("<Alt-o>", lambda _: self.select_folder())
        root.bind("<Alt-e>", lambda _: self.start())
        root.bind("<Escape>", lambda _: self.cancel() if self.busy else None)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.bridge = attach_drop(root, self.accept_drop)
        root.after(100, self.drain)
        if dropped:
            root.after(150, lambda: self.accept_drop(dropped, auto=True))

    def accept_drop(self, paths, auto=False):
        if self.busy:
            self.detail.set("Wait for extraction to finish, or click Cancel before choosing another image.")
            return
        try:
            source = select_drop(paths)
            self.image.set(str(source))
            self.destination.set(str(suggested_output(source)))
            self.image_entry.xview_moveto(1)
            self.output_entry.xview_moveto(1)
            self.result_kind = "ready"
            self.status.set("Ready to extract")
            self.detail.set("Files will go into the folder named after the image.")
            self.progress["value"] = 0
            self.folder_button.configure(state="disabled")
            if auto:
                self.start()
        except Exception as exc:
            self.result_kind = "error"
            self.image.set("")
            self.destination.set("")
            self.progress["value"] = 0
            self.folder_button.configure(state="disabled")
            self.status.set("Could not select the image")
            self.detail.set(str(exc))

    def select_image(self):
        if self.busy:
            return
        value = self.filedialog.askopenfilename(title="Choose CD image", filetypes=[
            ("CD images", "*.cue *.bin *.img *.iso"), ("All files", "*.*")])
        if value:
            self.accept_drop([value])

    def select_folder(self):
        if self.busy:
            return
        parent = self.filedialog.askdirectory(title="Choose parent for the extracted folder")
        if parent:
            stem = Path(self.image.get()).stem or "disc"
            selected = suggested_output(Path(parent) / (stem + ".bin"))
            self.destination.set(str(selected))
            self.output_entry.xview_moveto(1)

    def start(self):
        if self.busy:
            return
        if not self.image.get() or not self.destination.get():
            self.status.set("Choose an image and output folder first.")
            return
        self.busy = True
        self.result_kind = "running"
        self.last_progress = (0, 0)
        self.cancelled.clear()
        self.output = Path(self.destination.get()).absolute()
        for control in self.controls:
            control.configure(state="disabled")
        self.extract_button.configure(state="disabled")
        self.folder_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.progress["value"] = 0
        self.status.set("Reading the disc...")
        self.detail.set("Your original image is opened read-only.")
        threading.Thread(target=self.worker, args=(Path(self.image.get()), self.output), daemon=True).start()

    def worker(self, input_path, output_path):
        try:
            if input_path.suffix.lower() != ".cue":
                matches = [p for p in input_path.parent.iterdir()
                           if p.suffix.lower() == ".cue" and p.stem.casefold() == input_path.stem.casefold()]
                if len(matches) == 1:
                    input_path = matches[0]
            if self.cancelled.is_set():
                raise ExtractionCancelled()
            with Disc(input_path) as disc:
                last = 0
                def update(count, size, summary, name):
                    nonlocal last
                    now = time.monotonic()
                    if self.progress_hook or now - last >= 0.05 or count == summary["file_count"]:
                        last = now
                        percent = size / summary["data_bytes"] * 100 if summary["data_bytes"] else 100
                        self.events.put(("progress", (percent, count, size, summary["file_count"])))
                    if self.progress_hook:
                        self.progress_hook(count, size, summary, self.cancelled)
                result = extract(disc, output_path, update, self.cancelled.is_set)
                skipped = disc.summary()["skipped_audio_tracks"]
                note = (f"Audio tracks {', '.join(map(str, skipped))} skipped." if skipped
                        else "Recovered without a CUE. Audio boundaries are unknown." if disc.inferred else "Extraction complete.")
                self.events.put(("done", (f"Extracted {result['completed_files']:,} files ({result['completed_bytes'] / 1048576:.1f} MiB)", note)))
        except ExtractionCancelled:
            self.events.put(("cancelled", ("Extraction cancelled", "Partial files remain. Choose a new output folder to try again.")))
        except AmbiguousImageError as exc:
            self.events.put(("error", ("Several data volumes were found", str(exc))))
        except Exception as exc:
            self.events.put(("error", ("Could not extract the image", str(exc))))

    def drain(self):
        latest = None
        while True:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "progress":
                latest = value
            else:
                latest = None
                self.busy = False
                self.result_kind = kind
                self.status.set(value[0])
                self.detail.set(value[1])
                self.progress["value"] = 100 if kind == "done" else self.progress["value"]
                for control in self.controls:
                    control.configure(state="normal")
                self.extract_button.configure(state="normal")
                self.cancel_button.configure(state="disabled")
                self.folder_button.configure(state="normal" if kind == "done" else "disabled")
                if self.closing:
                    self.destroy()
                    return
        if latest:
            percent, count, size, total = latest
            self.last_progress = (count, size)
            self.progress["value"] = percent
            self.status.set(f"Extracting {count:,}/{total:,} files - {size / 1048576:.1f} MiB")
            self.detail.set(f"{percent:.0f}% complete")
        self.root.after(100, self.drain)

    def cancel(self):
        if self.busy:
            self.cancelled.set()
            self.status.set("Cancelling...")
            self.cancel_button.configure(state="disabled")

    def open_folder(self):
        if self.output and self.result_kind == "done":
            if sys.platform == "win32":
                os.startfile(self.output)
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(self.output)])

    def destroy(self):
        if self.bridge:
            self.bridge.close()
            self.bridge = None
        self.root.destroy()

    def close(self):
        if self.busy:
            self.closing = True
            self.cancel()
        else:
            self.destroy()


def main(dropped=()):
    try:
        import tkinter as tk
    except ImportError:
        raise SystemExit("Tkinter is unavailable. Use the CLI or install your system's Python Tk package.")
    root = tk.Tk()
    app = App(root, dropped)
    root.mainloop()
    if app.bridge:
        app.bridge.close()
    return 0
