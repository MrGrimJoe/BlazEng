"""ShotViewer — frame preview with a scrubber, plus the selected shot's details."""

from pathlib import Path
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QFormLayout, QLabel, QSizePolicy, QSlider, QVBoxLayout, QWidget,
)

from .theme import STATUS_LABELS


class _PreviewLabel(QLabel):
    """Keeps the source pixmap and rescales it on resize, preserving aspect ratio."""

    def __init__(self):
        super().__init__("No frame yet")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(320, 180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet("background:#14161a; border:1px solid #343a44; border-radius:4px; color:#7d8590;")
        self._source: Optional[QPixmap] = None

    def set_source(self, pixmap: Optional[QPixmap], placeholder: str = "No frame yet") -> None:
        self._source = pixmap
        if pixmap is None or pixmap.isNull():
            self._source = None
            self.setPixmap(QPixmap())
            self.setText(placeholder)
        else:
            self._rescale()

    def has_image(self) -> bool:
        return self._source is not None

    def _rescale(self) -> None:
        if self._source is not None:
            self.setPixmap(self._source.scaled(
                self.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._rescale()


class ShotViewer(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._frames: List[Path] = []

        self.preview = _PreviewLabel()
        self.scrubber = QSlider(Qt.Orientation.Horizontal)
        self.scrubber.setEnabled(False)
        self.scrubber.valueChanged.connect(self._show_frame)
        self.frame_label = QLabel("")
        self.frame_label.setAlignment(Qt.AlignmentFlag.AlignRight)

        self.title = QLabel("Select a shot on the timeline")
        self.title.setStyleSheet("font-size:15px; font-weight:600;")
        self.status = QLabel("")
        self.description = QLabel(""); self.description.setWordWrap(True)
        self.camera = QLabel(""); self.lighting = QLabel("")
        self.characters = QLabel(""); self.characters.setWordWrap(True)
        self.action = QLabel(""); self.action.setWordWrap(True)
        self.detail = QLabel(""); self.detail.setWordWrap(True)
        self.detail.setStyleSheet("color:#e0848a;")
        for lbl in (self.description, self.action, self.characters, self.detail):
            lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        for label, widget in [("Status", self.status), ("Scene", self.description), ("Action", self.action),
                              ("Camera", self.camera), ("Lighting", self.lighting), ("Characters", self.characters)]:
            form.addRow(label + ":", widget)

        layout = QVBoxLayout(self)
        layout.addWidget(self.title)
        layout.addWidget(self.preview, 1)
        row = QVBoxLayout()
        row.addWidget(self.scrubber)
        row.addWidget(self.frame_label)
        layout.addLayout(row)
        layout.addLayout(form)
        layout.addWidget(self.detail)

    def show_shot(self, info: Dict[str, Any], status: str, frames: Optional[List[Path]] = None, detail: str = "") -> None:
        self.title.setText(info.get("shot_id", "Shot"))
        self.status.setText(STATUS_LABELS.get(status, status))
        self.description.setText(info.get("scene_description", "") or "—")
        self.action.setText(info.get("action", "") or "—")
        self.camera.setText(info.get("camera_angle", "") or "—")
        self.lighting.setText(info.get("lighting", "") or "—")
        self.characters.setText(", ".join(info.get("characters", [])) or "—")
        self.detail.setText(detail if status in ("failed", "validation_failed") else "")
        self.set_frames(frames or [])

    def set_frames(self, frames: List[Path]) -> None:
        self._frames = [Path(f) for f in frames]
        self.scrubber.blockSignals(True)
        self.scrubber.setRange(0, max(0, len(self._frames) - 1))
        self.scrubber.setValue(0)
        self.scrubber.setEnabled(len(self._frames) > 1)
        self.scrubber.blockSignals(False)
        self._show_frame(0)

    def _show_frame(self, index: int) -> None:
        if not self._frames:
            self.preview.set_source(None)
            self.frame_label.setText("")
            return
        index = max(0, min(index, len(self._frames) - 1))
        # One decode per scrub step, only for the frame actually shown.
        self.preview.set_source(QPixmap(str(self._frames[index])), "Frame missing on disk")
        self.frame_label.setText(f"Frame {index + 1} / {len(self._frames)}")
