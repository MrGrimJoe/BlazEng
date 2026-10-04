"""
BlazEng — desktop entry point.
Loads config, shows model setup dialog on first run, initialises all systems
via provider_factory (three independent slots: text, vision, image), launches UI.
"""
import sys
import logging
from pathlib import Path
from PyQt6.QtWidgets import QApplication

from src import pipeline

LOG_PATH = Path("storage/logs")
LOG_PATH.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH / "studio.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger(__name__)


def load_config() -> dict:
    return pipeline.load_config("config.yaml")


def save_config(config: dict) -> None:
    pipeline.save_config(config, "config.yaml")


def ensure_storage(config: dict) -> None:
    pipeline.ensure_storage(config)


def needs_model_setup(config: dict) -> bool:
    from src.providers.provider_factory import validate_provider_config
    is_valid, _ = validate_provider_config(config)
    return not is_valid


def show_model_setup_dialog(config: dict) -> dict:
    from src.ui.model_setup_dialog import ModelSetupDialog
    dialog = ModelSetupDialog(config)
    if dialog.exec():
        config = dialog.get_updated_config()
        save_config(config)
        logger.info(
            f"Models configured — text: {config.get('text_provider')}, "
            f"vision: {config.get('vision_provider')}, image: {config.get('image_provider')}"
        )
    return config


def build_pipeline(config: dict):
    """Wire all pipeline components (see src/pipeline.py)."""
    return pipeline.build_pipeline(config)


def main() -> None:
    logger.info("BlazEng starting...")
    config = load_config()
    ensure_storage(config)

    app = QApplication(sys.argv)
    app.setApplicationName("BlazEng")
    from src.ui.theme import apply_dark_theme
    apply_dark_theme(app)

    if needs_model_setup(config):
        config = show_model_setup_dialog(config)

    from src.ui.main_window import MainWindow

    window = MainWindow(config)

    def reconfigure_model():
        nonlocal config
        config = show_model_setup_dialog(config)
        ensure_storage(config)
        window.set_config(config)

    window.model_change_requested.connect(reconfigure_model)
    window.show()
    logger.info("UI launched")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
