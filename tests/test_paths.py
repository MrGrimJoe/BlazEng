"""Installed-app (frozen) path handling, logging setup, and import-time side effects."""

import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from src import logging_setup, paths, pipeline


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv("BLAZENG_HOME", raising=False)
    monkeypatch.setattr(sys, "frozen", False, raising=False)


@pytest.fixture
def home(tmp_path, monkeypatch, clean_env):
    """Behave like an installed app, with the user-data dir inside tmp_path."""
    monkeypatch.setenv("BLAZENG_HOME", str(tmp_path / "userdata"))
    return tmp_path / "userdata"


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    """A fake PyInstaller bundle dir (read-only resources) and frozen=True."""
    root = tmp_path / "bundle"
    (root / "bin").mkdir(parents=True)
    (root / "godot").mkdir()
    (root / "config.yaml").write_text(yaml.safe_dump(
        {"storage_path": "./storage", "ffmpeg_path": "ffmpeg", "godot_binary_path": "./bin/godot",
         "text_provider": "dummy", "vision_provider": "dummy", "image_provider": "dummy"}))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(root), raising=False)
    return root


class TestUserDataDir:
    def test_override_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv("BLAZENG_HOME", str(tmp_path / "x"))
        assert paths.user_data_dir() == tmp_path / "x"

    def test_windows(self, monkeypatch, clean_env):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("LOCALAPPDATA", "C:/Users/me/AppData/Local")
        assert paths.user_data_dir() == Path("C:/Users/me/AppData/Local") / "BlazEng"

    def test_macos(self, monkeypatch, clean_env):
        monkeypatch.setattr(sys, "platform", "darwin")
        assert paths.user_data_dir() == Path.home() / "Library" / "Application Support" / "BlazEng"

    def test_linux_xdg(self, monkeypatch, clean_env, tmp_path):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        assert paths.user_data_dir() == tmp_path / "blazeng"


class TestConfigResolution:
    def test_source_mode_is_unchanged(self, clean_env):
        assert not paths.uses_user_data_dir()
        assert paths.resolve_config_path("config.yaml") == Path("config.yaml")

    def test_installed_default_goes_to_user_dir(self, home):
        assert paths.resolve_config_path("config.yaml") == home / "config.yaml"

    def test_explicit_path_is_respected_even_when_installed(self, home, tmp_path):
        assert paths.resolve_config_path(str(tmp_path / "mine.yaml")) == tmp_path / "mine.yaml"


class TestFirstRun:
    def test_seeds_user_config_from_bundled_template(self, home, bundle):
        cfg = pipeline.load_config()
        assert (home / "config.yaml").exists()
        assert cfg["text_provider"] == "dummy"

    def test_does_not_overwrite_existing_user_config(self, home, bundle):
        home.mkdir(parents=True)
        (home / "config.yaml").write_text(yaml.safe_dump({"text_provider": "gemini"}))
        assert pipeline.load_config()["text_provider"] == "gemini"

    def test_storage_goes_to_user_dir_not_install_dir(self, home, bundle):
        assert pipeline.load_config()["storage_path"] == str(home / "storage")

    def test_missing_template_still_works(self, home, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "empty"), raising=False)
        cfg = pipeline.load_config()
        assert cfg["storage_path"] == str(home / "storage")

    def test_save_config_writes_user_file(self, home, bundle):
        pipeline.save_config({"text_provider": "openai"})
        assert yaml.safe_load((home / "config.yaml").read_text())["text_provider"] == "openai"

    def test_ensure_storage_creates_folders_under_user_dir(self, home, bundle):
        cfg = pipeline.load_config()
        base = pipeline.ensure_storage(cfg)
        assert base == home / "storage" and (base / "database").is_dir()


class TestBundledTools:
    def test_uses_bundled_ffmpeg_and_godot(self, home, bundle, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        (bundle / "bin" / "ffmpeg.exe").write_text("")
        (bundle / "godot" / "Godot_v4.7.2-stable_win64.exe").write_text("")
        (bundle / "godot" / "Godot_v4.7.2-stable_win64_console.exe").write_text("")
        cfg = pipeline.load_config()
        assert cfg["ffmpeg_path"] == str(bundle / "bin" / "ffmpeg.exe")
        assert cfg["godot_binary_path"].endswith("win64.exe")          # GUI exe, not *_console.exe
        assert "console" not in cfg["godot_binary_path"]

    def test_users_own_existing_paths_are_respected(self, home, bundle, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        (bundle / "bin" / "ffmpeg.exe").write_text("")
        mine = tmp_path / "myffmpeg"
        mine.write_text("")
        home.mkdir(parents=True)
        (home / "config.yaml").write_text(yaml.safe_dump({"ffmpeg_path": str(mine)}))
        assert pipeline.load_config()["ffmpeg_path"] == str(mine)

    def test_stale_path_from_moved_install_falls_back_to_bundled(self, home, bundle, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        (bundle / "bin" / "ffmpeg.exe").write_text("")
        home.mkdir(parents=True)
        (home / "config.yaml").write_text(yaml.safe_dump({"ffmpeg_path": "C:/old/install/ffmpeg.exe"}))
        assert pipeline.load_config()["ffmpeg_path"] == str(bundle / "bin" / "ffmpeg.exe")

    def test_no_bundle_leaves_config_alone(self, home, clean_env, tmp_path):
        # BLAZENG_HOME set but NOT frozen: storage moves, tools are not touched
        home.mkdir(parents=True)
        (home / "config.yaml").write_text(yaml.safe_dump({"ffmpeg_path": "ffmpeg"}))
        cfg = pipeline.load_config()
        assert cfg["ffmpeg_path"] == "ffmpeg"


class TestLogging:
    @pytest.fixture(autouse=True)
    def _restore(self):
        root, hook = logging.getLogger(), sys.excepthook
        saved = (root.handlers[:], root.level)
        yield
        for h in root.handlers[:]:
            root.removeHandler(h)
            h.close()
        root.handlers[:], root.level = saved[0], saved[1]
        sys.excepthook = hook

    def test_writes_log_file_in_user_dir(self, home):
        log = logging_setup.configure_logging()
        logging.getLogger("t").warning("hello from test")
        for h in logging.getLogger().handlers:
            h.flush()
        assert log == home / "logs" / "studio.log" and "hello from test" in log.read_text()

    def test_no_console_means_no_stream_handler_and_no_crash(self, home, monkeypatch):
        monkeypatch.setattr(sys, "stdout", None)
        logging_setup.configure_logging()
        logging.getLogger("t").info("still fine")
        assert not any(type(h) is logging.StreamHandler for h in logging.getLogger().handlers)

    def test_unwritable_log_dir_does_not_crash(self, home, monkeypatch, tmp_path):
        # A directory "inside" a regular file can't be created on any OS.
        blocker = tmp_path / "a_file"
        blocker.write_text("x")
        monkeypatch.setattr(logging_setup, "log_directory", lambda: blocker / "logs")
        assert logging_setup.configure_logging() is None

    def test_uncaught_exceptions_are_logged(self, home):
        log = logging_setup.configure_logging()
        try:
            raise ValueError("kaboom")
        except ValueError:
            sys.excepthook(*sys.exc_info())
        for h in logging.getLogger().handlers:
            h.flush()
        assert "kaboom" in log.read_text()


def test_importing_main_has_no_filesystem_side_effects(tmp_path):
    """Regression: main.py used to create ./storage/logs at import time, which crashes
    when launched from a read-only install directory."""
    repo = Path(__file__).resolve().parent.parent
    env = {**os.environ, "PYTHONPATH": str(repo), "QT_QPA_PLATFORM": "offscreen"}
    env.pop("BLAZENG_HOME", None)
    result = subprocess.run([sys.executable, "-c", "import main"], cwd=tmp_path, env=env,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []
