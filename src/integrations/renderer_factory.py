"""
Picks the render backend from config.

``renderer: godot``   -> GodotRenderer (2D sprite composition)
``renderer: blender`` -> BlenderRenderer (real 3D scene, perspective camera)
``renderer: auto``    -> Godot if its binary is present, else Blender if it is
                         on PATH, else Godot (whose error message then tells the
                         user exactly what to install).
"""

import shutil
from pathlib import Path
from typing import Any, Dict

from .blender.renderer import BlenderRenderer
from .godot.renderer import GodotRenderer

VALID_RENDERERS = ("auto", "godot", "blender")


def resolve_renderer_name(config: Dict[str, Any]) -> str:
    choice = str(config.get("renderer", "auto")).lower()
    if choice not in VALID_RENDERERS:
        raise ValueError(f"Unknown renderer '{choice}'. Choose one of: {', '.join(VALID_RENDERERS)}")
    if choice != "auto":
        return choice
    if Path(config.get("godot_binary_path", "./bin/godot")).exists():
        return "godot"
    if shutil.which(str(config.get("blender_path", "blender"))):
        return "blender"
    return "godot"


def get_renderer(config: Dict[str, Any]):
    """Return the configured renderer instance."""
    if resolve_renderer_name(config) == "blender":
        return BlenderRenderer(config)
    return GodotRenderer(config)
