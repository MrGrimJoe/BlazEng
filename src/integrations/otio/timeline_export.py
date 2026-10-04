"""
OpenTimelineIO export: one editable timeline for the finished production.

Each shot becomes a clip on a single video track, in production order, whose
media reference is that shot's encoded segment. Scene metadata (description,
camera, lighting, characters, action) rides along under the ``blazeng`` key of
each clip's metadata so an editor can see what the shot was meant to be.

The timeline is written and read back through the ``opentimelineio`` library
itself (see tests/test_timeline_export.py), so it is a valid .otio file. It has
NOT been opened in Premiere or DaVinci Resolve from this project's
environment; those apps import OTIO through their own adapters, so treat
"imports cleanly into your editor" as the one thing still to confirm on your
machine.
"""

import logging
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

logger = logging.getLogger(__name__)


class TimelineExportError(Exception):
    """Raised when a timeline cannot be built (OTIO missing, no clips, ...)."""


def export_timeline(
    shots: Iterable[Dict[str, Any]],
    segment_paths: Dict[str, Path],
    output_path: Path,
    fps: int = 24,
    name: str = "BlazEng Production",
) -> Path:
    """Write ``output_path`` (.otio) and return it.

    ``shots``: ordered shot dicts (shot_id, duration_seconds, scene_description,
    camera_angle, lighting, characters, action). Shots without an encoded
    segment in ``segment_paths`` are skipped (e.g. a shot that failed to render).
    """
    try:
        import opentimelineio as otio
    except ImportError as e:  # pragma: no cover - exercised only without otio
        raise TimelineExportError(
            "opentimelineio is not installed. Run: pip install opentimelineio"
        ) from e

    timeline = otio.schema.Timeline(name=name)
    track = otio.schema.Track(name="Video 1", kind=otio.schema.TrackKind.Video)
    timeline.tracks.append(track)

    clip_count = 0
    for shot in shots:
        shot_id = shot["shot_id"]
        media = segment_paths.get(shot_id)
        if media is None:
            logger.warning(f"Timeline: skipping {shot_id} (no encoded segment)")
            continue

        duration = otio.opentime.RationalTime(
            round(float(shot.get("duration_seconds", 4.0)) * fps), fps
        )
        available = otio.opentime.TimeRange(otio.opentime.RationalTime(0, fps), duration)
        reference = otio.schema.ExternalReference(
            target_url=Path(media).resolve().as_uri(), available_range=available
        )
        clip = otio.schema.Clip(
            name=shot_id,
            media_reference=reference,
            source_range=available,
            metadata={
                "blazeng": {
                    "scene_description": shot.get("scene_description", ""),
                    "camera_angle": shot.get("camera_angle", ""),
                    "lighting": shot.get("lighting", ""),
                    "characters": list(shot.get("characters", [])),
                    "action": shot.get("action", ""),
                }
            },
        )
        track.append(clip)
        clip_count += 1

    if clip_count == 0:
        raise TimelineExportError("No shots with encoded media — nothing to put on the timeline")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    otio.adapters.write_to_file(timeline, str(output_path))
    logger.info(f"Timeline written: {output_path} ({clip_count} clips @ {fps}fps)")
    return output_path
