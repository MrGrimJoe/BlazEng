"""
Qt-free pipeline wiring shared by the GUI (main.py) and the headless CLI (cli.py).

Nothing here imports PyQt6, so it works on servers and in CI without a
display or Qt libraries installed.
"""

import logging
from pathlib import Path
from typing import Any, Dict

import yaml

from src import paths

logger = logging.getLogger(__name__)

STORAGE_FOLDERS = [
    "database", "projects", "assets/characters", "assets/objects",
    "assets/environments", "cache", "logs", "models",
]


def load_config(path: str = "config.yaml") -> Dict[str, Any]:
    """Load config. The default ``config.yaml`` resolves to the per-user file when installed."""
    config_path = paths.resolve_config_path(path)
    paths.ensure_user_config(config_path)
    if not config_path.exists():
        logger.warning(
            f"{config_path} not found — using built-in defaults. Copy config.yaml next to "
            "where you run blazeng (or run setup.py from a source checkout) to configure providers."
        )
        return paths.apply_installed_defaults({})
    with open(config_path, encoding="utf-8") as f:
        return paths.apply_installed_defaults(yaml.safe_load(f) or {})


def save_config(config: Dict[str, Any], path: str = "config.yaml") -> None:
    target = paths.resolve_config_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
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
    from src.providers.speech_providers import get_speech_provider
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
        speech_provider=get_speech_provider(config),
    )
    return director, orchestrator, world_state, asset_manager


def timeline_shots(plan, rendered, fps, world_state=None):
    """Shot dicts for the OTIO export, with the durations that were actually rendered.

    The plan's durations can be stale (a shot is lengthened to fit its dialogue
    after planning), so the length comes from the frame count when frames exist,
    and dialogue/metadata from world state when available.
    """
    out = []
    for s in plan.shots:
        data = (world_state.get_shot(s.shot_id) if world_state is not None else None) or {}
        frames = rendered.get(s.shot_id)
        duration = len(frames) / float(fps) if frames else data.get("duration_seconds", s.duration_seconds)
        out.append({
            "shot_id": s.shot_id, "duration_seconds": duration,
            "scene_description": data.get("scene_description", s.scene_description),
            "camera_angle": data.get("camera_angle", s.camera_angle),
            "lighting": data.get("lighting", s.lighting),
            "characters": data.get("characters", s.characters),
            "action": data.get("action", s.action),
            "dialogue": data.get("dialogue", s.dialogue),
        })
    return out
