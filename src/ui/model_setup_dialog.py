"""ModelSetupDialog — choose providers, API keys and the render backend."""

import logging
from typing import Any, Dict

from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox,
    QLabel, QLineEdit, QVBoxLayout,
)

from src.providers.provider_factory import validate_provider_config

logger = logging.getLogger(__name__)

TEXT_VISION = ["gemini", "openai", "anthropic", "huggingface", "ollama", "dummy"]
IMAGE = ["gemini", "openai", "diffusers", "dummy"]
RENDERERS = ["auto", "godot", "blender"]
ENGINES = ["eevee", "cycles", "workbench"]


class ModelSetupDialog(QDialog):
    def __init__(self, config: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.config = dict(config or {})
        self.setWindowTitle("Settings")
        self.setMinimumWidth(480)

        self.text_combo = self._combo(TEXT_VISION, "text_provider")
        self.vision_combo = self._combo(TEXT_VISION, "vision_provider")
        self.image_combo = self._combo(IMAGE, "image_provider")
        self.offline = QCheckBox("Offline demo mode (placeholder providers, no API keys, no cost)")
        self.offline.setChecked(all(c.currentText() == "dummy"
                                    for c in (self.text_combo, self.vision_combo, self.image_combo)))
        self.offline.toggled.connect(self._offline_toggled)

        models = QGroupBox("Models")
        f = QFormLayout(models)
        f.addRow("Text (planning):", self.text_combo)
        f.addRow("Vision (validation):", self.vision_combo)
        f.addRow("Image (assets):", self.image_combo)

        self.keys = {}
        keys_box = QGroupBox("API keys")
        kf = QFormLayout(keys_box)
        for label, field in [("Gemini", "gemini_api_key"), ("OpenAI", "openai_api_key"),
                             ("Anthropic", "anthropic_api_key")]:
            edit = QLineEdit(self._real_key(field))
            edit.setEchoMode(QLineEdit.EchoMode.Password)
            edit.setPlaceholderText("not set")
            edit.textChanged.connect(self._revalidate)
            self.keys[field] = edit
            kf.addRow(label + ":", edit)
        self.hf_repo = QLineEdit(str(self.config.get("hf_repo_id", "")))
        self.hf_image_repo = QLineEdit(str(self.config.get("hf_image_repo_id", "")))
        for e in (self.hf_repo, self.hf_image_repo):
            e.textChanged.connect(self._revalidate)
        kf.addRow("HuggingFace text repo:", self.hf_repo)
        kf.addRow("Diffusers image repo:", self.hf_image_repo)

        self.renderer_combo = self._combo(RENDERERS, "renderer", default="auto")
        self.engine_combo = self._combo(ENGINES, "blender_engine", default="eevee")
        render = QGroupBox("Rendering")
        rf = QFormLayout(render)
        rf.addRow("Backend:", self.renderer_combo)
        rf.addRow("Blender engine:", self.engine_combo)

        self.message = QLabel("")
        self.message.setWordWrap(True)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        for w in (self.offline, models, keys_box, render, self.message, self.buttons):
            layout.addWidget(w)
        for c in (self.text_combo, self.vision_combo, self.image_combo, self.renderer_combo):
            c.currentTextChanged.connect(self._revalidate)
        self._revalidate()

    # -- helpers -----------------------------------------------------------

    def _combo(self, items, key, default=None) -> QComboBox:
        c = QComboBox()
        c.addItems(items)
        current = self.config.get(key, default or items[0])
        c.setCurrentText(current if current in items else (default or items[0]))
        return c

    def _real_key(self, field: str) -> str:
        v = str(self.config.get(field, "") or "")
        return "" if v.endswith("_HERE") else v

    def _offline_toggled(self, on: bool) -> None:
        if on:
            for c in (self.text_combo, self.vision_combo, self.image_combo):
                c.setCurrentText("dummy")

    def _candidate(self) -> Dict[str, Any]:
        cfg = dict(self.config)
        cfg.update(
            text_provider=self.text_combo.currentText(),
            vision_provider=self.vision_combo.currentText(),
            image_provider=self.image_combo.currentText(),
            llm_provider=self.text_combo.currentText(),
            hf_repo_id=self.hf_repo.text().strip(),
            hf_image_repo_id=self.hf_image_repo.text().strip(),
            renderer=self.renderer_combo.currentText(),
            blender_engine=self.engine_combo.currentText(),
        )
        for field, edit in self.keys.items():
            typed = edit.text().strip()
            # Keep the template placeholder if the user typed nothing, so
            # validate_provider_config still sees "not set".
            cfg[field] = typed if typed else (self.config.get(field) or f"{field.upper()}_HERE")
        return cfg

    def _revalidate(self) -> None:
        ok, msg = validate_provider_config(self._candidate())
        self.message.setText("✓ Ready" if ok else f"⚠ {msg}")
        self.message.setStyleSheet("color:#2fbf71;" if ok else "color:#e0b34a;")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(ok)
        self.engine_combo.setEnabled(self.renderer_combo.currentText() != "godot")

    # -- public ------------------------------------------------------------

    def exec(self) -> bool:
        return super().exec() == QDialog.DialogCode.Accepted

    def get_updated_config(self) -> Dict[str, Any]:
        return self._candidate()
