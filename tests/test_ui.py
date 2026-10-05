"""
UI tests (offscreen Qt). They drive the real widgets, the real worker thread and
the real MainWindow; only the renderer is faked so no Godot/Blender is needed
(ffmpeg is, for the end-to-end run).
"""

import os
import shutil
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6")

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from src.ui.asset_browser import AssetBrowser
from src.ui.model_setup_dialog import ModelSetupDialog
from src.ui.shot_viewer import ShotViewer
from src.ui.timeline_widget import TimelineWidget

HAVE_FFMPEG = shutil.which("ffmpeg") is not None


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def wait_for(cond, timeout=30.0):
    end = time.time() + timeout
    while time.time() < end:
        QApplication.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def _png(path: Path, size=(64, 36), color=(200, 60, 60)) -> Path:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)
    return path


class FakeRenderer:
    def __init__(self, config):
        self.dir = Path(config["storage_path"]) / "renders"

    def render_shot(self, scene_path, shot_id, duration_seconds=4.0, num_frames=None):
        return [_png(self.dir / shot_id / f"frame{i:04d}.png", color=(i * 30, 90, 140)) for i in range(1, 7)]


@pytest.fixture
def config(tmp_path):
    return {
        "storage_path": str(tmp_path / "storage"), "render_fps": 6,
        "text_provider": "dummy", "vision_provider": "dummy", "image_provider": "dummy",
    }


# ---------------------------------------------------------------- timeline

class TestTimeline:
    def _tl(self, qapp):
        tl = TimelineWidget()
        tl.resize(600, 80)
        tl.load_shots([{"shot_id": "a", "duration": 2}, {"shot_id": "b", "duration": 4}, {"shot_id": "c", "duration": 2}])
        return tl

    def test_loads_pending(self, qapp):
        tl = self._tl(qapp)
        assert tl.shot_ids() == ["a", "b", "c"] and tl.status_of("b") == "pending"

    def test_widths_proportional_to_duration(self, qapp):
        r = self._tl(qapp)._rects()
        assert r["b"].width() == pytest.approx(2 * r["a"].width(), rel=0.05)

    def test_click_selects_and_emits(self, qapp):
        tl = self._tl(qapp)
        got = []
        tl.shot_selected.connect(got.append)
        centre = tl._rects()["c"].center()
        QTest.mouseClick(tl, Qt.MouseButton.LeftButton, pos=centre.toPoint())
        assert tl.selected() == "c" and got == ["c"]

    def test_keyboard_navigation_clamps(self, qapp):
        tl = self._tl(qapp)
        tl.select("a")
        QTest.keyClick(tl, Qt.Key.Key_Right)
        QTest.keyClick(tl, Qt.Key.Key_Right)
        QTest.keyClick(tl, Qt.Key.Key_Right)  # past the end
        assert tl.selected() == "c"
        for _ in range(5):
            QTest.keyClick(tl, Qt.Key.Key_Left)
        assert tl.selected() == "a"

    def test_status_update_and_unknown_shot_ignored(self, qapp):
        tl = self._tl(qapp)
        tl.set_status("a", "validated")
        tl.set_status("nope", "failed")
        assert tl.status_of("a") == "validated" and tl.status_of("nope") == "pending"

    def test_select_unknown_is_noop(self, qapp):
        tl = self._tl(qapp)
        tl.select("zzz")
        assert tl.selected() is None

    def test_reload_clears_stale_selection(self, qapp):
        tl = self._tl(qapp)
        tl.select("b")
        tl.load_shots([{"shot_id": "x", "duration": 1}])
        assert tl.selected() is None

    def test_paints_without_error_empty_and_full(self, qapp):
        tl = TimelineWidget(); tl.resize(400, 80)
        assert not tl.grab().isNull()
        tl = self._tl(qapp); tl.select("a"); tl.set_status("a", "failed")
        assert not tl.grab().isNull()

    def test_many_shots_stay_clickable(self, qapp):
        tl = TimelineWidget(); tl.resize(500, 80)
        tl.load_shots([{"shot_id": f"s{i}", "duration": 4} for i in range(100)])
        assert tl.minimumSizeHint().width() >= 100 * 72   # scrolls rather than crushing shots


# ------------------------------------------------------------- shot viewer

class TestShotViewer:
    def test_frames_and_scrubber(self, qapp, tmp_path):
        v = ShotViewer(); v.resize(600, 500)
        frames = [_png(tmp_path / f"f{i}.png") for i in range(5)]
        v.show_shot({"shot_id": "s1", "characters": ["Ann", "Bob"], "camera_angle": "wide"}, "rendered", frames)
        assert v.scrubber.maximum() == 4 and v.scrubber.isEnabled()
        assert v.preview.has_image() and "1 / 5" in v.frame_label.text()
        v.scrubber.setValue(3)
        assert "4 / 5" in v.frame_label.text()
        assert v.characters.text() == "Ann, Bob"

    def test_no_frames_shows_placeholder(self, qapp):
        v = ShotViewer()
        v.show_shot({"shot_id": "s1"}, "pending")
        assert not v.preview.has_image() and not v.scrubber.isEnabled()

    def test_missing_frame_file_does_not_crash(self, qapp, tmp_path):
        v = ShotViewer()
        v.show_shot({"shot_id": "s1"}, "rendered", [tmp_path / "gone.png"])
        assert not v.preview.has_image()

    def test_error_detail_only_shown_for_failures(self, qapp):
        v = ShotViewer()
        v.show_shot({"shot_id": "s"}, "failed", detail="boom")
        assert v.detail.text() == "boom"
        v.show_shot({"shot_id": "s"}, "rendered", detail="boom")
        assert v.detail.text() == ""


# ----------------------------------------------------------- asset browser

class TestAssetBrowser:
    def _make(self, tmp_path):
        root = tmp_path / "storage" / "assets" / "character"
        for name, versions in {"ann": 2, "bob": 1}.items():
            for v in range(1, versions + 1):
                _png(root / name / f"v{v}" / f"{name}_v{v}.png", (32, 64))
        return tmp_path / "storage"

    def test_tree_structure_and_ordering(self, qapp, tmp_path):
        b = AssetBrowser(self._make(tmp_path))
        top = b.topLevelItem(0)
        assert top.text(0) == "Character" and top.childCount() == 2
        ann = top.child(0)
        assert ann.text(0) == "ann" and [ann.child(i).text(0) for i in range(2)] == ["v1", "v2"]
        assert not ann.child(0).icon(0).isNull()          # thumbnail decoded

    def test_numeric_not_lexicographic_version_order(self, qapp, tmp_path):
        storage = self._make(tmp_path)
        for v in (3, 10):
            _png(storage / "assets" / "character" / "ann" / f"v{v}" / "x.png")
        b = AssetBrowser(storage)
        ann = b.topLevelItem(0).child(0)
        assert [ann.child(i).text(0) for i in range(ann.childCount())] == ["v1", "v2", "v3", "v10"]

    def test_delete_version(self, qapp, tmp_path):
        storage = self._make(tmp_path)
        b = AssetBrowser(storage)
        got = []
        b.version_deleted.connect(lambda t, n, v: got.append((t, n, v)))
        assert b.delete_version(b.topLevelItem(0).child(0).child(1), confirm=False)
        assert not (storage / "assets" / "character" / "ann" / "v2").exists()
        assert got == [("Character", "ann", 2)]
        assert b.topLevelItem(0).child(0).childCount() == 1

    def test_non_version_item_not_deletable(self, qapp, tmp_path):
        b = AssetBrowser(self._make(tmp_path))
        assert b.delete_version(b.topLevelItem(0), confirm=False) is False

    def test_missing_directory_is_empty_not_error(self, qapp, tmp_path):
        assert AssetBrowser(tmp_path / "nothing").topLevelItemCount() == 0


# ---------------------------------------------------------------- dialog

class TestDialog:
    def test_dummy_config_is_valid_and_roundtrips(self, qapp, config):
        d = ModelSetupDialog(config)
        assert d.offline.isChecked()
        assert d.buttons.button(d.buttons.StandardButton.Ok).isEnabled()
        out = d.get_updated_config()
        assert out["text_provider"] == out["image_provider"] == "dummy"

    def test_missing_key_blocks_ok_until_entered(self, qapp):
        d = ModelSetupDialog({"text_provider": "gemini", "vision_provider": "gemini",
                              "image_provider": "gemini", "gemini_api_key": "GEMINI_API_KEY_HERE"})
        ok = d.buttons.button(d.buttons.StandardButton.Ok)
        assert not ok.isEnabled() and "gemini_api_key" in d.message.text()
        d.keys["gemini_api_key"].setText("abc123")
        assert ok.isEnabled()
        assert d.get_updated_config()["gemini_api_key"] == "abc123"

    def test_offline_toggle_sets_all_dummy(self, qapp):
        d = ModelSetupDialog({"text_provider": "gemini", "gemini_api_key": "k"})
        d.offline.setChecked(True)
        assert d.text_combo.currentText() == d.vision_combo.currentText() == d.image_combo.currentText() == "dummy"

    def test_engine_disabled_for_godot(self, qapp, config):
        d = ModelSetupDialog(config)
        d.renderer_combo.setCurrentText("godot")
        assert not d.engine_combo.isEnabled()
        d.renderer_combo.setCurrentText("blender")
        assert d.engine_combo.isEnabled()

    def test_speech_voices_and_characters_roundtrip(self, qapp, config):
        d = ModelSetupDialog({**config, "speech_provider": "piper", "blender_characters": "rigged",
                              "voices": {"Ann": "amy", "Bob": "ryan:2"}})
        assert d.speech_combo.currentText() == "piper" and d.characters_combo.currentText() == "rigged"
        assert d.voices_edit.text() == "Ann=amy, Bob=ryan:2"
        d.voices_edit.setText("Ann=en-us+f3, Zed = x")
        out = d.get_updated_config()
        assert out["speech_provider"] == "piper" and out["blender_characters"] == "rigged"
        assert out["voices"] == {"Ann": "en-us+f3", "Zed": "x"}

    def test_defaults_when_config_has_no_speech_settings(self, qapp, config):
        out = ModelSetupDialog(config).get_updated_config()
        assert out["speech_provider"] == "auto" and out["blender_characters"] == "planes" and out["voices"] == {}

    def test_malformed_voices_block_ok_and_keep_old_value(self, qapp, config):
        d = ModelSetupDialog({**config, "voices": {"Ann": "amy"}})
        ok = d.buttons.button(d.buttons.StandardButton.Ok)
        d.voices_edit.setText("Ann amy")
        assert not ok.isEnabled() and "Name=voice" in d.message.text()
        assert d.get_updated_config()["voices"] == {"Ann": "amy"}
        d.voices_edit.setText("Ann=amy")
        assert ok.isEnabled()

    def test_characters_choice_disabled_for_godot(self, qapp, config):
        d = ModelSetupDialog(config)
        d.renderer_combo.setCurrentText("godot")
        assert not d.characters_combo.isEnabled()

    def test_openai_speech_needs_a_key(self, qapp, config):
        d = ModelSetupDialog({**config, "speech_provider": "openai"})
        assert not d.buttons.button(d.buttons.StandardButton.Ok).isEnabled()
        d.keys["openai_api_key"].setText("sk-1")
        assert d.buttons.button(d.buttons.StandardButton.Ok).isEnabled()

    def test_does_not_mutate_input_config(self, qapp, config):
        before = dict(config)
        d = ModelSetupDialog(config); d.renderer_combo.setCurrentText("blender"); d.get_updated_config()
        assert config == before


# ---------------------------------------------------------------- cancel

class TestOrchestratorCancel:
    def test_cancel_stops_a_run_in_progress(self, tmp_path):
        from src.pipeline import build_pipeline, ensure_storage
        import src.integrations.renderer_factory as rf

        cfg = {"storage_path": str(tmp_path / "s"), "text_provider": "dummy",
               "vision_provider": "dummy", "image_provider": "dummy"}
        ensure_storage(cfg)
        orig = rf.get_renderer
        rf.get_renderer = FakeRenderer
        try:
            director, orch, *_ = build_pipeline({**cfg, "render_fps": 6})
        finally:
            rf.get_renderer = orig
        orch.load_task_schedule(director.create_task_schedule(director.generate_production_plan("story")))
        seen = []

        def hook(shot_id, status, detail):
            seen.append((shot_id, status))
            if status == "composed":
                orch.cancel()          # cancel from inside the run, as the GUI thread would

        orch.on_shot_update = hook
        assert orch.run_pipeline() is False
        assert orch.cancelled and len(seen) < 6   # stopped early, not all 3 shots composed+rendered


# --------------------------------------------------- worker + main window

@pytest.mark.skipif(not HAVE_FFMPEG, reason="needs ffmpeg")
class TestEndToEnd:
    @pytest.fixture(autouse=True)
    def _fake(self, monkeypatch):
        monkeypatch.setattr("src.integrations.renderer_factory.get_renderer", FakeRenderer)

    def test_main_window_full_run(self, qapp, config, tmp_path):
        from src.ui.main_window import MainWindow

        w = MainWindow(config)
        w.show()
        w.prompt.setPlainText("A detective finds a clue in a warehouse.")
        results = []
        w.run_finished.connect(results.append)
        w.generate_btn.click()
        assert w.is_running() and not w.generate_btn.isEnabled() and w.cancel_btn.isEnabled()
        assert wait_for(lambda: bool(results), 90), "run never finished"
        assert wait_for(lambda: not w.is_running(), 10)

        r = results[0]
        assert r["video"] and Path(r["video"]).exists() and r["failures"] == []
        assert w.timeline.shot_ids() == ["shot_001", "shot_002", "shot_003"]
        assert all(w.timeline.status_of(s) in ("rendered", "validated", "rendered_unvalidated")
                   for s in w.timeline.shot_ids())
        assert w.generate_btn.isEnabled() and not w.cancel_btn.isEnabled()
        assert w.open_video_btn.isEnabled()
        # selecting a finished shot shows its frames
        w.timeline.select("shot_002")
        assert w.viewer.preview.has_image() and w.viewer.scrubber.maximum() == 5
        # world state viewer picked up the characters the run created
        assert w.world.topLevelItemCount() >= 1
        w.close()

    def test_empty_prompt_does_not_start(self, qapp, config):
        from src.ui.main_window import MainWindow

        w = MainWindow(config)
        w.generate_btn.click()
        assert not w.is_running() and "prompt" in w.statusBar().currentMessage().lower()

    def test_pipeline_failure_reaches_gui_without_crash(self, qapp, config, monkeypatch):
        from src.ui import main_window as mw

        monkeypatch.setattr(mw.QMessageBox, "critical", staticmethod(lambda *a, **k: None))
        config["text_provider"] = "nonexistent-provider"
        w = mw.MainWindow(config)
        w.prompt.setPlainText("story")
        results = []
        w.run_finished.connect(results.append)
        w.generate_btn.click()
        assert wait_for(lambda: bool(results), 30)
        assert results[0]["video"] is None
        assert wait_for(lambda: w.generate_btn.isEnabled(), 5), "Generate must re-enable after a failure"
        assert "Error" in w.statusBar().currentMessage()
        w.close()

    def test_cancel_button_stops_run(self, qapp, config, monkeypatch):
        from src.ui.main_window import MainWindow

        class SlowRenderer(FakeRenderer):
            def render_shot(self, *a, **k):
                time.sleep(0.6)
                return super().render_shot(*a, **k)

        monkeypatch.setattr("src.integrations.renderer_factory.get_renderer", SlowRenderer)
        w = MainWindow(config)
        w.prompt.setPlainText("story")
        results = []
        w.run_finished.connect(results.append)
        w.generate_btn.click()
        assert wait_for(lambda: w.timeline.status_of("shot_001") != "pending" if w.timeline.shot_ids() else False, 60)
        w.cancel_btn.click()
        assert wait_for(lambda: bool(results), 60)
        assert results[0]["cancelled"] is True and results[0]["video"] is None
        assert wait_for(lambda: w.generate_btn.isEnabled(), 10)
        w.close()

    def test_ui_stays_responsive_while_worker_runs(self, qapp, config, monkeypatch):
        """The GUI thread must keep servicing events during a slow render."""
        from src.ui.main_window import MainWindow

        class SlowRenderer(FakeRenderer):
            def render_shot(self, *a, **k):
                time.sleep(0.8)
                return super().render_shot(*a, **k)

        monkeypatch.setattr("src.integrations.renderer_factory.get_renderer", SlowRenderer)
        w = MainWindow(config)
        w.prompt.setPlainText("story")
        results = []
        w.run_finished.connect(results.append)
        w.generate_btn.click()
        ticks, last, worst = 0, time.perf_counter(), 0.0
        end = time.time() + 60
        while not results and time.time() < end:
            QApplication.processEvents()
            now = time.perf_counter()
            worst = max(worst, now - last)
            last = now
            ticks += 1
            time.sleep(0.005)
        assert results, "run never finished"
        assert ticks > 100, "event loop barely ran — GUI thread was blocked"
        assert worst < 0.5, f"GUI thread stalled for {worst:.2f}s"
        wait_for(lambda: not w.is_running(), 10)
        w.close()
