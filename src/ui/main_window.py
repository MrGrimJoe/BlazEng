"""Main Window — Two-panel UI (STUB)."""
import logging
from PyQt6.QtWidgets import QMainWindow, QWidget, QHBoxLayout, QLabel, QStatusBar
from PyQt6.QtCore import pyqtSignal

logger = logging.getLogger(__name__)

class MainWindow(QMainWindow):
    model_change_requested = pyqtSignal()
    pipeline_requested = pyqtSignal(str, str)
    
    def __init__(self, config, world_state, orchestrator):
        super().__init__()
        self.config = config
        self.world_state = world_state
        self.orchestrator = orchestrator
        self.setWindowTitle("AI Production Studio")
        self.setGeometry(100, 100, 1200, 800)
        
        central = QWidget()
        layout = QHBoxLayout()
        layout.addWidget(QLabel("AI Production Studio (stub implementation)"))
        central.setLayout(layout)
        self.setCentralWidget(central)
        
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("Ready (stub)")
        
        logger.info("MainWindow initialized (stub)")
    
    def update_shot_status(self, shot_id, status, details):
        self.status.showMessage(f"Shot {shot_id}: {status}")
    
    def pipeline_complete(self, msg):
        self.status.showMessage("Pipeline complete")
