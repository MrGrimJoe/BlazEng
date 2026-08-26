"""
Godot headless rendering bridge.

The invocation recipe here was verified against a real Godot 4.7.2 binary,
not assumed from docs — two findings changed the design from what a naive
reading of `godot --help` would suggest:

1. `--headless` alone forces Godot's "dummy" rendering driver, which has
   no real rasterization pipeline. Asking it to `--write-movie` a scene
   with actual visible content (a textured sprite) segfaults
   (`texture_2d_get`: Parameter "t" is null). The working alternative is
   running under a virtual X display (Xvfb) with a real software
   rendering driver (`--rendering-driver opengl3`, backed by llvmpipe on
   a GPU-less machine) — this actually renders and writes frames.

2. A scene file passed as a positional argument
   (`godot --path <project_dir> <scene>.tscn`) overrides the project's
   `run/main_scene` for that invocation — confirmed by rendering two
   different scenes from the same project and getting different pixels
   out. This means one shared Godot project can serve every shot; each
   render just points at a different generated .tscn.

This module assumes `xvfb-run` is available on the host (installed by
setup.py alongside Godot itself — see CONTRIBUTING.md Priority 2 for the
setup.py change this depends on, which is not yet wired in).
"""

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_FPS = 24
_DEFAULT_TIMEOUT_SECONDS = 120


class GodotRenderError(Exception):
    """Raised when Godot fails to render a scene, times out, or is missing."""


class GodotRenderer:
    """Invokes a Godot binary headlessly (via Xvfb) to render a scene to frames."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        storage_path = Path(config.get("storage_path", "./storage"))
        self.godot_binary = Path(config.get("godot_binary_path", "./bin/godot"))
        self.frames_dir = storage_path / "renders"
        self.frames_dir.mkdir(parents=True, exist_ok=True)
        self.fps = int(config.get("render_fps", _DEFAULT_FPS))
        self.timeout_seconds = int(config.get("render_timeout_seconds", _DEFAULT_TIMEOUT_SECONDS))

    def render_shot(
        self,
        scene_path: Path,
        shot_id: str,
        duration_seconds: float = 4.0,
        num_frames: Optional[int] = None,
    ) -> List[Path]:
        """Render `scene_path` and return the list of output frame PNG paths.

        Frame count is `num_frames` if given, otherwise derived from
        `duration_seconds` at the configured FPS (PipelineOrchestrator
        passes through Shot.duration_seconds in practice).
        Raises GodotRenderError if the binary is missing, xvfb-run is
        missing, the process times out, or it exits non-zero.
        """
        if not self.godot_binary.exists():
            raise GodotRenderError(
                f"Godot binary not found at {self.godot_binary}. "
                "Run setup.py to download it, or set godot_binary_path in config.yaml."
            )
        if shutil.which("xvfb-run") is None:
            raise GodotRenderError(
                "xvfb-run is not installed. On Debian/Ubuntu: sudo apt install xvfb. "
                "Real (non-dummy) rendering requires a virtual display — see "
                "this module's docstring for why --headless alone doesn't work."
            )

        frames = num_frames if num_frames is not None else max(1, round(duration_seconds * self.fps))
        shot_frames_dir = self.frames_dir / _sanitize(shot_id)
        shot_frames_dir.mkdir(parents=True, exist_ok=True)
        # Clear any stale frames from a previous attempt so a failed
        # partial render can't be mistaken for a complete one.
        for stale in shot_frames_dir.glob("*.png"):
            stale.unlink()

        # CRITICAL: must be absolute. Verified bug — a relative --write-movie
        # path is resolved by Godot against the project directory (--path),
        # not the process's actual working directory. With a relative
        # storage_path in config (the common case, e.g. "./storage"), a
        # relative movie_stub silently wrote frames into
        # <project_dir>/<storage_path>/renders/... instead of the real
        # storage location, and this method then reported "no frame files"
        # because it was looking in the right place for the wrong output.
        movie_stub = (shot_frames_dir / "frame.png").resolve()
        project_dir = scene_path.parent.parent.resolve()  # scenes/<file>.tscn -> project root
        scene_rel = scene_path.resolve().relative_to(project_dir)

        cmd = [
            "xvfb-run", "-a",
            str(self.godot_binary),
            "--path", str(project_dir),
            str(scene_rel),
            "--rendering-driver", "opengl3",
            "--write-movie", str(movie_stub),
            "--quit-after", str(frames),
            "--fixed-fps", str(self.fps),
        ]

        logger.info(f"Rendering {shot_id}: {frames} frames @ {self.fps}fps")
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self.timeout_seconds
            )
        except subprocess.TimeoutExpired as e:
            raise GodotRenderError(
                f"Render of '{shot_id}' timed out after {self.timeout_seconds}s"
            ) from e

        if result.returncode != 0:
            raise GodotRenderError(
                f"Godot exited with code {result.returncode} rendering '{shot_id}'.\n"
                f"stderr (last 2000 chars): {result.stderr[-2000:]}"
            )

        output_frames = sorted(shot_frames_dir.glob("frame*.png"))
        if not output_frames:
            raise GodotRenderError(
                f"Godot exited successfully but produced no frame files for '{shot_id}'. "
                f"stderr (last 2000 chars): {result.stderr[-2000:]}"
            )
        if len(output_frames) != frames:
            logger.warning(
                f"Expected {frames} frames for '{shot_id}', got {len(output_frames)} "
                "(Godot may have dropped frames near the end of the run)"
            )

        logger.info(f"Rendered {len(output_frames)} frames for {shot_id}")
        return output_frames


def _sanitize(name: str) -> str:
    import re
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", name.strip()) or "shot"
