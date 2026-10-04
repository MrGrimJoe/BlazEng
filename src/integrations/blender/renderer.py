"""
Blender headless rendering bridge.

Drop-in alternative to GodotRenderer: same ``render_shot`` signature, same
return value (a sorted list of PNG frame paths), same error behaviour.

Where Godot composes flat 2D sprites, Blender builds a real 3D scene from the
renderer-neutral ``<shot>.scene.json`` that SceneComposer writes next to each
Godot ``.tscn``: character image planes on a floor with a back wall, a
perspective camera chosen from the shot's camera angle (with a slow push-in
over the shot), and sun + fill lighting chosen from the shot's lighting.

Verified against a real Blender binary (4.0.2) rendered under Xvfb:

* ``--background`` alone is not enough for EEVEE, which needs an OpenGL
  context; running under ``xvfb-run`` gives it Mesa's software rasteriser and
  it renders fine on a GPU-less machine.
* Engines: ``eevee`` (default, ~1-2 s/frame at 640x360 on CPU-only Mesa),
  ``cycles`` (path-traced CPU, much slower but higher quality) and
  ``workbench`` (fastest, flat shading, for quick previews).
"""

import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_FPS = 24
_DEFAULT_TIMEOUT_SECONDS = 600
_VALID_ENGINES = ("eevee", "cycles", "workbench")
_SCRIPT = Path(__file__).with_name("blender_scene.py")


class BlenderRenderError(Exception):
    """Raised when Blender fails to render a scene, times out, or is missing."""


class BlenderRenderer:
    """Invokes Blender headlessly (via Xvfb) to render a shot to PNG frames."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        storage_path = Path(config.get("storage_path", "./storage"))
        self.blender_binary = str(config.get("blender_path", "blender"))
        self.frames_dir = storage_path / "renders"
        self.frames_dir.mkdir(parents=True, exist_ok=True)
        self.fps = int(config.get("render_fps", _DEFAULT_FPS))
        self.width = int(config.get("render_width", 1152))
        self.height = int(config.get("render_height", 648))
        self.timeout_seconds = int(config.get("render_timeout_seconds", _DEFAULT_TIMEOUT_SECONDS))

        engine = str(config.get("blender_engine", "eevee")).lower()
        if engine not in _VALID_ENGINES:
            raise BlenderRenderError(
                f"Unknown blender_engine '{engine}'. Choose one of: {', '.join(_VALID_ENGINES)}"
            )
        self.engine = engine
        self.samples = int(config.get("blender_samples", 16))

    # ------------------------------------------------------------------

    def resolve_binary(self) -> Optional[str]:
        """Absolute path of the Blender executable, or None if not found."""
        return shutil.which(self.blender_binary)

    def render_shot(
        self,
        scene_path: Path,
        shot_id: str,
        duration_seconds: float = 4.0,
        num_frames: Optional[int] = None,
    ) -> List[Path]:
        """Render the shot described next to ``scene_path`` and return its frames.

        ``scene_path`` is the .tscn SceneComposer produced; the Blender backend
        reads the sibling ``.scene.json`` instead. Raises BlenderRenderError if
        Blender or xvfb-run is missing, the scene JSON is absent, the process
        times out or exits non-zero, or no frames are produced.
        """
        binary = self.resolve_binary()
        if binary is None:
            raise BlenderRenderError(
                f"Blender executable '{self.blender_binary}' not found. Install Blender "
                "(https://www.blender.org/download/ or `sudo apt install blender`) or set "
                "blender_path in config.yaml."
            )
        if shutil.which("xvfb-run") is None:
            raise BlenderRenderError(
                "xvfb-run is not installed. On Debian/Ubuntu: sudo apt install xvfb. "
                "EEVEE needs an OpenGL context even with --background."
            )

        scene_json = Path(scene_path).with_suffix(".scene.json")
        if not scene_json.exists():
            raise BlenderRenderError(
                f"No scene description at {scene_json}. The Blender backend renders from the "
                ".scene.json that SceneComposer writes next to each .tscn."
            )

        frames = num_frames if num_frames is not None else max(1, round(duration_seconds * self.fps))
        shot_dir = self.frames_dir / _sanitize(shot_id)
        shot_dir.mkdir(parents=True, exist_ok=True)
        for stale in shot_dir.glob("*.png"):
            stale.unlink()

        cmd = [
            "xvfb-run", "-a",
            binary, "--background", "--factory-startup",
            "--python", str(_SCRIPT),
            "--",
            str(scene_json.resolve()), str(shot_dir.resolve()),
            str(frames), str(self.fps), str(self.width), str(self.height),
            self.engine, str(self.samples),
        ]

        logger.info(f"Blender rendering {shot_id}: {frames} frames @ {self.fps}fps ({self.engine})")
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired as e:
            raise BlenderRenderError(
                f"Blender render of '{shot_id}' timed out after {self.timeout_seconds}s"
            ) from e

        if result.returncode != 0 or "BLAZENG_RENDER_OK" not in result.stdout:
            tail = (result.stderr or result.stdout)[-2000:]
            raise BlenderRenderError(
                f"Blender failed rendering '{shot_id}' (exit {result.returncode}).\n"
                f"output (last 2000 chars): {tail}"
            )

        output_frames = sorted(shot_dir.glob("frame*.png"))
        if not output_frames:
            raise BlenderRenderError(
                f"Blender exited successfully but produced no frames for '{shot_id}'."
            )
        if len(output_frames) != frames:
            logger.warning(f"Expected {frames} frames for '{shot_id}', got {len(output_frames)}")
        logger.info(f"Blender rendered {len(output_frames)} frames for {shot_id}")
        return output_frames


def _sanitize(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", name.strip()) or "shot"
