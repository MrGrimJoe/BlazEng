"""Virtual-display handling: required on Linux, never required on Windows/macOS."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.core.director.director import Shot
from src.core.scene_composer.scene_composer import SceneComposer
from src.integrations import display
from src.integrations.blender.renderer import BlenderRenderer, BlenderRenderError
from src.integrations.godot.renderer import GodotRenderer


@pytest.fixture
def not_linux(monkeypatch):
    monkeypatch.setattr(display, "needs_virtual_display", lambda: False)


@pytest.fixture
def linux(monkeypatch):
    monkeypatch.setattr(display, "needs_virtual_display", lambda: True)


class TestHelpers:
    def test_wrap_on_linux(self, linux):
        assert display.wrap_command(["blender", "-b"]) == ["xvfb-run", "-a", "blender", "-b"]

    def test_wrap_elsewhere_is_identity(self, not_linux):
        assert display.wrap_command(["blender", "-b"]) == ["blender", "-b"]

    def test_hint_only_when_linux_and_missing(self, linux, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda n: None)
        assert "xvfb" in display.missing_display_tool()
        monkeypatch.setattr("shutil.which", lambda n: "/usr/bin/xvfb-run")
        assert display.missing_display_tool() is None

    def test_never_a_hint_on_windows_or_mac(self, not_linux, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda n: None)
        assert display.missing_display_tool() is None

    def test_platform_detection(self, monkeypatch):
        monkeypatch.setattr(display.sys, "platform", "linux")
        assert display.needs_virtual_display()
        for plat in ("win32", "darwin"):
            monkeypatch.setattr(display.sys, "platform", plat)
            assert not display.needs_virtual_display()

    def test_no_console_window_flag_only_on_windows(self, monkeypatch):
        monkeypatch.setattr(display.sys, "platform", "linux")
        assert display.subprocess_kwargs() == {}
        monkeypatch.setattr(display.sys, "platform", "win32")
        monkeypatch.setattr(display.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
        assert display.subprocess_kwargs() == {"creationflags": 0x08000000}


@pytest.fixture
def composed(tmp_path):
    from PIL import Image

    cfg = {"storage_path": str(tmp_path / "s"), "render_width": 64, "render_height": 36}
    png = tmp_path / "a.png"
    Image.new("RGBA", (8, 16), (200, 0, 0, 255)).save(png)
    scene = SceneComposer(cfg).compose_shot(
        Shot(shot_id="s1", scene_description="x", characters=["A"]), {"A": png})
    return cfg, scene


class TestBlenderOnWindowsAndMac:
    def test_runs_without_xvfb_and_launches_blender_directly(self, composed, not_linux):
        cfg, scene = composed

        def fake_run(cmd, **kw):
            out = Path(cmd[cmd.index("--") + 2])
            (out / "frame0001.png").write_bytes(b"png")
            return MagicMock(returncode=0, stdout="BLAZENG_RENDER_OK", stderr="")

        with patch("shutil.which", side_effect=lambda n: "C:/Blender/blender.exe" if n == "blender" else None), \
             patch("subprocess.run", side_effect=fake_run) as run:
            frames = BlenderRenderer(cfg).render_shot(scene, "s1", num_frames=1)
        cmd = run.call_args[0][0]
        assert cmd[0] == "C:/Blender/blender.exe" and "xvfb-run" not in cmd
        assert len(frames) == 1

    def test_still_requires_xvfb_on_linux(self, composed, linux):
        cfg, scene = composed
        with patch("shutil.which", side_effect=lambda n: "/usr/bin/blender" if n == "blender" else None):
            with pytest.raises(BlenderRenderError, match="xvfb"):
                BlenderRenderer(cfg).render_shot(scene, "s1", num_frames=1)


class TestGodotOnWindowsAndMac:
    def test_runs_without_xvfb_and_launches_godot_directly(self, composed, not_linux, tmp_path):
        cfg, scene = composed
        godot = tmp_path / "godot.exe"
        godot.write_text("")
        cfg["godot_binary_path"] = str(godot)

        def fake_run(cmd, **kw):
            stub = Path(cmd[cmd.index("--write-movie") + 1])
            (stub.parent / "frame00000000.png").write_bytes(b"png")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("shutil.which", return_value=None), patch("subprocess.run", side_effect=fake_run) as run:
            frames = GodotRenderer(cfg).render_shot(scene, "s1", num_frames=1)
        cmd = run.call_args[0][0]
        assert cmd[0] == str(godot) and "xvfb-run" not in cmd
        assert len(frames) == 1
