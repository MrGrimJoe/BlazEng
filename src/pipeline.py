"""
Qt-free pipeline wiring shared by the GUI (main.py) and the headless CLI (cli.py).

Nothing here imports PyQt6, so it works on servers and in CI without a
display or Qt libraries installed.
"""

import logging
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

logger = logging.getLogger(__name__)

STORAGE_FOLDERS = [
    "database", "projects", "assets/characters", "assets/objects",
    "assets/environments", "cache", "logs", "models",
]


def load_config(path: str = "config.yaml") -> Dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        logger.warning(
            f"{config_path} not found — using built-in defaults. Copy config.yaml next to "
            "where you run blazeng (or run setup.py from a source checkout) to configure providers."
        )
        return {}
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_config(config: Dict[str, Any], path: str = "config.yaml") -> None:
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False)


def ensure_storage(config: Dict[str, Any]) -> Path:
    base = Path(config.get("storage_path", "./storage"))
    for folder in STORAGE_FOLDERS:
        (base / folder).mkdir(parents=True, exist_ok=True)
    return base


def build_pipeline(config: Dict[str, Any]):
    """Wire all pipeline components. Text, vision and image providers are independent.

    Returns (director, orchestrator, world_state, asset_manager).
    """
    from src.core.asset_manager.asset_manager import AssetManager
    from src.core.director.director import Director
    from src.core.orchestrator.orchestrator import PipelineOrchestrator
    from src.core.repair.repair_engine import RepairEngine
    from src.core.scene_composer.scene_composer import SceneComposer
    from src.core.validator.validator_manager import ValidatorManager
    from src.core.world_state.world_state import WorldStateManager
    from src.integrations.renderer_factory import get_renderer
    from src.providers.provider_factory import (
        get_image_provider, get_text_provider, get_vision_provider,
    )

    world_state = WorldStateManager(config)
    text_provider = get_text_provider(config)
    vision_provider = get_vision_provider(config)
    image_provider = get_image_provider(config)

    asset_manager = AssetManager(config, image_provider)
    scene_composer = SceneComposer(config)
    validator_mgr = ValidatorManager(text_provider, vision_provider)
    director = Director(text_provider, world_state)
    repair_engine = RepairEngine(asset_manager, scene_composer, director, world_state)
    renderer = get_renderer(config)

    orchestrator = PipelineOrchestrator(
        config, world_state, asset_manager,
        scene_composer, validator_mgr, repair_engine,
        renderer=renderer,
    )
    return director, orchestrator, world_state, asset_manager
