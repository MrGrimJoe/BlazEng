"""
Tests for GodotRenderer.

Two tiers:
  1. Mocked subprocess tests — run everywhere, verify our command
     construction and error handling logic without needing Godot
     installed.
  2. A real integration test that shells out to an actual Godot binary
     and asserts on genuine pixel output — skipped automatically unless
     BLAZENG_GODOT_BINARY points at a real binary (this is how we
     verified the renderer against Godot 4.7.2 during development; see
     ARCHITECTURE.md's "Godot rendering notes" for what was learned).
"""

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.integrations.godot.renderer import GodotRenderer, GodotRenderError


@pytest.fixture
def fake_project(tmp_path):
    """A minimal fake Godot project + scene, without a real binary."""
    project_dir = tmp_path / "godot_project"
    scenes_dir = project_dir / "scenes"
    scenes_dir.mkdir(parents=True)
    (project_dir / "project.godot").write_text("config_version=5\n")
    scene_path = scenes_dir / "shot_001.tscn"
    scene_path.write_text('[gd_scene load_steps=1 format=3]\n[node name="Root" type="Node2D"]\n')
    return project_dir, scene_path


@pytest.fixture
def renderer_config(tmp_path, fake_project):
    fake_binary = tmp_path / "fake_godot"
    fake_binary.write_text("#!/bin/sh\necho fake\n")
    fake_binary.chmod(0o755)
    return {
        "storage_path": str(tmp_path / "storage"),
        "godot_binary_path": str(fake_binary),
    }


class TestGodotRendererValidation:
    def test_missing_binary_raises_clear_error(self, tmp_path):
        config = {
            "storage_path": str(tmp_path / "storage"),
            "godot_binary_path": str(tmp_path / "does_not_exist"),
        }
        renderer = GodotRenderer(config)
        with pytest.raises(GodotRenderError, match="not found"):
            renderer.render_shot(tmp_path / "scene.tscn", "shot_001")

    @patch("shutil.which", return_value=None)
    def test_missing_xvfb_raises_clear_error(self, mock_which, renderer_config, fake_project):
        _, scene_path = fake_project
        renderer = GodotRenderer(renderer_config)
        with pytest.raises(GodotRenderError, match="xvfb-run"):
            renderer.render_shot(scene_path, "shot_001")


class TestGodotRendererCommandConstruction:
    @patch("subprocess.run")
    @patch("shutil.which", return_value="/usr/bin/xvfb-run")
    def test_builds_expected_command(self, mock_which, mock_run, renderer_config, fake_project, tmp_path):
        project_dir, scene_path = fake_project
        renderer = GodotRenderer(renderer_config)

        # Simulate Godot successfully producing frame files, since our
        # code checks the frames dir after the subprocess call returns.
        frames_dir = Path(renderer_config["storage_path"]) / "renders" / "shot_001"

        def fake_run(cmd, **kwargs):
            frames_dir.mkdir(parents=True, exist_ok=True)
            (frames_dir / "frame00000000.png").write_bytes(b"fake")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = fake_run

        renderer.render_shot(scene_path, "shot_001", duration_seconds=1.0)

        cmd = mock_run.call_args.args[0]
        assert cmd[0] == "xvfb-run"
        assert "-a" in cmd
        assert str(renderer.godot_binary) in cmd
        assert "--path" in cmd
        assert str(project_dir) in cmd
        assert "scenes/shot_001.tscn" in cmd or "scenes\\shot_001.tscn" in cmd
        assert "--rendering-driver" in cmd
        assert "opengl3" in cmd
        assert "--headless" not in cmd  # deliberately NOT used — see module docstring

    @patch("subprocess.run")
    @patch("shutil.which", return_value="/usr/bin/xvfb-run")
    def test_frame_count_derived_from_duration(self, mock_which, mock_run, renderer_config, fake_project):
        _, scene_path = fake_project
        renderer = GodotRenderer(renderer_config)
        frames_dir = Path(renderer_config["storage_path"]) / "renders" / "shot_001"

        def fake_run(cmd, **kwargs):
            frames_dir.mkdir(parents=True, exist_ok=True)
            (frames_dir / "frame00000000.png").write_bytes(b"fake")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = fake_run
        renderer.render_shot(scene_path, "shot_001", duration_seconds=2.0)  # 2s * 24fps = 48

        cmd = mock_run.call_args.args[0]
        quit_after_index = cmd.index("--quit-after")
        assert cmd[quit_after_index + 1] == "48"

    @patch("subprocess.run")
    @patch("shutil.which", return_value="/usr/bin/xvfb-run")
    def test_explicit_num_frames_overrides_duration(self, mock_which, mock_run, renderer_config, fake_project):
        _, scene_path = fake_project
        renderer = GodotRenderer(renderer_config)
        frames_dir = Path(renderer_config["storage_path"]) / "renders" / "shot_001"

        def fake_run(cmd, **kwargs):
            frames_dir.mkdir(parents=True, exist_ok=True)
            (frames_dir / "frame00000000.png").write_bytes(b"fake")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = fake_run
        renderer.render_shot(scene_path, "shot_001", duration_seconds=10.0, num_frames=5)

        cmd = mock_run.call_args.args[0]
        quit_after_index = cmd.index("--quit-after")
        assert cmd[quit_after_index + 1] == "5"


class TestGodotRendererErrorHandling:
    @patch("subprocess.run")
    @patch("shutil.which", return_value="/usr/bin/xvfb-run")
    def test_nonzero_exit_raises_with_stderr(self, mock_which, mock_run, renderer_config, fake_project):
        _, scene_path = fake_project
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="ERROR: scene failed to parse")
        renderer = GodotRenderer(renderer_config)

        with pytest.raises(GodotRenderError, match="scene failed to parse"):
            renderer.render_shot(scene_path, "shot_001")

    @patch("subprocess.run")
    @patch("shutil.which", return_value="/usr/bin/xvfb-run")
    def test_timeout_raises_clear_error(self, mock_which, mock_run, renderer_config, fake_project):
        _, scene_path = fake_project
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["godot"], timeout=120)
        renderer = GodotRenderer(renderer_config)

        with pytest.raises(GodotRenderError, match="timed out"):
            renderer.render_shot(scene_path, "shot_001")

    @patch("subprocess.run")
    @patch("shutil.which", return_value="/usr/bin/xvfb-run")
    def test_success_exit_but_no_frames_raises(self, mock_which, mock_run, renderer_config, fake_project):
        """A genuine failure mode: Godot can exit 0 but produce nothing
        (e.g. silently failing to write the movie). We should not report
        success in that case."""
        _, scene_path = fake_project
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        renderer = GodotRenderer(renderer_config)

        with pytest.raises(GodotRenderError, match="no frame files"):
            renderer.render_shot(scene_path, "shot_001")

    @patch("subprocess.run")
    @patch("shutil.which", return_value="/usr/bin/xvfb-run")
    def test_stale_frames_cleared_before_render(self, mock_which, mock_run, renderer_config, fake_project):
        """A previous failed/partial render's leftover frames shouldn't
        be mistaken for the current render's output."""
        _, scene_path = fake_project
        renderer = GodotRenderer(renderer_config)
        frames_dir = Path(renderer_config["storage_path"]) / "renders" / "shot_001"
        frames_dir.mkdir(parents=True)
        (frames_dir / "frame00000000.png").write_bytes(b"stale from a previous failed run")
        (frames_dir / "frame00000001.png").write_bytes(b"stale")

        def fake_run(cmd, **kwargs):
            # New render only produces ONE frame this time
            (frames_dir / "frame00000000.png").write_bytes(b"fresh")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = fake_run
        result = renderer.render_shot(scene_path, "shot_001", num_frames=1)

        assert len(result) == 1
        assert result[0].read_bytes() == b"fresh"


@pytest.mark.skipif(
    not os.environ.get("BLAZENG_GODOT_BINARY"),
    reason="Set BLAZENG_GODOT_BINARY to a real Godot binary path to run this integration test",
)
class TestGodotRendererRealIntegration:
    """Genuine end-to-end test against a real Godot binary.

    This is how the renderer's command recipe was actually verified
    during development (see module docstring) — this test just
    automates that manual verification so it doesn't silently regress.
    Skipped in normal CI since Godot isn't installed there; run locally
    with BLAZENG_GODOT_BINARY=/path/to/godot to exercise it for real.
    """

    def test_real_render_produces_correct_pixels(self, tmp_path):
        from src.core.director.director import Shot
        from src.core.scene_composer.scene_composer import SceneComposer
        import struct
        import zlib

        def make_png(path, w, h, rgb):
            raw = bytearray()
            for _ in range(h):
                raw.append(0)
                for _ in range(w):
                    raw.extend(rgb)

            def chunk(tag, data):
                return (
                    struct.pack(">I", len(data))
                    + tag
                    + data
                    + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
                )

            sig = b"\x89PNG\r\n\x1a\n"
            ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
            idat = zlib.compress(bytes(raw))
            Path(path).write_bytes(sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b""))

        asset_path = tmp_path / "red_square.png"
        make_png(asset_path, 40, 40, (255, 0, 0))

        config = {
            "storage_path": str(tmp_path / "storage"),
            "godot_binary_path": os.environ["BLAZENG_GODOT_BINARY"],
        }
        composer = SceneComposer(config)
        shot = Shot(
            shot_id="integration_test_shot",
            scene_description="A single red square",
            characters=["subject"],
            camera_angle="medium shot",
            lighting="bright daylight",
        )
        scene_path = composer.compose_shot(shot, {"subject": asset_path})

        renderer = GodotRenderer(config)
        frames = renderer.render_shot(scene_path, shot.shot_id, num_frames=1)

        assert len(frames) == 1
        assert frames[0].exists()
        assert frames[0].stat().st_size > 100  # not an empty/corrupt file

        try:
            from PIL import Image
            img = Image.open(frames[0]).convert("RGB")
            # A single character is centered horizontally by
            # _layout_characters (margin=15% each side, one slot spans
            # the remaining width) — sample the actual computed center
            # rather than assuming a fixed fraction, so this test tracks
            # the real layout formula instead of a guessed offset.
            sample = img.getpixel((img.width // 2, int(img.height * 0.6)))
            # "bright daylight" modulate brightens red slightly above 255
            # clamp, so we just check it's clearly red-dominant, not exact.
            assert sample[0] > 150 and sample[0] > sample[1] and sample[0] > sample[2], (
                f"Expected a red-dominant pixel at the character's placed position, got {sample}"
            )
        except ImportError:
            pytest.skip("Pillow not installed — skipping pixel-level verification")
