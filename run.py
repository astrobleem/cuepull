"""Run from an unpacked source folder, optionally using local dependencies."""
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
vendor = root / "dependencies"
if vendor.is_dir():
    sys.path.insert(0, str(vendor))
try:
    from cuepull.cli import main
except ModuleNotFoundError as exc:
    raise SystemExit("Install the dependency first: python -m pip install -r requirements.txt") from exc
raise SystemExit(main())
