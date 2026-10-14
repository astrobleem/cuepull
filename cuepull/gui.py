"""Optional Tkinter interface: choose an image, choose a new folder, extract."""
import queue
import threading
from pathlib import Path


def main():
    try:
        import tkinter as tk
        from tkinter import filedialog, ttk
    except ImportError:
        raise SystemExit("Tkinter is unavailable. Use the CLI, or install your system's Python Tk package.")
    from .core import AmbiguousImageError, Disc, extract

    root = tk.Tk()
    root.title("CuePull")
    root.geometry("620x340")
    root.minsize(520, 340)
    panel = ttk.Frame(root, padding=18)
    panel.pack(fill="both", expand=True)
    ttk.Label(panel, text="Extract files from a CD image", font=("Segoe UI", 15)).pack(anchor="w")
    ttk.Label(panel, text="Choose a BIN or CUE, then a new output folder.").pack(anchor="w", pady=(4, 16))
    image = tk.StringVar()
    destination = tk.StringVar()
    status = tk.StringVar(value="Ready")
    events = queue.Queue()
    busy = False

    def select_image():
        value = filedialog.askopenfilename(title="Choose CD image", filetypes=[
            ("CD images", "*.cue *.bin *.img *.iso"), ("All files", "*.*")])
        if value:
            image.set(value)
            p = Path(value)
            suggested = p.with_name(p.stem + "-extracted")
            n = 2
            while suggested.exists():
                suggested = p.with_name(p.stem + f"-extracted-{n}")
                n += 1
            destination.set(str(suggested))

    def select_folder():
        parent = filedialog.askdirectory(title="Choose parent for a new extracted folder")
        if parent:
            stem = Path(image.get()).stem or "disc"
            selected = Path(parent) / (stem + "-extracted")
            n = 2
            while selected.exists():
                selected = Path(parent) / (stem + f"-extracted-{n}")
                n += 1
            destination.set(str(selected))

    def row(label, variable, callback):
        ttk.Label(panel, text=label).pack(anchor="w")
        frame = ttk.Frame(panel)
        frame.pack(fill="x", pady=(3, 10))
        entry = ttk.Entry(frame, textvariable=variable)
        entry.pack(side="left", fill="x", expand=True)
        button = ttk.Button(frame, text="Browse…", command=callback)
        button.pack(side="right", padx=(8, 0))
        return entry, button

    controls = [*row("Image", image, select_image),
                *row("New output folder", destination, select_folder)]
    progress = ttk.Progressbar(panel, maximum=100)
    progress.pack(fill="x", pady=(3, 9))
    ttk.Label(panel, textvariable=status, wraplength=560).pack(anchor="w")

    def worker(input_path, output_path):
        try:
            # Prefer a matching CUE when a BIN was selected; missing CUE is fine.
            if input_path.suffix.lower() != ".cue":
                matches = [p for p in input_path.parent.iterdir()
                           if p.suffix.lower() == ".cue" and p.stem.casefold() == input_path.stem.casefold()]
                if len(matches) == 1:
                    input_path = matches[0]
            with Disc(input_path) as disc:
                def update(count, size, summary, name):
                    percent = size / summary["data_bytes"] * 100 if summary["data_bytes"] else 100
                    events.put(("progress", (percent, f"{count:,}/{summary['file_count']:,} files · {size / 1048576:.1f} MiB")))
                result = extract(disc, output_path, update)
                skipped = disc.summary()["skipped_audio_tracks"]
                note = (f" Audio tracks {', '.join(map(str, skipped))} skipped." if skipped
                        else " CUE missing: audio boundaries are unknown." if disc.inferred else "")
                events.put(("done", f"Extracted {result['completed_files']:,} files to {output_path}.{note}"))
        except AmbiguousImageError as exc:
            events.put(("error", f"Multiple data volumes found. Use the CLI with --layout and --start-sector. {exc}"))
        except Exception as exc:
            events.put(("error", str(exc)))

    def start():
        nonlocal busy
        if busy:
            return
        if not image.get() or not destination.get():
            status.set("Choose an image and output folder first.")
            return
        busy = True
        for control in controls:
            control.configure(state="disabled")
        extract_button.configure(state="disabled")
        progress["value"] = 0
        status.set("Reading the disc…")
        threading.Thread(target=worker, args=(Path(image.get()), Path(destination.get())), daemon=True).start()

    extract_button = ttk.Button(panel, text="Extract", command=start)
    extract_button.pack(anchor="e", pady=(12, 0))

    def drain():
        nonlocal busy
        latest = None
        while True:
            try:
                kind, value = events.get_nowait()
            except queue.Empty:
                break
            if kind == "progress":
                latest = value
            else:
                latest = None
                busy = False
                status.set(value if kind == "done" else "Unable to extract: " + value)
                progress["value"] = 100 if kind == "done" else 0
                for control in controls:
                    control.configure(state="normal")
                extract_button.configure(state="normal")
        if latest:
            progress["value"], message = latest
            status.set(message)
        root.after(100, drain)

    def close():
        if busy:
            status.set("Extraction is running. Close when it completes.")
        else:
            root.destroy()

    root.protocol("WM_DELETE_WINDOW", close)
    root.after(100, drain)
    root.mainloop()
    return 0
