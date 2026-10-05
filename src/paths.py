"""
Where BlazEng keeps things, whether it runs from source or as an installed app.

From source (``python main.py``) nothing changes: config.yaml and storage/ are
relative to the current directory, exactly as before.

When *frozen* by PyInstaller (the Windows installer) the install directory
(``Program Files\\BlazEng``) is read-only for normal users, so everything that
changes lives in a per-user data directory instead:

    Windows  %LOCALAPPDATA%\\BlazEng
    macOS    ~/Library/Application Support/BlazEng
    Linux    $XDG_DATA_HOME/blazeng  (default ~/.local/share/blazeng)

Set ``BLAZENG_HOME`` to force that behaviour (and location) when running from
source, e.g. for testing or a portable install.
"""

import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, Optional

APP_NAME = "BlazEng"
DEFAULT_CONFIG_NAME = "config.yaml"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_dir() -> Path:
    """Read-only directory holding bundled files (config template, ffmpeg, Godot)."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def user_data_dir() -> Path:
    override = os.environ.get("BLAZENG_HOME")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / APP_NAME.lower()


def uses_user_data_dir() -> bool:
    return is_frozen() or bool(os.environ.get("BLAZENG_HOME"))


def resolve_config_path(path: str = DEFAULT_CONFIG_NAME) -> Path:
    """Map the default ``config.yaml`` to the per-user file when installed.

    An explicitly chosen path (anything but the bare default) is always used as given.
    """
    if path == DEFAULT_CONFIG_NAME and uses_user_data_dir():
        return user_data_dir() / DEFAULT_CONFIG_NAME
    return Path(path)


def ensure_user_config(config_path: Path) -> None:
    """First run of an installed app: seed the user's config from the bundled template."""
    if config_path.exists() or not uses_user_data_dir():
        return
    template = resource_dir() / DEFAULT_CONFIG_NAME
    if template.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(template, config_path)


def _bundled_godot() -> Optional[Path]:
    folder = resource_dir() / "godot"
    if not folder.is_dir():
        return None
    # The Windows zip ships a GUI exe and a *_console.exe wrapper; use the GUI one.
    candidates = sorted(
        p for p in folder.iterdir()
        if p.is_file() and p.name.lower().startswith("godot") and "console" not in p.name.lower()
        and p.suffix.lower() in ("", ".exe", ".x86_64")
    )
    return candidates[0] if candidates else None


def apply_installed_defaults(config: Dict[str, Any]) -> Dict[str, Any]:
    """Point an installed app's config at its per-user storage and bundled tools.

    Only fills in what is missing or no longer exists, so a user's own explicit
    paths are respected. A no-op when running from source.
    """
    if not uses_user_data_dir():
        return config
    cfg = dict(config)

    storage = Path(str(cfg.get("storage_path", "./storage")))
    if not storage.is_absolute():
        cfg["storage_path"] = str(user_data_dir() / "storage")

    if is_frozen():
        exe = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
        bundled_ffmpeg = resource_dir() / "bin" / exe
        current = str(cfg.get("ffmpeg_path") or "ffmpeg")
        if bundled_ffmpeg.exists() and (
            current == "ffmpeg" or not (Path(current).exists() or shutil.which(current))
        ):
            cfg["ffmpeg_path"] = str(bundled_ffmpeg)

        godot = _bundled_godot()
        if godot and not Path(str(cfg.get("godot_binary_path") or "")).exists():
            cfg["godot_binary_path"] = str(godot)
    return cfg
