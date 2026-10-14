"""Console entry point sharing the portable application's dynamic parser."""
import sys
from pathlib import Path

base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
if (base / "dependencies").is_dir():
    sys.path.insert(0, str(base / "dependencies"))
from cuepull.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
