"""
MainWindow — two-panel production studio.

Left (35%): prompt, Generate/Cancel, and tabs for the Asset browser and the
World-state viewer. Right (65%): shot timeline, shot viewer (frame preview +
details), and output actions.

All pipeline work runs in PipelineWorker on a QThread; this class only reacts
to its signals, so the window stays responsive during long renders.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import QThread, QTimer, QUrl, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QDesktopServices, QKeySequence
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QScrollArea, QSplitter, QStatusBar, QTabWidget, QVBoxLayout, QWidget,
)

from .asset_browser import AssetBrowser
from .shot_viewer import ShotViewer
from .timeline_widget import TimelineWidget
from .world_state_viewer import WorldStateViewer
from .worker import PipelineWorker

logger = logging.getLogger(__name__)

_REFRESH_DEBOUNCE_MS = 400


class MainWindow(QMainWindow):
    model_change_requested = pyqtSignal()
    run_finished = pyqtSignal(dict)   # emitted after every run (useful for tests/automation)

    def __init__(self, config: Dict[str, Any]):
        super().__init__()
        self.config = config
        self.setWindowTitle("BlazEng — AI Production Studio")
        self.resize(1360, 840)

        self._thread: Optional[QThread] = None
        self._worker: Optional[PipelineWorker] = None
        self._shots: Dict[str, Dict[str, Any]] = {}
        self._frames: Dict[str, List[str]] = {}
        self._details: Dict[str, str] = {}
        self._result: Dict[str, Any] = {}

        storage = Path(config.get("storage_path", "./storage"))

        # -- left panel ------------------------------------------------------
        self.prompt = QPlainTextEdit()
        self.prompt.setPlaceholderText("Describe your story…  e.g. “A detective in 1940s rain discovers a clue at an abandoned warehouse.”")
        self.prompt.setMinimumHeight(96)
        self.generate_btn = QPushButton("Generate video")
        self.generate_btn.setObjectName("primary")
        self.generate_btn.setShortcut(QKeySequence("Ctrl+Return"))
        self.generate_btn.clicked.connect(self.start_run)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel_run)
        self.settings_btn = QPushButton("Settings…")
        self.settings_btn.clicked.connect(self.model_change_requested.emit)

        buttons = QHBoxLayout()
        buttons.addWidget(self.generate_btn, 1)
        buttons.addWidget(self.cancel_btn)
        buttons.addWidget(self.settings_btn)

        self.assets = AssetBrowser(storage)
        self.world = WorldStateViewer(config)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.assets, "Assets")
        self.tabs.addTab(self.world, "World state")

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.addWidget(QLabel("Story prompt"))
        lv.addWidget(self.prompt)
        lv.addLayout(buttons)
        lv.addWidget(self.tabs, 1)

        # -- right panel -----------------------------------------------------
        self.timeline = TimelineWidget()
        self.timeline.shot_selected.connect(self._on_shot_selected)
        scroll = QScrollArea()
        scroll.setWidget(self.timeline)
        scroll.setWidgetResizable(True)
        scroll.setFixedHeight(self.timeline.minimumHeight() + 22)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.viewer = ShotViewer()
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.stage_label = QLabel("")
        self.open_video_btn = QPushButton("Open video")
        self.open_folder_btn = QPushButton("Open output folder")
        self.open_video_btn.setEnabled(False)
        self.open_folder_btn.setEnabled(False)
        self.open_video_btn.clicked.connect(lambda: self._open_path(self._result.get("video")))
        self.open_folder_btn.clicked.connect(lambda: self._open_path(Path(self._result["video"]).parent
                                                                      if self._result.get("video") else None))

        bottom = QHBoxLayout()
        bottom.addWidget(self.stage_label, 1)
        bottom.addWidget(self.open_video_btn)
        bottom.addWidget(self.open_folder_btn)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.addWidget(QLabel("Timeline"))
        rv.addWidget(scroll)
        rv.addWidget(self.viewer, 1)
        rv.addWidget(self.progress)
        rv.addLayout(bottom)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 35)
        splitter.setStretchFactor(1, 65)
        splitter.setSizes([440, 920])
        self.setCentralWidget(splitter)

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Ready")

        menu = self.menuBar().addMenu("&File")
        act = QAction("Settings…", self)
        act.triggered.connect(self.model_change_requested.emit)
        menu.addAction(act)
        quit_act = QAction("Quit", self)
        quit_act.setShortcut(QKeySequence.StandardKey.Quit)
        quit_act.triggered.connect(self.close)
        menu.addAction(quit_act)

        # Refreshing the asset/world views on every shot event would thrash
        # the disk and DB; coalesce bursts into one refresh.
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(_REFRESH_DEBOUNCE_MS)
        self._refresh_timer.timeout.connect(self._refresh_side_panels)
        self.world.refresh()

    # -- running ----------------------------------------------------------

    def is_running(self) -> bool:
        """True from Generate until the run is fully cleaned up and controls are re-enabled.

        Deliberately tied to the cleanup in _on_thread_done rather than to
        QThread.isRunning(): the thread stops slightly *before* that queued
        slot runs, and reporting "not running" in that gap left the UI in an
        inconsistent state (idle, but Generate still disabled).
        """
        return self._thread is not None

    def start_run(self) -> None:
        prompt = self.prompt.toPlainText().strip()
        if self.is_running():
            return
        if not prompt:
            self.statusBar().showMessage("Enter a story prompt first")
            self.prompt.setFocus()
            return

        self._shots, self._frames, self._details, self._result = {}, {}, {}, {}
        self.timeline.load_shots([])
        self.viewer.show_shot({"shot_id": "Working…"}, "pending")
        self.progress.setRange(0, 0)           # busy indicator until the plan arrives
        self.open_video_btn.setEnabled(False)
        self.open_folder_btn.setEnabled(False)
        self._set_running(True)

        self._thread = QThread(self)
        self._worker = PipelineWorker(self.config, prompt)
        w = self._worker
        w.moveToThread(self._thread)
        self._thread.started.connect(w.run)
        w.stage.connect(self._on_stage)
        w.plan_ready.connect(self._on_plan)
        w.shot_update.connect(self._on_shot_update)
        w.progress.connect(self._on_progress)
        w.finished.connect(self._on_finished)
        w.failed.connect(self._on_failed)
        for sig in (w.finished, w.failed):
            sig.connect(self._thread.quit)
        self._thread.finished.connect(self._on_thread_done)
        self._thread.start()

    def cancel_run(self) -> None:
        if self._worker is not None and self._thread is not None and self._thread.isRunning():
            self.cancel_btn.setEnabled(False)
            self.statusBar().showMessage("Cancelling after the current step…")
            self._worker.cancel()

    def _set_running(self, running: bool) -> None:
        self.generate_btn.setEnabled(not running)
        self.cancel_btn.setEnabled(running)
        self.settings_btn.setEnabled(not running)
        self.prompt.setReadOnly(running)

    # -- worker signals -----------------------------------------------------

    def _on_stage(self, text: str) -> None:
        self.stage_label.setText(text)
        self.statusBar().showMessage(text)

    def _on_plan(self, shots: List[dict]) -> None:
        self._shots = {s["shot_id"]: s for s in shots}
        self.timeline.load_shots(shots)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        if shots:
            self.timeline.select(shots[0]["shot_id"])

    def _on_progress(self, done: int, total: int) -> None:
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(done)

    def _on_shot_update(self, shot_id: str, status: str, detail: str) -> None:
        self.timeline.set_status(shot_id, status)
        self._details[shot_id] = detail
        if shot_id == self.timeline.selected():
            self._show_selected()
        self._refresh_timer.start()

    def _on_finished(self, result: dict) -> None:
        self._result = result
        self._frames = result.get("frames", {})
        for shot_id, frames in self._frames.items():
            if frames:
                self.timeline.set_status(shot_id, "rendered" if self.timeline.status_of(shot_id) == "pending"
                                         else self.timeline.status_of(shot_id))
        n_fail = len(result.get("failures", []))
        if result.get("cancelled"):
            msg = "Cancelled"
        elif result.get("video"):
            msg = "Done — video ready" + (f" ({n_fail} shot(s) failed)" if n_fail else "")
        else:
            msg = f"Finished with no video ({n_fail} failure(s))"
        self.stage_label.setText(msg)
        self.statusBar().showMessage(msg)
        self.open_video_btn.setEnabled(bool(result.get("video")))
        self.open_folder_btn.setEnabled(bool(result.get("video")))
        self._show_selected()
        self._refresh_side_panels()
        self.run_finished.emit(result)

    def _on_failed(self, message: str) -> None:
        self.stage_label.setText("Failed")
        self.statusBar().showMessage(f"Error: {message}")
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        QMessageBox.critical(self, "Generation failed", message)
        self._result = {"video": None, "failures": [{"shot": "-", "stage": "pipeline", "error": message}]}
        self.run_finished.emit(self._result)

    def _on_thread_done(self) -> None:
        self._set_running(False)
        if self._thread is not None:
            self._thread.deleteLater()
        if self._worker is not None:
            self._worker.deleteLater()
        self._thread = self._worker = None

    # -- selection ----------------------------------------------------------

    def _on_shot_selected(self, _shot_id: str) -> None:
        self._show_selected()

    def _show_selected(self) -> None:
        sid = self.timeline.selected()
        if sid is None:
            return
        self.viewer.show_shot(
            self._shots.get(sid, {"shot_id": sid}), self.timeline.status_of(sid),
            frames=[Path(f) for f in self._frames.get(sid, [])], detail=self._details.get(sid, ""))

    def _refresh_side_panels(self) -> None:
        self.assets.refresh()
        self.world.refresh()

    # -- misc ---------------------------------------------------------------

    def set_config(self, config: Dict[str, Any]) -> None:
        self.config = config
        self.statusBar().showMessage("Settings updated")

    @staticmethod
    def _open_path(path) -> None:
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).resolve())))

    def closeEvent(self, event) -> None:
        if self._thread is not None and self._thread.isRunning():
            answer = QMessageBox.question(
                self, "Generation in progress", "Cancel the current run and quit?")
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self._worker.cancel()
            self._thread.quit()
            self._thread.wait(15000)
        self.world.close_connection()
        event.accept()
