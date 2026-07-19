# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec file — builds the .exe

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
        'google.generativeai',
        'pydantic',
        'yaml',
        'sqlite3',
        'opentimelineio',
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
    icon='assets/icon.ico',
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
