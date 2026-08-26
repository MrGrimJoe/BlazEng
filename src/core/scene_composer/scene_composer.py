"""
SceneComposer — builds Godot scene files from a Shot and its asset images.

Design choices here are the result of hands-on verification against a real
Godot 4.7.2 binary (see ARCHITECTURE.md "Godot rendering notes"), not just
reading docs:

1. Character/asset images are loaded at RUNTIME via a generated GDScript
   (`Image.load()` + `ImageTexture.create_from_image()`), not baked in as
   static `ExtResource` references. A freshly-generated PNG has no Godot
   `.import` sidecar file, and Godot's resource loader refuses to load an
   un-imported image as an ExtResource — it only works for images that have
   already been through the editor's import pipeline. Runtime loading
   sidesteps that entirely, which matters here because every asset is
   freshly generated and never opened in the Godot editor.

2. Composer writes one .tscn per shot into a SHARED project directory
   (one project.godot for the whole production), rather than one full
   Godot project per shot. The renderer invokes Godot with the specific
   scene path as a positional argument, which overrides the project's
   default `run/main_scene` — verified working, and much cheaper than
   maintaining N separate project directories.
"""

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Fallback viewport size if config doesn't specify render_width/render_height
# (matches Godot's own built-in default, verified against a real binary).
_DEFAULT_VIEWPORT_WIDTH = 1152
_DEFAULT_VIEWPORT_HEIGHT = 648

# Rough camera-angle -> framing heuristics. "Framing" here just means how
# much of the viewport a character sprite occupies and where it sits
# vertically — a real implementation would eventually replace this with
# actual 3D camera placement, but for the current 2D-sprite-composition
# architecture (see ARCHITECTURE.md) this is the documented interim model.
_CAMERA_ANGLE_SCALE = {
    "wide shot": 0.5,
    "wide": 0.5,
    "medium shot": 1.0,
    "medium": 1.0,
    "close-up": 1.8,
    "close up": 1.8,
    "closeup": 1.8,
    "overhead": 0.7,
}
_DEFAULT_SCALE = 1.0

# Lighting keyword -> a dim (r, g, b, a) modulate applied to the whole
# scene root, as a simple stand-in for real lighting until SceneComposer
# grows an actual 2D light/environment setup.
_LIGHTING_MODULATE = {
    "dim": (0.55, 0.55, 0.6, 1.0),
    "dark": (0.35, 0.35, 0.4, 1.0),
    "night": (0.3, 0.3, 0.45, 1.0),
    "shadow": (0.45, 0.45, 0.5, 1.0),
    "bright": (1.15, 1.13, 1.05, 1.0),
    "daylight": (1.1, 1.1, 1.0, 1.0),
    "harsh": (1.2, 1.15, 1.05, 1.0),
}
_DEFAULT_MODULATE = (1.0, 1.0, 1.0, 1.0)


class SceneComposerError(Exception):
    """Raised when a scene can't be composed (missing assets, bad paths)."""


@dataclass
class CharacterPlacement:
    name: str
    image_path: Path
    position: "tuple[float, float]"
    scale: float


class SceneComposer:
    """Builds a Godot .tscn scene file for one shot from its asset images."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.storage_path = Path(config.get("storage_path", "./storage"))
        self.project_dir = self.storage_path / "godot_project"
        self.scenes_dir = self.project_dir / "scenes"
        self.scenes_dir.mkdir(parents=True, exist_ok=True)
        self.viewport_width = int(config.get("render_width", _DEFAULT_VIEWPORT_WIDTH))
        self.viewport_height = int(config.get("render_height", _DEFAULT_VIEWPORT_HEIGHT))
        self._ensure_project_file()

    def _ensure_project_file(self) -> None:
        """Write a single shared project.godot if one doesn't exist yet."""
        project_file = self.project_dir / "project.godot"
        if project_file.exists():
            return
        project_file.write_text(
            'config_version=5\n\n'
            '[application]\n'
            'config/name="BlazEngProduction"\n'
            'run/main_scene="res://scenes/_default.tscn"\n\n'
            '[display]\n'
            f'window/size/viewport_width={self.viewport_width}\n'
            f'window/size/viewport_height={self.viewport_height}\n\n'
            '[rendering]\n'
            'renderer/rendering_method="gl_compatibility"\n'
        )
        # A minimal default scene so the project is valid even if nothing
        # ever renders it directly (each real render passes its own scene
        # path explicitly — see GodotRenderer).
        default_scene = self.scenes_dir / "_default.tscn"
        default_scene.write_text(
            '[gd_scene load_steps=1 format=3]\n\n'
            '[node name="Root" type="Node2D"]\n'
        )
        logger.info(f"Godot project initialized: {self.project_dir}")

    def compose_shot(self, shot, assets: Dict[str, Path]) -> Path:
        """Build a .tscn scene for `shot` using `assets` (name -> image path).

        Returns the absolute path to the generated .tscn file. Raises
        SceneComposerError if a character in shot.characters has no
        corresponding entry in `assets`.
        """
        placements = self._layout_characters(shot, assets)
        modulate = self._modulate_for_lighting(shot.lighting)
        gdscript = self._build_gdscript(placements, modulate)

        scene_path = self.scenes_dir / f"{_sanitize_shot_id(shot.shot_id)}.tscn"
        scene_path.write_text(self._build_tscn(gdscript))
        logger.info(f"Composed scene for {shot.shot_id}: {scene_path}")
        return scene_path

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _layout_characters(self, shot, assets: Dict[str, Path]) -> List[CharacterPlacement]:
        characters = list(shot.characters) or []
        placements = []

        scale = _CAMERA_ANGLE_SCALE.get(shot.camera_angle.lower(), _DEFAULT_SCALE)

        if not characters:
            return placements

        # Simple horizontal spread across the viewport, characters
        # left-to-right in the order they appear in the shot.
        margin = self.viewport_width * 0.15
        usable_width = self.viewport_width - 2 * margin
        step = usable_width / max(len(characters), 1)
        y = self.viewport_height * 0.6

        for i, name in enumerate(characters):
            if name not in assets:
                raise SceneComposerError(
                    f"Shot '{shot.shot_id}' references character '{name}' "
                    f"but no asset image was provided for it"
                )
            x = margin + step * (i + 0.5)
            placements.append(
                CharacterPlacement(
                    name=name,
                    image_path=Path(assets[name]).resolve(),
                    position=(x, y),
                    scale=scale,
                )
            )
        return placements

    @staticmethod
    def _modulate_for_lighting(lighting: str) -> "tuple[float, float, float, float]":
        lighting_lower = (lighting or "").lower()
        for keyword, modulate in _LIGHTING_MODULATE.items():
            if keyword in lighting_lower:
                return modulate
        return _DEFAULT_MODULATE

    # ------------------------------------------------------------------
    # GDScript / scene text generation
    # ------------------------------------------------------------------

    def _build_gdscript(
        self,
        placements: List[CharacterPlacement],
        modulate: "tuple[float, float, float, float]",
    ) -> str:
        lines = ["extends Node2D", "", "func _ready():"]
        lines.append(f"\tmodulate = Color({modulate[0]}, {modulate[1]}, {modulate[2]}, {modulate[3]})")

        if not placements:
            lines.append("\tpass  # no characters in this shot")
        for i, p in enumerate(placements):
            # res:// paths must be forward-slash and relative to the
            # project root — assets live outside the project dir, so we
            # use an absolute filesystem path via Image.load(), which
            # Godot accepts even outside res:// (verified: Image.load()
            # takes any OS path, not just res://).
            escaped_path = str(p.image_path).replace("\\", "\\\\").replace('"', '\\"')
            lines.extend([
                f"\tvar img_{i} = Image.new()",
                f"\tvar err_{i} = img_{i}.load(\"{escaped_path}\")",
                f"\tif err_{i} == OK:",
                f"\t\tvar tex_{i} = ImageTexture.create_from_image(img_{i})",
                f"\t\tvar sprite_{i} = Sprite2D.new()",
                f"\t\tsprite_{i}.texture = tex_{i}",
                f"\t\tsprite_{i}.position = Vector2({p.position[0]}, {p.position[1]})",
                f"\t\tsprite_{i}.scale = Vector2({p.scale}, {p.scale})",
                f"\t\tadd_child(sprite_{i})",
                f"\telse:",
                f"\t\tpush_error(\"Failed to load asset image for '{_gd_escape(p.name)}': \" + str(err_{i}))",
            ])
        return "\n".join(lines) + "\n"

    @staticmethod
    def _build_tscn(gdscript_source: str) -> str:
        # GDScript source embedded in a .tscn must have its own quotes
        # escaped and newlines literal-escaped for the text resource format.
        escaped = gdscript_source.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return (
            '[gd_scene load_steps=2 format=3]\n\n'
            '[sub_resource type="GDScript" id="1"]\n'
            f'script/source = "{escaped}"\n\n'
            '[node name="Root" type="Node2D"]\n'
            'script = SubResource("1")\n'
        )


def _sanitize_shot_id(shot_id: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", shot_id.strip()) or "shot"


def _gd_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', "'")
