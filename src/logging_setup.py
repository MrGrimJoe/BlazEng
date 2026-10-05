"""Application logging for the desktop app (file + optional console + crash capture)."""

import logging
import sys
from pathlib import Path
from typing import Optional

from src import paths

_FORMAT = "%(asctime)s [%(levelname)s] %(name)s — %(message)s"


def log_directory() -> Path:
    """Per-user logs folder when installed; ./storage/logs when run from source (as before)."""
    return paths.user_data_dir() / "logs" if paths.uses_user_data_dir() else Path("storage") / "logs"


def configure_logging(level: int = logging.INFO) -> Optional[Path]:
    """Set up logging; returns the log file path, or None if no file could be opened.

    Safe in a windowed (no-console) PyInstaller app, where ``sys.stdout`` is None:
    the console handler is only added when a console exists. Also routes uncaught
    exceptions into the log, since a windowed app has nowhere else to show them.
    """
    handlers = []
    log_file: Optional[Path] = None
    try:
        directory = log_directory()
        directory.mkdir(parents=True, exist_ok=True)
        log_file = directory / "studio.log"
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    except OSError:
        log_file = None   # read-only location etc.: keep running without a file log
    if sys.stdout is not None:
        handlers.append(logging.StreamHandler(sys.stdout))

    logging.basicConfig(level=level, format=_FORMAT, handlers=handlers, force=True)

    def _log_uncaught(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        logging.getLogger("uncaught").critical("Unhandled exception", exc_info=(exc_type, exc, tb))

    sys.excepthook = _log_uncaught
    return log_file
