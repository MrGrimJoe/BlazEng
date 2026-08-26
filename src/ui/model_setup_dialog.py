"""Model Setup Dialog — First-run configuration (STUB)."""
import logging
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton

logger = logging.getLogger(__name__)

class ModelSetupDialog(QDialog):
    def __init__(self, config):
        super().__init__()
        self.config = config or {}
        self.setWindowTitle("Configure AI Models")
        layout = QVBoxLayout()
        layout.addWidget(QLabel("Model configuration stub"))
        layout.addWidget(QPushButton("OK"))
        self.setLayout(layout)
        logger.info("ModelSetupDialog initialized (stub)")
    
    def exec(self):
        return super().exec() == QDialog.DialogCode.Accepted
    
    def get_updated_config(self):
        return self.config
