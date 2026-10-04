"""AssetBrowser — Type → Name → Version tree with thumbnails and a context menu."""

import logging
import shutil
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QSize, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QIcon, QImageReader, QPixmap
from PyQt6.QtWidgets import QMenu, QMessageBox, QTreeWidget, QTreeWidgetItem

logger = logging.getLogger(__name__)

_ROLE_KIND = Qt.ItemDataRole.UserRole
_ROLE_PATH = Qt.ItemDataRole.UserRole + 1
_THUMB = 48
_MAX_THUMBS = 400   # beyond this, skip thumbnails so refresh stays instant


class AssetBrowser(QTreeWidget):
    """Reads storage/assets/<type>/<name>/v<N>/ straight from disk."""

    version_deleted = pyqtSignal(str, str, int)    # type, name, version

    def __init__(self, storage_path, parent=None):
        super().__init__(parent)
        self.assets_root = Path(storage_path) / "assets"
        self.setHeaderLabels(["Asset", "Details"])
        self.setIconSize(QSize(_THUMB, _THUMB))
        self.setUniformRowHeights(False)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.setColumnWidth(0, 200)
        self.refresh()

    # -- loading -----------------------------------------------------------

    def refresh(self) -> None:
        self.clear()
        thumbs = 0
        if not self.assets_root.exists():
            return
        for type_dir in sorted(p for p in self.assets_root.iterdir() if p.is_dir()):
            names = sorted(p for p in type_dir.iterdir() if p.is_dir())
            if not names:
                continue
            type_item = QTreeWidgetItem([type_dir.name.title(), f"{len(names)} asset(s)"])
            type_item.setData(0, _ROLE_KIND, "type")
            self.addTopLevelItem(type_item)
            for name_dir in names:
                versions = sorted((v for v in name_dir.iterdir() if v.is_dir() and v.name.startswith("v")),
                                  key=_version_number)
                name_item = QTreeWidgetItem([name_dir.name, f"{len(versions)} version(s)"])
                name_item.setData(0, _ROLE_KIND, "name")
                type_item.addChild(name_item)
                for v in versions:
                    image = _first_image(v)
                    item = QTreeWidgetItem([v.name, image.name if image else "no image"])
                    item.setData(0, _ROLE_KIND, "version")
                    item.setData(0, _ROLE_PATH, str(v))
                    if image is not None and thumbs < _MAX_THUMBS:
                        icon = _thumbnail(image)
                        if icon is not None:
                            item.setIcon(0, icon)
                            thumbs += 1
                    name_item.addChild(item)
            type_item.setExpanded(True)
            for i in range(type_item.childCount()):
                type_item.child(i).setExpanded(True)

    # -- context menu ---------------------------------------------------------

    def _menu(self, pos) -> None:
        item = self.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        target = self._folder_for(item)
        if target is not None:
            menu.addAction("Open folder", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(target))))
        if item.data(0, _ROLE_KIND) == "version":
            menu.addAction("Delete this version…", lambda: self.delete_version(item, confirm=True))
        if not menu.isEmpty():
            menu.exec(self.viewport().mapToGlobal(pos))

    def _folder_for(self, item) -> Optional[Path]:
        path = item.data(0, _ROLE_PATH)
        if path:
            return Path(path)
        if item.data(0, _ROLE_KIND) == "name":
            return self.assets_root / item.parent().text(0).lower() / item.text(0)
        return None

    def delete_version(self, item: QTreeWidgetItem, confirm: bool = True) -> bool:
        """Delete a version directory (and refresh). Returns True if deleted."""
        if item.data(0, _ROLE_KIND) != "version":
            return False
        version_dir = Path(item.data(0, _ROLE_PATH))
        name_item = item.parent()
        if confirm:
            answer = QMessageBox.question(
                self, "Delete asset version",
                f"Permanently delete {name_item.text(0)} {version_dir.name}?")
            if answer != QMessageBox.StandardButton.Yes:
                return False
        try:
            shutil.rmtree(version_dir)
        except OSError as e:
            logger.error(f"Could not delete {version_dir}: {e}")
            return False
        self.version_deleted.emit(name_item.parent().text(0), name_item.text(0), _version_number(version_dir))
        self.refresh()
        return True


def _version_number(p: Path) -> int:
    try:
        return int(p.name.lstrip("v"))
    except ValueError:
        return 0


def _first_image(version_dir: Path) -> Optional[Path]:
    for ext in ("*.png", "*.jpg", "*.jpeg", "*.webp"):
        found = sorted(version_dir.glob(ext))
        if found:
            return found[0]
    return None


def _thumbnail(image: Path) -> Optional[QIcon]:
    """Decode at thumbnail size directly (cheap), instead of loading the full image."""
    reader = QImageReader(str(image))
    size = reader.size()
    if not size.isValid():
        return None
    size.scale(_THUMB, _THUMB, Qt.AspectRatioMode.KeepAspectRatio)
    reader.setScaledSize(size)
    img = reader.read()
    return None if img.isNull() else QIcon(QPixmap.fromImage(img))
