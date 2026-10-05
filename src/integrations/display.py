"""
Virtual-display handling shared by the Godot and Blender renderers.

Linux servers and CI runners have no display, and both engines need an OpenGL
context to render, so on Linux every render command is wrapped in
``xvfb-run`` (Mesa software rendering). Windows and macOS always have a window
system available, so there the engine is launched directly and ``xvfb-run``
(which doesn't exist there) must not be required.
"""

import shutil
import subprocess
import sys
from typing import List, Optional


def needs_virtual_display() -> bool:
    return sys.platform.startswith("linux")


def missing_display_tool() -> Optional[str]:
    """Return an install hint if a required virtual-display tool is absent, else None."""
    if needs_virtual_display() and shutil.which("xvfb-run") is None:
        return "xvfb-run is not installed. On Debian/Ubuntu: sudo apt install xvfb."
    return None


def wrap_command(cmd: List[str]) -> List[str]:
    """Prefix ``cmd`` with ``xvfb-run -a`` on Linux; return it unchanged elsewhere."""
    return ["xvfb-run", "-a", *cmd] if needs_virtual_display() else list(cmd)


def subprocess_kwargs() -> dict:
    """Extra ``subprocess.run`` kwargs: on Windows, don't flash a console window.

    The installed GUI is a windowed app, so every ffmpeg/Godot/Blender child
    process would otherwise pop up its own black console window.
    """
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}
