"""
FFmpeg video assembly — turns per-shot PNG frame sequences into one MP4.

Concatenates each shot's frames as its own encoded segment, then joins
segments via ffmpeg's concat demuxer, since shots may have different
frame counts and this avoids assuming a single global frame index.
"""

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..display import subprocess_kwargs

logger = logging.getLogger(__name__)


class FFmpegError(Exception):
    """Raised when ffmpeg is missing or a render/concat step fails."""


class VideoAssembler:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.ffmpeg_binary = config.get("ffmpeg_path", "ffmpeg")
        self.fps = int(config.get("render_fps", 24))
        self.storage_path = Path(config.get("storage_path", "./storage"))
        self.output_dir = self.storage_path / "output"
        # shot_id -> encoded per-shot segment, filled by assemble(); used by
        # the OpenTimelineIO exporter to point clips at real media files.
        self.segment_paths: Dict[str, Path] = {}

    def assemble(
        self,
        shot_frames: Dict[str, List[Path]],
        output_name: str = "final.mp4",
        shot_audio: Optional[Dict[str, Path]] = None,
    ) -> Path:
        """Build one MP4 from `shot_frames` (shot_id -> ordered frame paths),
        in the order shot_frames is given (Python dicts preserve insertion
        order, so callers should pass shots in production order).

        ``shot_audio`` (shot_id -> WAV) adds each shot's dialogue; shots without
        an entry get silence so segments join cleanly. With no audio at all the
        video has no audio stream.

        Raises FFmpegError if ffmpeg isn't found, a shot has no frames, or
        any ffmpeg invocation fails.
        """
        if shutil.which(self.ffmpeg_binary) is None:
            raise FFmpegError(f"ffmpeg binary not found: {self.ffmpeg_binary!r}")
        if not shot_frames:
            raise FFmpegError("No shots given to assemble")

        self.output_dir.mkdir(parents=True, exist_ok=True)
        segments_dir = self.output_dir / "_segments"
        segments_dir.mkdir(parents=True, exist_ok=True)

        segment_paths = []
        self.segment_paths = {}
        for shot_id, frames in shot_frames.items():
            if not frames:
                raise FFmpegError(f"Shot '{shot_id}' has no frames to encode")
            segment = self._encode_segment(
                shot_id, frames, segments_dir,
                audio=(shot_audio or {}).get(shot_id), with_audio=bool(shot_audio),
            )
            segment_paths.append(segment)
            self.segment_paths[shot_id] = segment

        if len(segment_paths) == 1:
            final_path = self.output_dir / output_name
            shutil.copy2(segment_paths[0], final_path)
            return final_path

        return self._concat_segments(segment_paths, output_name)

    def _encode_segment(
        self, shot_id: str, frames: List[Path], segments_dir: Path,
        audio: Optional[Path] = None, with_audio: bool = False,
    ) -> Path:
        first_frame = Path(frames[0])
        pattern = self._frame_glob_pattern(first_frame)
        segment_path = segments_dir / f"{_sanitize(shot_id)}.mp4"

        cmd = [
            self.ffmpeg_binary, "-y",
            "-framerate", str(self.fps),
            # Renderers number frames from different bases (Godot from 0,
            # Blender from 1); start at the first frame we were actually given
            # rather than relying on ffmpeg's start-number probing.
            "-start_number", str(self._frame_number(first_frame)),
            "-i", str(first_frame.parent / pattern),
        ]
        if with_audio:
            if audio is not None:
                cmd += ["-i", str(audio)]
            else:
                cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono"]
        cmd += [
            # H.264 + yuv420p needs even dimensions; round down if a custom
            # render_width/height is odd instead of failing the whole encode.
            "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
        ]
        if with_audio:
            # Pad/trim the audio to the picture so every segment has identical streams
            # and the concat step can copy them.
            cmd += ["-af", "apad", "-c:a", "aac", "-ar", "44100", "-ac", "1", "-shortest"]
        cmd.append(str(segment_path))
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, **subprocess_kwargs())
        if result.returncode != 0:
            raise FFmpegError(
                f"ffmpeg failed encoding segment for '{shot_id}': {result.stderr[-500:]}"
            )
        return segment_path

    def _concat_segments(self, segment_paths: List[Path], output_name: str) -> Path:
        concat_list = self.output_dir / "_segments" / "concat.txt"
        concat_list.write_text(
            "\n".join(f"file '{p.resolve()}'" for p in segment_paths) + "\n"
        )
        final_path = self.output_dir / output_name
        cmd = [
            self.ffmpeg_binary, "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_list),
            "-c", "copy",
            str(final_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, **subprocess_kwargs())
        if result.returncode != 0:
            raise FFmpegError(f"ffmpeg concat failed: {result.stderr[-500:]}")
        return final_path

    @staticmethod
    def _frame_number(frame: Path) -> int:
        digits = ""
        for ch in reversed(frame.stem):
            if not ch.isdigit():
                break
            digits = ch + digits
        return int(digits) if digits else 0

    @staticmethod
    def _frame_glob_pattern(first_frame: Path) -> str:
        """Godot's --write-movie names frames like frame00000000.png —
        derive an ffmpeg %0Nd pattern from the actual filename rather than
        assuming a fixed digit count."""
        stem = first_frame.stem
        digits = 0
        for ch in reversed(stem):
            if ch.isdigit():
                digits += 1
            else:
                break
        if digits == 0:
            raise FFmpegError(
                f"Frame filename '{first_frame.name}' has no trailing digits — "
                "cannot build an ffmpeg sequence pattern from it"
            )
        prefix = stem[: len(stem) - digits]
        return f"{prefix}%0{digits}d{first_frame.suffix}"


def _sanitize(name: str) -> str:
    import re
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", name.strip()) or "shot"
