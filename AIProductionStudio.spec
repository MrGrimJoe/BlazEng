# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec file — builds the .exe

import os

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[
        ('bin/ffmpeg.exe', 'bin'),   # bundled FFmpeg
    ],
    datas=[
        ('config.yaml', '.'),
        ('godot/', 'godot/'),
        ('storage/', 'storage/'),
    ],
    hiddenimports=[
        'PyQt6.QtMultimedia',
        'PyQt6.QtMultimediaWidgets',
        'google.genai',
        'pydantic',
        'yaml',
        'sqlite3',
        # opentimelineio deliberately NOT here: requirements.txt has it
        # commented out (Phase 9+, not built yet -- see
        # src/integrations/usd/__init__.py, which is a literal empty stub),
        # so it's never actually installed. Forcing it as a hiddenimport
        # made PyInstaller fail trying to resolve a module that doesn't
        # exist in the build environment.
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'numpy'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# assets/icon.ico doesn't exist in the repo (no assets/ folder at all --
# same never-committed-yet pattern as storage/ and godot/, but this one's
# an actual design asset, not something to fake). PyInstaller's icon
# embedding is Windows-only and hard-fails with FileNotFoundError if the
# path doesn't exist, even though the app itself has nothing to do with
# icons -- Icon() falling back to None just means Windows shows the
# generic exe icon until a real .ico gets added here.
_icon_path = 'assets/icon.ico'
_icon = _icon_path if os.path.isfile(_icon_path) else None

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='AIProductionStudio',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,   # no terminal window
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_icon,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='AIProductionStudio',
)
