"""CLI tests. The pipeline test swaps in a fake renderer so it needs ffmpeg but not Godot/Blender."""

import json
import shutil
from pathlib import Path

import pytest

from src import cli

HAVE_FFMPEG = shutil.which("ffmpeg") is not None


class FakeRenderer:
    """Writes a few real PNG frames per shot, numbered like Blender (from 1)."""

    def __init__(self, config):
        self.dir = Path(config["storage_path"]) / "renders"

    def render_shot(self, scene_path, shot_id, duration_seconds=4.0, num_frames=None):
        from PIL import Image

        out = self.dir / shot_id
        out.mkdir(parents=True, exist_ok=True)
        frames = []
        for i in range(1, 7):
            p = out / f"frame{i:04d}.png"
            Image.new("RGB", (64, 36), (i * 30, 80, 120)).save(p)
            frames.append(p)
        return frames


@pytest.fixture
def cfg_file(tmp_path):
    import yaml

    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump({"storage_path": str(tmp_path / "storage"), "render_fps": 6}))
    return p


class TestArgs:
    def test_resolution_parse(self):
        assert cli._parse_resolution("1280x720") == (1280, 720)
        assert cli._parse_resolution("640X360") == (640, 360)

    @pytest.mark.parametrize("bad", ["1280", "axb", "0x0", "1280x", "-5x10"])
    def test_resolution_rejects_garbage(self, bad):
        import argparse

        with pytest.raises(argparse.ArgumentTypeError):
            cli._parse_resolution(bad)

    def test_overrides(self):
        args = cli.build_parser().parse_args(
            ["run", "p", "--dummy", "--renderer", "blender", "--resolution", "320x180",
             "--fps", "8", "--engine", "cycles", "--validate", "--storage", "/tmp/s"]
        )
        cfg = cli.apply_overrides({"skip_validation": True}, args)
        assert cfg["text_provider"] == cfg["vision_provider"] == cfg["image_provider"] == "dummy"
        assert cfg["renderer"] == "blender" and cfg["blender_engine"] == "cycles"
        assert (cfg["render_width"], cfg["render_height"], cfg["render_fps"]) == (320, 180, 8)
        assert cfg["skip_validation"] is False and cfg["storage_path"] == "/tmp/s"

    def test_overrides_do_not_mutate_input(self):
        args = cli.build_parser().parse_args(["run", "p", "--dummy"])
        original = {"text_provider": "gemini"}
        cli.apply_overrides(original, args)
        assert original == {"text_provider": "gemini"}


class TestUsageErrors:
    def test_no_prompt(self, cfg_file, capsys):
        assert cli.main(["run", "-c", str(cfg_file)]) == cli.EXIT_USAGE

    def test_blank_prompt(self, cfg_file):
        assert cli.main(["run", "   ", "-c", str(cfg_file)]) == cli.EXIT_USAGE

    def test_missing_prompt_file(self, cfg_file):
        assert cli.main(["run", "--prompt-file", "/nope.txt", "-c", str(cfg_file)]) == cli.EXIT_USAGE

    def test_bad_resolution(self, cfg_file):
        assert cli.main(["run", "x", "--resolution", "wat", "-c", str(cfg_file)]) == cli.EXIT_USAGE

    def test_version(self, capsys):
        assert cli.main(["version"]) == 0
        assert capsys.readouterr().out.startswith("blazeng ")


class TestDoctor:
    def test_reports_and_exit_code(self, cfg_file, capsys):
        code = cli.main(["doctor", "-c", str(cfg_file)])
        out = capsys.readouterr().out
        assert "Python" in out and "ffmpeg" in out
        assert code in (cli.EXIT_OK, cli.EXIT_FAILED)

    def test_flags_missing_renderer_as_blocking(self, tmp_path, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda name: None)
        checks = cli.run_checks({"godot_binary_path": str(tmp_path / "nope")})
        assert any(s == cli.FAIL and n == "renderer" for s, n, _ in checks)
        assert any(s == cli.FAIL and n == "ffmpeg" for s, n, _ in checks)


@pytest.mark.skipif(not HAVE_FFMPEG, reason="needs ffmpeg")
class TestRunPipeline:
    @pytest.fixture(autouse=True)
    def _fake_renderer(self, monkeypatch):
        monkeypatch.setattr("src.integrations.renderer_factory.get_renderer", FakeRenderer)

    def test_full_run_produces_video_and_timeline(self, cfg_file, tmp_path, capsys):
        code = cli.main(["run", "A story", "--dummy", "-c", str(cfg_file), "-o", "out.mp4"])
        assert code == cli.EXIT_OK, capsys.readouterr()
        out = tmp_path / "storage" / "output"
        assert (out / "out.mp4").stat().st_size > 0
        assert (out / "out.otio").exists()

    def test_json_summary(self, cfg_file, capsys):
        code = cli.main(["run", "A story", "--dummy", "--json", "-c", str(cfg_file)])
        data = json.loads(capsys.readouterr().out)
        assert code == 0
        assert data["shots"] == ["shot_001", "shot_002", "shot_003"]
        assert data["failures"] == [] and data["video"].endswith("final.mp4")

    def test_no_timeline_flag(self, cfg_file, tmp_path):
        cli.main(["run", "A story", "--dummy", "--no-timeline", "-c", str(cfg_file)])
        assert not (tmp_path / "storage" / "output" / "final.otio").exists()

    def test_partial_failure_exit_code_and_video_still_built(self, cfg_file, tmp_path, monkeypatch, capsys):
        class Flaky(FakeRenderer):
            def render_shot(self, scene_path, shot_id, **kw):
                if shot_id == "shot_002":
                    raise RuntimeError("renderer exploded")
                return super().render_shot(scene_path, shot_id, **kw)

        monkeypatch.setattr("src.integrations.renderer_factory.get_renderer", Flaky)
        code = cli.main(["run", "A story", "--dummy", "--json", "-c", str(cfg_file)])
        data = json.loads(capsys.readouterr().out)
        assert code == cli.EXIT_PARTIAL
        assert [f["shot"] for f in data["failures"]] == ["shot_002"]
        assert (tmp_path / "storage" / "output" / "final.mp4").exists()

    def test_total_failure_exit_code(self, cfg_file, monkeypatch, capsys):
        class Broken(FakeRenderer):
            def render_shot(self, *a, **kw):
                raise RuntimeError("nope")

        monkeypatch.setattr("src.integrations.renderer_factory.get_renderer", Broken)
        assert cli.main(["run", "A story", "--dummy", "-c", str(cfg_file)]) == cli.EXIT_FAILED
        assert "no shot rendered" in capsys.readouterr().err
