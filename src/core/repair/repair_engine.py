"""
RepairEngine — turns validator feedback into a revised, re-renderable shot.

Scope is deliberately narrow: this engine's job is to produce a corrected
Shot (via Director.repair_shot) and persist it back into WorldStateManager
so downstream steps (SceneComposer, GodotRenderer) see the revision when
they next read shot data. It does NOT re-render or re-validate itself —
that loop lives in PipelineOrchestrator, which already owns compose/
render/validate sequencing and has direct access to GodotRenderer (this
class deliberately does not, to avoid duplicating that wiring).
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class RepairEngineError(Exception):
    """Raised when a shot can't be repaired (unknown shot, no world state, etc.)."""


class RepairEngine:
    """Produces a corrected Shot from validator feedback and persists it."""

    def __init__(self, asset_manager, scene_composer, director, world_state):
        self.asset_manager = asset_manager
        self.scene_composer = scene_composer
        self.director = director
        self.world_state = world_state
        logger.info("RepairEngine ready")

    def repair_shot(self, shot_id: str, feedback: str):
        """Ask Director to revise `shot_id` based on `feedback`, persist the
        revision to world state, and return the revised Shot.

        Raises RepairEngineError if there's no world state to persist into
        (repairing without persisting would silently be undone the next
        time something reads the shot back out of world state).
        """
        if self.world_state is None:
            raise RepairEngineError(
                "RepairEngine requires a WorldStateManager to persist repairs into"
            )

        revised_shot = self.director.repair_shot(shot_id, feedback)
        self._persist_revision(revised_shot)
        logger.info(f"Repaired shot '{shot_id}': {feedback[:100]}")
        return revised_shot

    def _persist_revision(self, shot) -> None:
        self.world_state.add_shot(shot.shot_id, {
            "scene_description": shot.scene_description,
            "characters": shot.characters,
            "camera_angle": shot.camera_angle,
            "lighting": shot.lighting,
            "action": shot.action,
            "duration_seconds": shot.duration_seconds,
        })
