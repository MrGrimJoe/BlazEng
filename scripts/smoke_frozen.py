#!/usr/bin/env python3
"""
Smoke-test a PyInstaller build of BlazEng (dist/BlazEng/).

Runs the *frozen* executables, not the Python sources, in a throw-away user
environment, and checks what a real install would depend on:

  1. blazeng-cli reports its version
  2. blazeng-cli doctor finds the bundled tools named by --expect-bundled
  3. the desktop exe starts, stays alive, and logs "UI launched"
  4. config + logs land in the per-user data dir
  5. nothing is written into the install dir or the working dir

Usage:  python scripts/smoke_frozen.py dist/BlazEng [--expect-bundled ffmpeg,godot]
Exit code 0 = all good; 1 = a check failed (details printed).
"""

import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

WIN = sys.platform == "win32"
EXE = ".exe" if WIN else ""


def snapshot(root: Path) -> set:
    return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}


def isolated_env(home: Path) -> dict:
    env = dict(os.environ)
    for var in ("PYTHONPATH", "BLAZENG_HOME"):
        env.pop(var, None)
    env.update({
        "HOME": str(home), "USERPROFILE": str(home),
        "LOCALAPPDATA": str(home / "AppData" / "Local"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "QT_QPA_PLATFORM": "offscreen",
    })
    return env


def user_data(home: Path) -> Path:
    if WIN:
        return home / "AppData" / "Local" / "BlazEng"
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "BlazEng"
    return home / ".local" / "share" / "blazeng"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dist", type=Path)
    ap.add_argument("--expect-bundled", default="", help="comma list: ffmpeg,godot")
    ap.add_argument("--gui-seconds", type=float, default=10.0)
    args = ap.parse_args()

    dist = args.dist.resolve()
    cli, gui = dist / f"blazeng-cli{EXE}", dist / f"BlazEng{EXE}"
    failures = []

    def check(ok: bool, what: str, detail: str = "") -> None:
        print(f"[{'ok' if ok else 'FAIL'}] {what}" + (f"  -- {detail}" if detail and not ok else ""))
        if not ok:
            failures.append(what)

    check(cli.exists(), f"{cli.name} present")
    check(gui.exists(), f"{gui.name} present")
    if failures:
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        home, work = Path(tmp) / "home", Path(tmp) / "cwd"
        home.mkdir(); work.mkdir()
        env = isolated_env(home)
        before = snapshot(dist)

        r = subprocess.run([str(cli), "version"], cwd=work, env=env, capture_output=True, text=True, timeout=120)
        check(r.returncode == 0 and r.stdout.startswith("blazeng "), "cli version", r.stdout + r.stderr)

        r = subprocess.run([str(cli), "doctor"], cwd=work, env=env, capture_output=True, text=True, timeout=120)
        print(r.stdout.rstrip())
        for tool in filter(None, args.expect_bundled.split(",")):
            line = next((l for l in r.stdout.splitlines() if f"] {tool}" in l), "")
            bundled = "[  ok]" in line and (str(dist).lower() in line.lower() or "_internal" in line.lower())
            check(bundled, f"doctor finds bundled {tool}", line or "no line for it")

        # seed a config that doesn't block on the first-run settings dialog
        cfg_dir = user_data(home)
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "config.yaml").write_text(
            "text_provider: dummy\nvision_provider: dummy\nimage_provider: dummy\n")

        proc = subprocess.Popen([str(gui)], cwd=work, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        deadline = time.time() + args.gui_seconds
        while time.time() < deadline and proc.poll() is None:
            time.sleep(0.25)
        alive = proc.poll() is None
        if alive:
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
        out = (proc.stdout.read() or b"").decode(errors="replace")
        check(alive, f"GUI stayed alive for {args.gui_seconds:.0f}s", out[-1500:])

        log = cfg_dir / "logs" / "studio.log"
        text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
        check("UI launched" in text, "GUI logged 'UI launched' to the per-user log", text[-800:] or "no log file")
        check((cfg_dir / "storage").exists(), "storage created in the per-user data dir")

        check(snapshot(dist) == before, "install dir untouched", str(snapshot(dist) ^ before))
        check(not list(work.iterdir()), "working dir untouched", str(list(work.iterdir())))

    print("\nALL CHECKS PASSED" if not failures else f"\n{len(failures)} CHECK(S) FAILED: {failures}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
