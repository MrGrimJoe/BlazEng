"""Dark theme and the shared status colour map used across the UI."""

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

# Shot status -> colour. "In progress" statuses are amber, finished ones green.
STATUS_COLORS = {
    "pending": "#5b6270",
    "composed": "#d0a23b",
    "repairing": "#d0a23b",
    "rendered": "#3f9d5f",
    "rendered_unvalidated": "#3f9d5f",
    "validated": "#2fbf71",
    "failed": "#c8484f",
    "validation_failed": "#c8484f",
}
STATUS_LABELS = {
    "pending": "Pending",
    "composed": "Scene composed",
    "repairing": "Repairing",
    "rendered": "Rendered",
    "rendered_unvalidated": "Rendered (not validated)",
    "validated": "Validated",
    "failed": "Failed",
    "validation_failed": "Failed validation",
}


def status_color(status: str) -> QColor:
    return QColor(STATUS_COLORS.get(status, STATUS_COLORS["pending"]))


def apply_dark_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    p = QPalette()
    bg, base, text, accent = QColor("#1e2127"), QColor("#262a31"), QColor("#e6e8eb"), QColor("#4c8dff")
    p.setColor(QPalette.ColorRole.Window, bg)
    p.setColor(QPalette.ColorRole.WindowText, text)
    p.setColor(QPalette.ColorRole.Base, base)
    p.setColor(QPalette.ColorRole.AlternateBase, bg)
    p.setColor(QPalette.ColorRole.Text, text)
    p.setColor(QPalette.ColorRole.Button, QColor("#2d323a"))
    p.setColor(QPalette.ColorRole.ButtonText, text)
    p.setColor(QPalette.ColorRole.ToolTipBase, base)
    p.setColor(QPalette.ColorRole.ToolTipText, text)
    p.setColor(QPalette.ColorRole.Highlight, accent)
    p.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.ColorRole.PlaceholderText, QColor("#7d8590"))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor("#6b727c"))
    app.setPalette(p)
    app.setStyleSheet(
        "QPushButton#primary { background:#4c8dff; color:white; border:none; padding:7px 16px; border-radius:5px; font-weight:600; }"
        "QPushButton#primary:disabled { background:#3a4150; color:#8a919c; }"
        "QPushButton#primary:hover:!disabled { background:#6aa0ff; }"
        "QPlainTextEdit, QTreeWidget, QTextBrowser { border:1px solid #343a44; border-radius:4px; }"
        "QTabWidget::pane { border:1px solid #343a44; border-radius:4px; }"
        "QProgressBar { border:1px solid #343a44; border-radius:4px; text-align:center; height:14px; }"
        "QProgressBar::chunk { background:#4c8dff; border-radius:3px; }"
    )
