"""
TimelineWidget — a horizontal strip with one rectangle per shot.

Rectangle width is proportional to shot duration; colour shows status
(grey pending, amber in progress, green done, red failed). Click or use
Left/Right arrow keys to select a shot. Custom-painted rather than built from
child widgets so a 100-shot production stays instant to repaint.
"""

from typing import Dict, List, Optional

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PyQt6.QtWidgets import QSizePolicy, QWidget

from .theme import STATUS_LABELS, status_color

_MIN_SHOT_WIDTH = 72
_GAP = 4
_HEIGHT = 78


class TimelineWidget(QWidget):
    shot_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._shots: List[Dict] = []       # [{"shot_id": str, "duration": float}]
        self._status: Dict[str, str] = {}
        self._selected: Optional[str] = None
        self._hover: Optional[str] = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumHeight(_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName("Shot timeline")

    # -- public API ------------------------------------------------------

    def load_shots(self, shots: List[Dict]) -> None:
        """``shots``: [{"shot_id": str, "duration": seconds}, ...] in production order."""
        self._shots = [
            {"shot_id": str(s["shot_id"]), "duration": max(0.1, float(s.get("duration", 4.0)))}
            for s in shots
        ]
        self._status = {s["shot_id"]: "pending" for s in self._shots}
        if self._selected not in self._status:
            self._selected = None
        self.updateGeometry()
        self.update()

    def set_status(self, shot_id: str, status: str) -> None:
        if shot_id in self._status and self._status[shot_id] != status:
            self._status[shot_id] = status
            self.update()

    def status_of(self, shot_id: str) -> str:
        return self._status.get(shot_id, "pending")

    def shot_ids(self) -> List[str]:
        return [s["shot_id"] for s in self._shots]

    def selected(self) -> Optional[str]:
        return self._selected

    def select(self, shot_id: Optional[str]) -> None:
        if shot_id == self._selected or (shot_id is not None and shot_id not in self._status):
            return
        self._selected = shot_id
        self.update()
        if shot_id is not None:
            self.shot_selected.emit(shot_id)

    # -- geometry ---------------------------------------------------------

    def sizeHint(self) -> QSize:
        return QSize(max(300, len(self._shots) * (_MIN_SHOT_WIDTH + _GAP)), _HEIGHT)

    def minimumSizeHint(self) -> QSize:
        return QSize(len(self._shots) * (_MIN_SHOT_WIDTH + _GAP), _HEIGHT)

    def _rects(self) -> Dict[str, QRectF]:
        n = len(self._shots)
        if n == 0:
            return {}
        usable = max(1, self.width() - _GAP * (n + 1))
        total = sum(s["duration"] for s in self._shots)
        widths = [max(_MIN_SHOT_WIDTH, usable * s["duration"] / total) for s in self._shots]
        rects, x = {}, float(_GAP)
        for s, w in zip(self._shots, widths):
            rects[s["shot_id"]] = QRectF(x, 6, w, self.height() - 12)
            x += w + _GAP
        return rects

    def _shot_at(self, pos: QPointF) -> Optional[str]:
        for shot_id, r in self._rects().items():
            if r.contains(pos):
                return shot_id
        return None

    # -- events -----------------------------------------------------------

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self._shots:
            p.setPen(QColor("#7d8590"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No shots yet — enter a prompt and press Generate")
            return
        name_font, small_font = QFont(self.font()), QFont(self.font())
        name_font.setBold(True)
        small_font.setPointSizeF(max(7.0, self.font().pointSizeF() - 1.5))
        fm_name, fm_small = QFontMetrics(name_font), QFontMetrics(small_font)

        rects = self._rects()
        for s in self._shots:
            sid, r = s["shot_id"], rects[s["shot_id"]]
            status = self._status.get(sid, "pending")
            color = status_color(status)
            if sid == self._hover:
                color = color.lighter(115)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawRoundedRect(r, 6, 6)
            if sid == self._selected:
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(QColor("#ffffff"), 2))
                p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 6, 6)
            inner = r.adjusted(8, 6, -8, -6)
            p.setPen(QColor("#ffffff"))
            p.setFont(name_font)
            p.drawText(inner, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                       fm_name.elidedText(sid, Qt.TextElideMode.ElideRight, int(inner.width())))
            p.setFont(small_font)
            p.setPen(QColor(255, 255, 255, 215))
            label = f"{STATUS_LABELS.get(status, status)} · {s['duration']:g}s"
            p.drawText(inner, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
                       fm_small.elidedText(label, Qt.TextElideMode.ElideRight, int(inner.width())))

        if self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor("#4c8dff"), 1, Qt.PenStyle.DashLine))
            p.drawRect(self.rect().adjusted(0, 0, -1, -1))

    def mousePressEvent(self, event) -> None:
        shot = self._shot_at(event.position())
        if shot is not None:
            self.setFocus()
            self.select(shot)

    def mouseMoveEvent(self, event) -> None:
        shot = self._shot_at(event.position())
        if shot != self._hover:
            self._hover = shot
            self.setCursor(Qt.CursorShape.PointingHandCursor if shot else Qt.CursorShape.ArrowCursor)
            self.update()

    def leaveEvent(self, _event) -> None:
        self._hover = None
        self.update()

    def keyPressEvent(self, event) -> None:
        ids = self.shot_ids()
        if not ids or event.key() not in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            return super().keyPressEvent(event)
        i = ids.index(self._selected) if self._selected in ids else (-1 if event.key() == Qt.Key.Key_Right else 0)
        i = min(len(ids) - 1, i + 1) if event.key() == Qt.Key.Key_Right else max(0, i - 1)
        self.select(ids[i])

    def focusInEvent(self, e) -> None:
        super().focusInEvent(e); self.update()

    def focusOutEvent(self, e) -> None:
        super().focusOutEvent(e); self.update()
