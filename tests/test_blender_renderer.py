"""
Tests for BlenderRenderer, the scene JSON SceneComposer emits for it, and the
renderer factory.

Most tests mock the subprocess (fast, no Blender needed). The ``Real`` class
shells out to an actual Blender binary under Xvfb and is skipped automatically
when Blender or xvfb-run is not installed, so CI without them stays green.
"""

import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.core.director.director import Shot
from src.core.scene_composer.scene_composer import SceneComposer
from src.integrations.blender.renderer import BlenderRenderError, BlenderRenderer
from src.integrations.renderer_factory import get_renderer, resolve_renderer_name
from src.integrations.godot.renderer import GodotRenderer

HAVE_BLENDER = shutil.which("blender") is not None and shutil.which("xvfb-run") is not None


def _make_png(path: Path, size=(64, 128), color=(200, 60, 60, 255)) -> Path:
    from PIL import Image

    Image.new("RGBA", size, color).save(path)
    return path


@pytest.fixture
def config(tmp_path):
    return {
        "storage_path": str(tmp_path / "storage"),
        "render_width": 320,
        "render_height": 180,
        "render_fps": 24,
        "blender_path": "blender",
    }


@pytest.fixture
def composed(config, tmp_path):
    """A composed two-character shot: returns (scene_path, shot)."""
    composer = SceneComposer(config)
    shot = Shot(
        shot_id="shot_001",
        scene_description="Two people talk",
        characters=["Ann", "Bob"],
        camera_angle="medium shot",
        lighting="bright daylight",
        duration_seconds=1.0,
    )
    assets = {
        "Ann": _make_png(tmp_path / "ann.png"),
        "Bob": _make_png(tmp_path / "bob.png", color=(40, 120, 200, 255)),
    }
    return composer.compose_shot(shot, assets), shot


# ---------------------------------------------------------------------------
# Scene JSON written by SceneComposer
# ---------------------------------------------------------------------------

class TestSceneJson:
    def test_json_written_next_to_tscn(self, composed):
        scene_path, _ = composed
        assert SceneComposer.scene_json_path(scene_path).exists()
        assert scene_path.exists()  # Godot output unaffected

    def test_json_contents(self, composed):
        scene_path, shot = composed
        data = json.loads(SceneComposer.scene_json_path(scene_path).read_text())
        assert data["version"] == 1
        assert data["shot_id"] == "shot_001"
        assert data["camera_angle"] == "medium shot"
        assert [c["name"] for c in data["characters"]] == ["Ann", "Bob"]

    def test_characters_are_left_to_right_and_normalised(self, composed):
        scene_path, _ = composed
        data = json.loads(SceneComposer.scene_json_path(scene_path).read_text())
        xs = [c["x"] for c in data["characters"]]
        assert xs[0] < 0 < xs[1]
        assert all(-1.0 <= x <= 1.0 for x in xs)

    def test_image_paths_are_absolute(self, composed):
        scene_path, _ = composed
        data = json.loads(SceneComposer.scene_json_path(scene_path).read_text())
        assert all(Path(c["image"]).is_absolute() for c in data["characters"])

    def test_no_characters_still_valid(self, config):
        composer = SceneComposer(config)
        shot = Shot(shot_id="empty", scene_description="Empty room", characters=[])
        data = json.loads(SceneComposer.scene_json_path(composer.compose_shot(shot, {})).read_text())
        assert data["characters"] == []


# ---------------------------------------------------------------------------
# BlenderRenderer: configuration and error paths (mocked)
# ---------------------------------------------------------------------------

class TestBlenderRendererConfig:
    def test_defaults(self, config):
        r = BlenderRenderer(config)
        assert r.engine == "eevee"
        assert r.fps == 24

    def test_unknown_engine_rejected(self, config):
        config["blender_engine"] = "povray"
        with pytest.raises(BlenderRenderError, match="Unknown blender_engine"):
            BlenderRenderer(config)

    def test_engine_is_case_insensitive(self, config):
        config["blender_engine"] = "CYCLES"
        assert BlenderRenderer(config).engine == "cycles"


class TestBlenderRendererErrors:
    def test_missing_binary(self, config, composed):
        config["blender_path"] = "definitely-not-blender"
        with pytest.raises(BlenderRenderError, match="not found"):
            BlenderRenderer(config).render_shot(composed[0], "shot_001")

    def test_missing_xvfb(self, config, composed):
        with patch("shutil.which", side_effect=lambda n: "/usr/bin/blender" if n == "blender" else None):
            with pytest.raises(BlenderRenderError, match="xvfb-run"):
                BlenderRenderer(config).render_shot(composed[0], "shot_001")

    def test_missing_scene_json(self, config, tmp_path):
        tscn = tmp_path / "lonely.tscn"
        tscn.write_text("x")
        with patch("shutil.which", return_value="/usr/bin/x"):
            with pytest.raises(BlenderRenderError, match="scene description"):
                BlenderRenderer(config).render_shot(tscn, "lonely")

    def _run(self, config, composed, completed=None, side_effect=None):
        with patch("shutil.which", return_value="/usr/bin/x"), \
             patch("subprocess.run", return_value=completed, side_effect=side_effect) as run:
            try:
                return BlenderRenderer(config).render_shot(composed[0], "shot_001", num_frames=3), run
            except BlenderRenderError as e:
                return e, run

    def test_timeout(self, config, composed):
        err, _ = self._run(config, composed, side_effect=subprocess.TimeoutExpired("blender", 1))
        assert isinstance(err, BlenderRenderError) and "timed out" in str(err)

    def test_nonzero_exit(self, config, composed):
        done = MagicMock(returncode=1, stdout="", stderr="boom")
        err, _ = self._run(config, composed, completed=done)
        assert isinstance(err, BlenderRenderError) and "boom" in str(err)

    def test_zero_exit_without_ok_marker_is_failure(self, config, composed):
        done = MagicMock(returncode=0, stdout="Blender quit", stderr="")
        err, _ = self._run(config, composed, completed=done)
        assert isinstance(err, BlenderRenderError)

    def test_ok_marker_but_no_frames(self, config, composed):
        done = MagicMock(returncode=0, stdout="BLAZENG_RENDER_OK frames=3", stderr="")
        err, _ = self._run(config, composed, completed=done)
        assert isinstance(err, BlenderRenderError) and "no frames" in str(err)

    def test_command_shape(self, config, composed):
        def fake_run(cmd, **kw):
            out_dir = Path(cmd[cmd.index("--") + 2])
            for i in range(1, 4):
                (out_dir / f"frame{i:04d}.png").write_bytes(b"png")
            return MagicMock(returncode=0, stdout="BLAZENG_RENDER_OK frames=3", stderr="")

        with patch("shutil.which", return_value="/usr/bin/x"), patch("subprocess.run", side_effect=fake_run) as run:
            frames = BlenderRenderer(config).render_shot(composed[0], "shot_001", num_frames=3)
        cmd = run.call_args[0][0]
        assert cmd[:2] == ["xvfb-run", "-a"]
        assert "--background" in cmd and "--factory-startup" in cmd
        sep = cmd.index("--")
        assert cmd[sep + 3] == "3"             # frame count
        assert cmd[sep + 5] == "320"           # width from config
        assert len(frames) == 3

    def test_stale_frames_cleared(self, config, composed):
        r = BlenderRenderer(config)
        shot_dir = r.frames_dir / "shot_001"
        shot_dir.mkdir(parents=True)
        stale = shot_dir / "frame9999.png"
        stale.write_bytes(b"old")

        def fake_run(cmd, **kw):
            (shot_dir / "frame0001.png").write_bytes(b"new")
            return MagicMock(returncode=0, stdout="BLAZENG_RENDER_OK", stderr="")

        with patch("shutil.which", return_value="/usr/bin/x"), patch("subprocess.run", side_effect=fake_run):
            frames = r.render_shot(composed[0], "shot_001", num_frames=1)
        assert not stale.exists()
        assert [f.name for f in frames] == ["frame0001.png"]


# ---------------------------------------------------------------------------
# Renderer factory
# ---------------------------------------------------------------------------

class TestRendererFactory:
    def test_explicit_blender(self, config):
        config["renderer"] = "blender"
        assert isinstance(get_renderer(config), BlenderRenderer)

    def test_explicit_godot(self, config):
        config["renderer"] = "godot"
        assert isinstance(get_renderer(config), GodotRenderer)

    def test_unknown_renderer(self, config):
        config["renderer"] = "unreal"
        with pytest.raises(ValueError, match="Unknown renderer"):
            get_renderer(config)

    def test_auto_prefers_godot_when_binary_exists(self, config, tmp_path):
        godot = tmp_path / "godot"
        godot.write_text("")
        config["godot_binary_path"] = str(godot)
        assert resolve_renderer_name(config) == "godot"

    def test_auto_falls_back_to_blender(self, config, tmp_path):
        config["godot_binary_path"] = str(tmp_path / "nope")
        with patch("shutil.which", return_value="/usr/bin/blender"):
            assert resolve_renderer_name(config) == "blender"

    def test_auto_defaults_to_godot_when_neither_found(self, config, tmp_path):
        config["godot_binary_path"] = str(tmp_path / "nope")
        with patch("shutil.which", return_value=None):
            assert resolve_renderer_name(config) == "godot"


# ---------------------------------------------------------------------------
# Real Blender (skipped when not installed)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not HAVE_BLENDER, reason="needs blender and xvfb-run on PATH")
class TestRealBlender:
    def test_renders_real_frames(self, config, composed):
        scene_path, _ = composed
        frames = BlenderRenderer(config).render_shot(scene_path, "shot_001", num_frames=2)
        assert len(frames) == 2
        from PIL import Image

        with Image.open(frames[0]) as im:
            assert im.size == (320, 180)

    def test_frames_differ_over_time_camera_moves(self, config, composed):
        from PIL import Image, ImageChops

        frames = BlenderRenderer(config).render_shot(composed[0], "shot_001", num_frames=6)
        with Image.open(frames[0]) as a, Image.open(frames[-1]) as b:
            assert ImageChops.difference(a.convert("RGB"), b.convert("RGB")).getbbox() is not None

    def test_characters_actually_appear_in_the_render(self, config, composed):
        """Ann's colour (red) must be on the left half, Bob's (blue) on the right."""
        from PIL import Image

        frames = BlenderRenderer(config).render_shot(composed[0], "shot_001", num_frames=1)
        with Image.open(frames[0]).convert("RGB") as im:
            w, h = im.size

            def count(box, pred):
                region = im.crop(box)
                return sum(1 for px in region.getdata() if pred(px))

            red = lambda p: p[0] > 140 and p[1] < 110 and p[2] < 110
            blue = lambda p: p[2] > 140 and p[0] < 110
            assert count((0, 0, w // 2, h), red) > count((w // 2, 0, w, h), red)
            assert count((w // 2, 0, w, h), blue) > count((0, 0, w // 2, h), blue)

    def test_bad_scene_json_version_fails_cleanly(self, config, composed):
        scene_path, _ = composed
        jp = SceneComposer.scene_json_path(scene_path)
        data = json.loads(jp.read_text())
        data["version"] = 99
        jp.write_text(json.dumps(data))
        with pytest.raises(BlenderRenderError):
            BlenderRenderer(config).render_shot(scene_path, "shot_001", num_frames=1)
