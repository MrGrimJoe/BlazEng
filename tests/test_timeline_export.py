"""OpenTimelineIO export — written and read back through the otio library itself."""

from pathlib import Path

import pytest

otio = pytest.importorskip("opentimelineio")

from src.integrations.otio.timeline_export import TimelineExportError, export_timeline


def _shots():
    return [
        {"shot_id": "shot_001", "duration_seconds": 4.0, "scene_description": "Rain on a street",
         "camera_angle": "wide shot", "lighting": "night", "characters": ["Ann"], "action": "walks"},
        {"shot_id": "shot_002", "duration_seconds": 2.5, "scene_description": "A clue",
         "camera_angle": "close-up", "lighting": "dim", "characters": ["Ann", "Bob"], "action": "kneels"},
    ]


@pytest.fixture
def media(tmp_path):
    paths = {}
    for sid in ("shot_001", "shot_002"):
        p = tmp_path / f"{sid}.mp4"
        p.write_bytes(b"x")
        paths[sid] = p
    return paths


def test_roundtrip_clips_in_order(tmp_path, media):
    out = export_timeline(_shots(), media, tmp_path / "t.otio", fps=24)
    tl = otio.adapters.read_from_file(str(out))
    clips = list(tl.find_clips())
    assert [c.name for c in clips] == ["shot_001", "shot_002"]


def test_durations_and_total(tmp_path, media):
    out = export_timeline(_shots(), media, tmp_path / "t.otio", fps=24)
    tl = otio.adapters.read_from_file(str(out))
    durations = [c.source_range.duration.to_seconds() for c in tl.find_clips()]
    assert durations == [4.0, 2.5]
    assert tl.duration().to_seconds() == pytest.approx(6.5)


def test_media_reference_points_at_file(tmp_path, media):
    out = export_timeline(_shots(), media, tmp_path / "t.otio")
    clip = list(otio.adapters.read_from_file(str(out)).find_clips())[0]
    assert clip.media_reference.target_url.startswith("file://")
    assert clip.media_reference.target_url.endswith("shot_001.mp4")


def test_scene_metadata_preserved(tmp_path, media):
    out = export_timeline(_shots(), media, tmp_path / "t.otio")
    clip = list(otio.adapters.read_from_file(str(out)).find_clips())[1]
    meta = clip.metadata["blazeng"]
    assert meta["camera_angle"] == "close-up" and list(meta["characters"]) == ["Ann", "Bob"]


def test_shots_without_media_are_skipped(tmp_path, media):
    del media["shot_002"]
    out = export_timeline(_shots(), media, tmp_path / "t.otio")
    assert [c.name for c in otio.adapters.read_from_file(str(out)).find_clips()] == ["shot_001"]


def test_nothing_to_export_raises(tmp_path):
    with pytest.raises(TimelineExportError):
        export_timeline(_shots(), {}, tmp_path / "t.otio")


def test_creates_parent_directory(tmp_path, media):
    out = export_timeline(_shots(), media, tmp_path / "deep" / "dir" / "t.otio")
    assert out.exists()
