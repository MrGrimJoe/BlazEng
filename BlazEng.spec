# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec — builds the BlazEng desktop app (BlazEng.exe) and the
# command-line tool (blazeng-cli.exe) into ONE shared folder: dist/BlazEng/
#
# Platform-aware on purpose: bin/ffmpeg(.exe) and godot/ are bundled when they
# were staged before the build (CI does this on Windows), and simply skipped
# otherwise, so the same spec also builds on Linux/macOS for testing.
#
# Nothing the app WRITES lives in the bundle: the install directory is
# read-only for normal users, so src/paths.py sends config, storage and logs
# to the per-user data directory when frozen.

import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules

WIN = sys.platform == "win32"

binaries, datas = [], [
    ("config.yaml", "."),                      # template, copied to the user dir on first run
    ("LICENSE", "."),
    # Blender runs this by FILE PATH (blender --python <file>), so it must exist
    # on disk next to the renderer module, not just inside the compiled archive.
    ("src/integrations/blender/blender_scene.py", "src/integrations/blender"),
]

_ffmpeg = "bin/ffmpeg.exe" if WIN else "bin/ffmpeg"
if os.path.isfile(_ffmpeg):
    binaries.append((_ffmpeg, "bin"))
if os.path.isdir("godot"):
    datas.append(("godot", "godot"))

# OpenTimelineIO ships native extensions and a plugin manifest that PyInstaller's
# import analysis can't see.
_otio_datas, _otio_binaries, _otio_hidden = collect_all("opentimelineio")
datas += _otio_datas
binaries += _otio_binaries

hiddenimports = (
    _otio_hidden
    # every provider is imported lazily by name from provider_factory
    + collect_submodules("src")
    + ["yaml", "sqlite3"]
)

_COMMON_EXCLUDES = ["tkinter", "matplotlib", "numpy", "pytest"]

# assets/icon.ico isn't in the repo yet; PyInstaller hard-fails on a missing icon
# path, so only use it if it exists.
_icon = "assets/icon.ico" if os.path.isfile("assets/icon.ico") else None

# ── GUI app (windowed) ──────────────────────────────────────────────────────
gui = Analysis(
    ["main.py"], pathex=[], binaries=binaries, datas=datas,
    hiddenimports=hiddenimports, hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=_COMMON_EXCLUDES, noarchive=False,
)

# ── CLI (console) — no Qt, so it stays small ────────────────────────────────
cli = Analysis(
    ["cli_entry.py"], pathex=[], binaries=binaries, datas=datas,
    hiddenimports=hiddenimports, hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=_COMMON_EXCLUDES + ["PyQt6"], noarchive=False,
)

MERGE((gui, "main", "BlazEng"), (cli, "cli_entry", "blazeng-cli"))

gui_pyz = PYZ(gui.pure)
cli_pyz = PYZ(cli.pure)

gui_exe = EXE(
    gui_pyz, gui.scripts, [], exclude_binaries=True,
    name="BlazEng", debug=False, bootloader_ignore_signals=False, strip=False,
    upx=False,           # UPX-packed exes are a common antivirus false-positive trigger
    console=False,       # no terminal window for the desktop app
    disable_windowed_traceback=False, icon=_icon,
)
cli_exe = EXE(
    cli_pyz, cli.scripts, [], exclude_binaries=True,
    name="blazeng-cli", debug=False, bootloader_ignore_signals=False, strip=False,
    upx=False, console=True, icon=_icon,
)

coll = COLLECT(
    gui_exe, gui.binaries, gui.datas,
    cli_exe, cli.binaries, cli.datas,
    strip=False, upx=False, name="BlazEng",
)
