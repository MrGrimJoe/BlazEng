#!/usr/bin/env python3
"""
AI Production Studio — Automated Setup Installer
=================================================
Run ONCE after cloning the repo:

    python setup_studio.py

What this does:
  1.  Checks Python 3.11+
  2.  Creates a virtual environment (.venv/)
  3.  Installs all pip dependencies from requirements.txt
  4.  Downloads Godot 4.3 headless binary → bin/
  5.  Downloads Blender 4.2 → bin/blender_app/
  6.  Downloads FFmpeg static build → bin/
  7.  Creates all storage/ subdirectories
  8.  Prompts for your Gemini API key → writes to config.yaml
  9.  Runs a quick smoke test

After setup, launch with:
    .venv/bin/python main.py          (Linux / macOS)
    .venv\\Scripts\\python main.py    (Windows)

Checkpoints in this repo
────────────────────────
  CP1  This installer + project skeleton hardening
  CP2  Validators live (real Gemini Vision calls)
  CP3  Repair engine — all methods implemented
  CP4  Godot render_scene.gd + subprocess calls live
  CP5  Imagen real API + export dialog UI
  CP6  Shot-targeting dropdown + PyInstaller spec
"""

from __future__ import annotations

import os
import re
import sys
import shutil
import tarfile
import zipfile
import platform
import tempfile
import subprocess
from pathlib import Path
from typing import Optional
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

# ── Paths ─────────────────────────────────────────────────────────────────────

ROOT        = Path(__file__).parent.resolve()
BIN         = ROOT / "bin"
VENV        = ROOT / ".venv"
CONFIG_FILE = ROOT / "config.yaml"
STORAGE     = ROOT / "storage"
GODOT_DIR   = ROOT / "godot"

# ── Platform detection ────────────────────────────────────────────────────────

_sys = platform.system()
OS_NAME = {"Linux": "linux", "Darwin": "darwin", "Windows": "windows"}.get(_sys, _sys.lower())
_machine = platform.machine().lower()
ARCH = "arm64" if _machine in ("aarch64", "arm64") else "x86_64"
PKEY = f"{OS_NAME}_{ARCH}"

IS_WIN = OS_NAME == "windows"
IS_MAC = OS_NAME == "darwin"
IS_LIN = OS_NAME == "linux"
EXE    = ".exe" if IS_WIN else ""

# ── Download URLs ─────────────────────────────────────────────────────────────

_GV  = "4.3-stable"
_GB  = f"https://github.com/godotengine/godot/releases/download/{_GV}"
_BV  = "4.2.0"
_BB  = f"https://download.blender.org/release/Blender4.2/blender-{_BV}"
_FFB = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest"

# Each entry: (url, archive_type, binary_pattern_relative_to_extract_root)
# binary_pattern: simple path, leading * means search first subdirectory
DOWNLOADS: dict[str, dict[str, tuple]] = {
    "godot": {
        "linux_x86_64":   (f"{_GB}/Godot_v{_GV}_linux.x86_64.zip",   "zip",    f"Godot_v{_GV}_linux.x86_64"),
        "linux_arm64":    (f"{_GB}/Godot_v{_GV}_linux.arm64.zip",     "zip",    f"Godot_v{_GV}_linux.arm64"),
        "windows_x86_64": (f"{_GB}/Godot_v{_GV}_win64.exe.zip",       "zip",    f"Godot_v{_GV}_win64.exe"),
        "darwin_x86_64":  (f"{_GB}/Godot_v{_GV}_macos.universal.zip", "zip",    "Godot.app/Contents/MacOS/Godot"),
        "darwin_arm64":   (f"{_GB}/Godot_v{_GV}_macos.universal.zip", "zip",    "Godot.app/Contents/MacOS/Godot"),
    },
    "blender": {
        "linux_x86_64":   (f"{_BB}-linux-x64.tar.xz",   "tar.xz", f"blender-{_BV}-linux-x64/blender"),
        "windows_x86_64": (f"{_BB}-windows-x64.zip",    "zip",    f"blender-{_BV}-windows-x64/blender.exe"),
        "darwin_x86_64":  (f"{_BB}-macos-x64.dmg",      "dmg",    None),
        "darwin_arm64":   (f"{_BB}-macos-arm64.dmg",    "dmg",    None),
    },
    "ffmpeg": {
        "linux_x86_64":   (f"{_FFB}/ffmpeg-master-latest-linux64-gpl.tar.xz",    "tar.xz", "*/bin/ffmpeg"),
        "linux_arm64":    (f"{_FFB}/ffmpeg-master-latest-linuxarm64-gpl.tar.xz", "tar.xz", "*/bin/ffmpeg"),
        "windows_x86_64": (f"{_FFB}/ffmpeg-master-latest-win64-gpl.zip",          "zip",    "*/bin/ffmpeg.exe"),
        "darwin_x86_64":  (f"{_FFB}/ffmpeg-master-latest-macos64-gpl.zip",        "zip",    "*/bin/ffmpeg"),
        "darwin_arm64":   (f"{_FFB}/ffmpeg-master-latest-macos64-gpl.zip",        "zip",    "*/bin/ffmpeg"),
    },
}

# ── Console helpers ───────────────────────────────────────────────────────────

def banner(msg: str) -> None:
    print(f"\n{'─' * 62}\n  {msg}\n{'─' * 62}")

def step(msg: str) -> None:
    print(f"  ▸  {msg}")

def ok(msg: str) -> None:
    print(f"  ✓  {msg}")

def warn(msg: str) -> None:
    print(f"  ⚠  {msg}")

def fail(msg: str) -> None:
    print(f"  ✗  {msg}")

# ── File helpers ──────────────────────────────────────────────────────────────

def download(url: str, dest: Path, label: str) -> bool:
    """Download url to dest, showing a progress bar. Returns True on success."""
    try:
        req = Request(url, headers={"User-Agent": "AIProductionStudio/1.0"})
        with urlopen(req, timeout=180) as resp:
            total = int(resp.headers.get("Content-Length", 0) or 0)
            done  = 0
            with open(dest, "wb") as f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if total:
                        pct  = int(done / total * 40)
                        bar  = "█" * pct + "░" * (40 - pct)
                        mb   = done / 1_048_576
                        tmb  = total / 1_048_576
                        print(f"\r    [{bar}] {mb:.1f}/{tmb:.1f} MB", end="", flush=True)
        print()
        return True
    except (HTTPError, URLError) as exc:
        fail(f"Download failed ({label}): {exc}")
        return False


def extract(archive: Path, dest: Path, archive_type: str) -> None:
    """Extract zip or tar.xz / tar.gz to dest."""
    dest.mkdir(parents=True, exist_ok=True)
    if archive_type == "zip":
        with zipfile.ZipFile(archive) as z:
            z.extractall(dest)
    else:
        with tarfile.open(archive) as t:
            t.extractall(dest)


def find_binary(root: Path, pattern: str) -> Optional[Path]:
    """
    Locate a file matching a simple path pattern.
    A leading * means 'search the first subdirectory level'.
    """
    parts = Path(pattern).parts
    if not parts:
        return None
    if parts[0] == "*":
        rest = str(Path(*parts[1:])) if len(parts) > 1 else ""
        for child in sorted(root.iterdir()):
            if child.is_dir():
                result = find_binary(child, rest)
                if result:
                    return result
        return None
    candidate = root / Path(*parts)
    return candidate if candidate.exists() else None


def config_set(key: str, value: str) -> None:
    """Replace the value of a key in config.yaml (handles quoted and bare values)."""
    text = CONFIG_FILE.read_text(encoding="utf-8")
    # Match both quoted and unquoted values
    new  = re.sub(
        rf'^({re.escape(key)}:\s*).*$',
        rf'\g<1>"{value}"',
        text,
        flags=re.MULTILINE,
    )
    CONFIG_FILE.write_text(new, encoding="utf-8")


def make_executable(p: Path) -> None:
    if not IS_WIN:
        p.chmod(p.stat().st_mode | 0o111)

# ── Setup steps ───────────────────────────────────────────────────────────────

def check_python() -> None:
    banner("Checking Python version")
    v = sys.version_info
    if v < (3, 11):
        fail(f"Python 3.11+ required — you have {v.major}.{v.minor}.{v.micro}")
        sys.exit(1)
    ok(f"Python {v.major}.{v.minor}.{v.micro}")


def create_venv() -> None:
    banner("Virtual environment")
    if (VENV / ("Scripts/pip.exe" if IS_WIN else "bin/pip")).exists():
        ok(".venv already present — skipping")
        return
    step("Creating .venv ...")
    subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
    ok("Created .venv/")


def install_deps() -> None:
    banner("Python dependencies")
    pip = VENV / ("Scripts/pip.exe" if IS_WIN else "bin/pip")
    step("pip install -r requirements.txt ...")
    subprocess.run(
        [str(pip), "install", "-r", str(ROOT / "requirements.txt"), "--quiet"],
        check=True,
    )
    ok("All Python packages installed")


def create_dirs() -> None:
    banner("Storage directories")
    for sub in ("database", "projects", "assets/characters",
                "assets/objects", "assets/environments", "cache", "logs"):
        (STORAGE / sub).mkdir(parents=True, exist_ok=True)
    BIN.mkdir(parents=True, exist_ok=True)
    GODOT_DIR.mkdir(parents=True, exist_ok=True)
    ok("storage/ and bin/ ready")


def install_tool(name: str, target_name: str, binary_label: str) -> Optional[Path]:
    """
    Generic downloader / extractor for Godot, Blender, or FFmpeg.
    Returns the path of the installed binary, or None on failure.
    """
    target = BIN / target_name

    if target.exists():
        ok(f"{binary_label} already at {target} — skipping")
        return target

    entry = DOWNLOADS[name].get(PKEY)
    if not entry:
        warn(f"No {binary_label} build defined for platform '{PKEY}'.")
        warn(f"Install manually and set {name}_path in config.yaml.")
        return None

    url, archive_type, bin_pattern = entry

    if archive_type == "dmg":
        warn(f"{binary_label} for macOS ships as a .dmg. Install manually from:")
        warn(f"  {url}")
        warn(f"Then set blender_path in config.yaml to the Blender binary.")
        return None

    step(f"Downloading {binary_label} ...")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path  = Path(tmp)
        archive   = tmp_path / url.split("/")[-1]
        extract_d = tmp_path / "ex"

        if not download(url, archive, binary_label):
            return None

        step("Extracting ...")
        extract(archive, extract_d, archive_type)

        binary: Optional[Path] = None
        if bin_pattern:
            binary = find_binary(extract_d, bin_pattern)

        if not binary:
            # Fallback: find any matching executable by name
            candidates = [
                p for p in extract_d.rglob(f"{name}*")
                if p.is_file() and not p.suffix in (".txt", ".md", ".h", ".c", ".json")
            ]
            binary = candidates[0] if candidates else None

        if not binary:
            fail(f"Could not locate {binary_label} binary in archive.")
            return None

        # Blender needs its whole sibling directory structure
        if name == "blender":
            blender_app = BIN / "blender_app"
            shutil.copytree(binary.parent, blender_app, dirs_exist_ok=True)
            target = blender_app / target_name.split("/")[-1]
        else:
            shutil.copy2(binary, target)

        if target.exists():
            make_executable(target)

    ok(f"{binary_label} → {target}")
    return target


def install_ffmpeg() -> None:
    banner("FFmpeg")
    target_name = f"ffmpeg{EXE}"
    t = install_tool("ffmpeg", target_name, "FFmpeg")
    if t and t.exists():
        config_set("ffmpeg_path", str(t))


def install_godot() -> None:
    banner("Godot 4")
    target_name = f"godot{EXE}"
    t = install_tool("godot", target_name, "Godot 4")
    if t and t.exists():
        config_set("godot_path", str(t))


def install_blender() -> None:
    banner("Blender 4.2")
    target_name = f"blender_app/blender{EXE}"
    t = install_tool("blender", target_name, "Blender")
    if t and t.exists():
        config_set("blender_path", str(t))


def configure_api_key() -> None:
    banner("Gemini API key")
    text = CONFIG_FILE.read_text(encoding="utf-8")
    if "GEMINI_API_KEY_HERE" not in text:
        ok("API key already set in config.yaml")
        return
    print("  Your Gemini API key powers the AI Director, validators, and image generation.")
    print("  Get one free at: https://aistudio.google.com/apikey\n")
    key = input("  Paste your Gemini API key (Enter to skip): ").strip()
    if key:
        config_set("gemini_api_key", key)
        ok("Key saved to config.yaml")
    else:
        warn("Skipped — edit config.yaml before running the studio.")


def smoke_test() -> None:
    banner("Smoke test")
    python = VENV / ("Scripts/python.exe" if IS_WIN else "bin/python")
    if not python.exists():
        python = Path(sys.executable)
    result = subprocess.run(
        [str(python), "-c",
         "import yaml, pydantic, sqlite3; print('Core imports: OK')"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        ok(result.stdout.strip())
    else:
        warn(f"Import check failed: {result.stderr.strip()}")

    for name in ("ffmpeg", "godot"):
        b = BIN / f"{name}{EXE}"
        (ok if b.exists() else warn)(
            f"{name} binary {'present' if b.exists() else 'NOT FOUND — update config.yaml'}"
        )


def print_summary() -> None:
    banner("Setup complete!")
    launch = (
        r".venv\Scripts\python main.py" if IS_WIN
        else ".venv/bin/python main.py"
    )
    print(f"  Launch the studio:  {launch}\n")
    print("  Checkpoints in this repo:")
    print("    CP1 — Installer (this script)")
    print("    CP2 — Validators live (Gemini Vision)")
    print("    CP3 — Repair engine completed")
    print("    CP4 — Godot render_scene.gd + subprocesses live")
    print("    CP5 — Imagen real API + export dialog UI")
    print("    CP6 — Shot targeting + PyInstaller spec\n")

# ── Entry ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n" + "═" * 62)
    print("  AI Production Studio — Automated Setup")
    print("═" * 62)
    check_python()
    create_venv()
    install_deps()
    create_dirs()
    install_ffmpeg()
    install_godot()
    install_blender()
    configure_api_key()
    smoke_test()
    print_summary()
