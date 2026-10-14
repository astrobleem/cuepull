"""Run the frozen executables with PATH/PYTHONHOME/PYTHONPATH removed."""
import argparse
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path


def wait_manifest(process, destination, timeout=40):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"Windowed app exited before completion: {process.returncode}")
        path = destination / "cuepull-manifest.json"
        if path.exists():
            try:
                result = json.loads(path.read_text(encoding="utf-8"))
                if result["status"] == "complete":
                    return result
            except json.JSONDecodeError:
                pass
        time.sleep(0.08)
    raise AssertionError("No completed extraction manifest from windowed launch")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    app, report = args.bundle.absolute(), args.report.absolute()
    report.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
    env["PYTHONHOME"] = str(report / "nonexistent-python")
    env["PYTHONPATH"] = str(report / "nonexistent-modules")
    env["CUEPULL_DEMO_ROOT"] = env.get("CUEPULL_DEMO_ROOT", str(report / "demo"))
    demo_root = Path(env["CUEPULL_DEMO_ROOT"])
    wrong_cwd = report / "different-working-directory"
    wrong_cwd.mkdir()
    result = subprocess.run([str(app / "CuePull.exe"), "--smoke-test", str(report)], cwd=wrong_cwd, env=env, timeout=120)
    smoke = json.loads((report / "smoke-report.json").read_text())
    assert result.returncode == 0 and smoke["status"] == "passed", smoke
    assert smoke["frozen"] and smoke["parser_source"].startswith("dependencies")
    # Launch exactly as Explorer does: separate Unicode/spaced filename args,
    # no Extract click and unrelated cwd. Existing output already exists.
    image = demo_root / "raw" / "Demo Disc \u03a9.bin"
    parent = image.parent
    n = 2
    destination = parent / image.stem
    while destination.exists():
        destination = parent / (image.stem + f" ({n})")
        n += 1
    original_hash = hashlib.sha256(image.read_bytes()).hexdigest()
    process = subprocess.Popen([str(app / "CuePull.exe"), str(image)], cwd=wrong_cwd, env=env)
    try:
        wait_manifest(process, destination)
        assert hashlib.sha256(image.read_bytes()).hexdigest() == original_hash
        assert not list(wrong_cwd.glob("cuepull-manifest.json"))
    finally:
        process.terminate(); process.wait(timeout=10)
    cue = demo_root / "cue" / "CUE Example.cue"
    cue_image = cue.with_suffix(".bin")
    destination = cue.parent / (cue.stem + " (2)")
    process = subprocess.Popen([str(app / "CuePull.exe"), str(cue_image), str(cue)], cwd=wrong_cwd, env=env)
    try:
        wait_manifest(process, destination)
        assert not (cue.parent / (cue.stem + " (3)")).exists()
    finally:
        process.terminate(); process.wait(timeout=10)
    cli = subprocess.check_output([str(app / "CuePull-cli.exe"), "inspect", str(image), "--json"], cwd=wrong_cwd, env=env, text=True)
    assert json.loads(cli)["metadata_source"] == "inferred"
    smoke["checks"] += ["actual GUI launch argv: Unicode/spaces, missing CUE, different cwd, sibling folder and safe suffix",
                        "actual GUI launch argv: matching BIN+CUE pair makes one extraction",
                        "frozen CLI inspect without Python or external parser"]
    smoke["external_python_environment"] = "PATH only System32; PYTHONHOME/PYTHONPATH point to nonexistent directories"
    (report / "smoke-report.json").write_text(json.dumps(smoke, indent=2), encoding="utf-8")
    print(json.dumps(smoke, indent=2))


if __name__ == "__main__":
    main()
