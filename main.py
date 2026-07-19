"""
AI Production Studio — Entry Point.
Loads config, shows model setup dialog on first run, initialises all systems
via provider_factory (three independent slots: text, vision, image), launches UI.
"""
import sys
import logging
import yaml
from pathlib import Path
from PyQt6.QtWidgets import QApplication

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
    config_path = Path("config.yaml")
    if not config_path.exists():
        logger.warning("config.yaml not found — run setup.py first")
        return {}
    with open(config_path) as f:
        return yaml.safe_load(f) or {}


def save_config(config: dict) -> None:
    with open("config.yaml", "w") as f:
        yaml.dump(config, f, default_flow_style=False)


def ensure_storage(config: dict) -> None:
    base = Path(config.get("storage_path", "./storage"))
    for folder in ["database", "projects", "assets/characters",
                   "assets/objects", "assets/environments",
                   "cache", "logs", "models"]:
        (base / folder).mkdir(parents=True, exist_ok=True)


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
    """Wire all pipeline components. Text, vision, and image providers are independent."""
    from src.core.world_state.world_state import WorldStateManager
    from src.providers.provider_factory import (
        get_text_provider, get_vision_provider, get_image_provider
    )
    from src.core.asset_manager.asset_manager import AssetManager
    from src.core.scene_composer.scene_composer import SceneComposer
    from src.core.validator.validator_manager import ValidatorManager
    from src.core.repair.repair_engine import RepairEngine
    from src.core.director.director import Director
    from src.core.orchestrator.orchestrator import PipelineOrchestrator

    world_state     = WorldStateManager(config)
    text_provider   = get_text_provider(config)
    vision_provider = get_vision_provider(config)
    image_provider  = get_image_provider(config)

    asset_manager  = AssetManager(config, image_provider)
    scene_composer = SceneComposer(config)
    validator_mgr  = ValidatorManager(text_provider, vision_provider)
    director       = Director(text_provider, world_state)
    repair_engine  = RepairEngine(asset_manager, scene_composer, director, world_state)

    orchestrator = PipelineOrchestrator(
        config, world_state, asset_manager,
        scene_composer, validator_mgr, repair_engine
    )
    return director, orchestrator, world_state, asset_manager


def main() -> None:
    logger.info("AI Production Studio starting...")
    config = load_config()
    ensure_storage(config)

    app = QApplication(sys.argv)
    app.setApplicationName("AI Production Studio")
    app.setStyle("Fusion")

    if needs_model_setup(config):
        config = show_model_setup_dialog(config)

    from src.ui.main_window import MainWindow

    try:
        director, orchestrator, world_state, asset_manager = build_pipeline(config)
    except Exception as e:
        logger.error(f"Pipeline init failed: {e}", exc_info=True)
        director = orchestrator = world_state = asset_manager = None

    window = MainWindow(config, world_state, orchestrator)

    def reconfigure_model():
        nonlocal config, director, orchestrator, world_state, asset_manager
        config = show_model_setup_dialog(config)
        try:
            director, orchestrator, world_state, asset_manager = build_pipeline(config)
            window.world_state = world_state
            window.orchestrator = orchestrator
            window.status.showMessage("Models updated")
        except Exception as e:
            logger.error(f"Rebuild after model switch failed: {e}", exc_info=True)

    window.model_change_requested.connect(reconfigure_model)

    if orchestrator and director:
        def run_pipeline(prompt: str, target: str) -> None:
            from PyQt6.QtCore import QThread, QObject, pyqtSignal

            class Worker(QObject):
                shot_updated   = pyqtSignal(str, str, str)
                pipeline_done  = pyqtSignal(str)
                pipeline_error = pyqtSignal(str)

                def __init__(self, prompt, target):
                    super().__init__()
                    self.prompt = prompt
                    self.target = target

                def run(self):
                    try:
                        plan  = director.generate_production_plan(self.prompt)
                        tasks = director.create_task_schedule(plan)
                        orchestrator.load_task_schedule(tasks)
                        shot_ids = [s.shot_id for s in plan.shots]
                        window.timeline.load_shots(shot_ids)
                        orchestrator.run_pipeline()
                        self.pipeline_done.emit("done")
                    except Exception as ex:
                        logger.error(f"Pipeline error: {ex}", exc_info=True)
                        self.pipeline_error.emit(str(ex))

            thread = QThread()
            worker = Worker(prompt, target)
            worker.moveToThread(thread)
            worker.shot_updated.connect(window.update_shot_status)
            worker.pipeline_done.connect(window.pipeline_complete)
            worker.pipeline_error.connect(
                lambda msg: window.status.showMessage(f"Error: {msg}")
            )
            thread.started.connect(worker.run)
            thread.start()
            window._thread = thread
            window._worker = worker

        window.pipeline_requested.connect(run_pipeline)

    window.show()
    logger.info("UI launched")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
