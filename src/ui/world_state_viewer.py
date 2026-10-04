"""WorldStateViewer — characters and their latest continuity state, plus the event log."""

import logging

from PyQt6.QtWidgets import QTreeWidget, QTreeWidgetItem

logger = logging.getLogger(__name__)


class WorldStateViewer(QTreeWidget):
    """Reads through its own WorldStateManager (own SQLite connection), so it is
    safe to refresh on the GUI thread while the pipeline worker writes."""

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self._ws = None
        self.setHeaderLabels(["Character / field", "Value"])
        self.setColumnWidth(0, 190)
        self.setWordWrap(True)

    def _open(self):
        if self._ws is None:
            from src.core.world_state.world_state import WorldStateManager
            self._ws = WorldStateManager(self.config)
        return self._ws

    def refresh(self) -> None:
        try:
            ws = self._open()
            names = ws.list_characters()
            events = ws.get_events()
        except Exception as e:  # noqa: BLE001 - a viewer must never take the app down
            logger.warning(f"World state unavailable: {e}")
            return

        expanded = {self.topLevelItem(i).text(0) for i in range(self.topLevelItemCount())
                    if self.topLevelItem(i).isExpanded()}
        self.clear()
        for name in names:
            c = ws.get_character(name) or {}
            top = QTreeWidgetItem([name, c.get("appearance") or ""])
            for label, key in [("Clothing", "clothing"), ("Injuries", "injuries")]:
                if c.get(key):
                    top.addChild(QTreeWidgetItem([label, str(c[key])]))
            if c.get("props"):
                top.addChild(QTreeWidgetItem(["Props", ", ".join(map(str, c["props"]))]))
            self.addTopLevelItem(top)
            top.setExpanded(name in expanded or not expanded)
        if events:
            ev = QTreeWidgetItem(["Events", f"{len(events)}"])
            for e in events[-50:]:
                ev.addChild(QTreeWidgetItem([e.get("shot_id") or "—", e.get("description", "")]))
            self.addTopLevelItem(ev)

    def close_connection(self) -> None:
        if self._ws is not None:
            try:
                self._ws.close()
            finally:
                self._ws = None
