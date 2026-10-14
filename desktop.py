"""Windowed entry point; Explorer-dropped arguments auto-extract."""
import sys
from pathlib import Path

base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
if (base / "dependencies").is_dir():
    sys.path.insert(0, str(base / "dependencies"))

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--smoke-test":
        from cuepull.smoke import run
        run(Path(sys.argv[2]))
    else:
        from cuepull.gui import main
        main(sys.argv[1:])
