"""
PipelineOrchestrator — executes a Director task schedule end to end.

Dispatches each Task (from Director.create_task_schedule) to the right
component: asset generation -> AssetManager, scene composition ->
SceneComposer, rendering -> GodotRenderer. Shot metadata is read back from
WorldStateManager (which Director seeds when it builds a plan) rather than
threaded through task payloads, so this stays correct even if a future
Director change alters what it puts in a Task's payload.

Validation/repair are intentionally soft dependencies right now: Phase 3
(ValidatorManager, RepairEngine) isn't implemented yet (see CONTRIBUTING.md),
so by default this orchestrator marks a shot "rendered" and skips the
validate/repair step rather than crashing on a stub with no real behavior.
Set `skip_validation: false` in config once Phase 3 lands — at that point
this will call into validator_mgr for real and propagate whatever it does.
"""

import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class OrchestratorError(Exception):
    """Raised when the pipeline cannot proceed (not per-task failures)."""


class ShotFailure:
    """Records that a single shot failed somewhere in the pipeline.

    Collected rather than raised immediately, so one bad shot doesn't
    necessarily abort every other shot in the same production.
    """

    def __init__(self, shot_id: str, task_type: str, error: Exception):
        self.shot_id = shot_id
        self.task_type = task_type
        self.error = error

    def __repr__(self) -> str:
        return f"ShotFailure(shot_id={self.shot_id!r}, task_type={self.task_type!r}, error={self.error!r})"


class PipelineOrchestrator:
    """Runs a Director-produced task schedule against the real pipeline components."""

    def __init__(
        self,
        config: Dict[str, Any],
        world_state,
        asset_manager,
        scene_composer,
        validator_mgr,
        repair_engine,
        godot_renderer=None,
    ):
        self.config = config
        self.world_state = world_state
        self.asset_manager = asset_manager
        self.scene_composer = scene_composer
        self.validator_mgr = validator_mgr
        self.repair_engine = repair_engine
        self.godot_renderer = godot_renderer
        self.skip_validation = bool(config.get("skip_validation", True))

        self._tasks: List[Any] = []
        self._asset_paths: Dict[str, Any] = {}   # character/object name -> Path
        self._scene_paths: Dict[str, Any] = {}   # shot_id -> Path
        self._rendered_frames: Dict[str, List[Any]] = {}  # shot_id -> [Path, ...]
        self.failures: List[ShotFailure] = []

        # Optional UI progress hook: called as (current_index, total_tasks).
        self.on_progress: Optional[Callable[[int, int], None]] = None
        # Optional per-shot status hook: called as (shot_id, status, details).
        self.on_shot_update: Optional[Callable[[str, str, str], None]] = None

        logger.info("PipelineOrchestrator ready")

    def load_task_schedule(self, tasks: List[Any]) -> None:
        self._tasks = list(tasks)
        logger.info(f"Loaded {len(self._tasks)} tasks")

    def run_pipeline(self) -> bool:
        """Execute every loaded task in order. Returns True iff no shot failed.

        A failure in one shot's task is recorded in self.failures and does
        NOT stop other shots' tasks from running — see class docstring.
        Returns False if self.failures is non-empty after the run.
        """
        if not self._tasks:
            raise OrchestratorError("No tasks loaded — call load_task_schedule() first")

        total = len(self._tasks)
        dispatch = {
            "generate_asset": self._run_generate_asset,
            "compose_scene": self._run_compose_scene,
            "render": self._run_render,
            "validate": self._run_validate,
        }

        for i, task in enumerate(self._tasks):
            handler = dispatch.get(task.task_type)
            if handler is None:
                logger.warning(f"Unknown task type '{task.task_type}', skipping")
                continue

            try:
                handler(task)
            except Exception as e:  # noqa: BLE001 - deliberately broad, see docstring
                logger.error(f"Task {task.task_type} failed for shot {task.shot_id}: {e}")
                self.failures.append(ShotFailure(task.shot_id, task.task_type, e))
                if self.on_shot_update:
                    self.on_shot_update(task.shot_id, "failed", str(e))

            if self.on_progress:
                self.on_progress(i + 1, total)

        success = not self.failures
        logger.info(
            f"Pipeline run complete: {total - len(self.failures)}/{total} tasks "
            f"succeeded, {len(self.failures)} failed"
        )
        return success

    def cancel(self) -> None:
        """Clear remaining tasks so a subsequent run_pipeline() call is a no-op."""
        self._tasks = []
        logger.info("Pipeline cancelled")

    # ------------------------------------------------------------------
    # Task handlers
    # ------------------------------------------------------------------

    def _run_generate_asset(self, task) -> None:
        asset_type = task.payload["asset_type"]
        name = task.payload["name"]

        description = name
        if asset_type == "character" and self.world_state is not None:
            character = self.world_state.get_character(name)
            if character:
                description = character.get("appearance") or name

        path = self.asset_manager.get_asset(asset_type, name, description)
        self._asset_paths[name] = path
        logger.debug(f"Asset ready: {name} -> {path}")

    def _run_compose_scene(self, task) -> None:
        shot_id = task.shot_id
        shot = self._get_shot(shot_id)

        missing = [c for c in shot.characters if c not in self._asset_paths]
        if missing:
            raise OrchestratorError(
                f"Shot '{shot_id}' needs assets for {missing}, but they were never "
                "generated — generate_asset tasks may have failed or run out of order"
            )

        assets = {c: self._asset_paths[c] for c in shot.characters}
        scene_path = self.scene_composer.compose_shot(shot, assets)
        self._scene_paths[shot_id] = scene_path
        if self.world_state is not None:
            self.world_state.set_shot_status(shot_id, "composed")
        if self.on_shot_update:
            self.on_shot_update(shot_id, "composed", str(scene_path))

    def _run_render(self, task) -> None:
        shot_id = task.shot_id
        if self.godot_renderer is None:
            raise OrchestratorError(
                "No GodotRenderer configured — pass godot_renderer= to "
                "PipelineOrchestrator, or set godot_binary_path in config.yaml"
            )
        scene_path = self._scene_paths.get(shot_id)
        if scene_path is None:
            raise OrchestratorError(f"No composed scene for shot '{shot_id}' — compose_scene may have failed")

        shot = self._get_shot(shot_id)
        frames = self.godot_renderer.render_shot(scene_path, shot_id, duration_seconds=shot.duration_seconds)
        self._rendered_frames[shot_id] = frames
        if self.world_state is not None:
            self.world_state.set_shot_status(shot_id, "rendered")
        if self.on_shot_update:
            self.on_shot_update(shot_id, "rendered", f"{len(frames)} frames")

    def _run_validate(self, task) -> None:
        shot_id = task.shot_id
        if self.skip_validation:
            logger.debug(f"Validation skipped for {shot_id} (skip_validation: true)")
            if self.world_state is not None:
                self.world_state.set_shot_status(shot_id, "rendered_unvalidated")
            return

        max_attempts = int(self.config.get("max_repair_attempts", 3))
        attempt = 0

        while True:
            frames = self._rendered_frames.get(shot_id)
            if not frames:
                raise OrchestratorError(f"No rendered frames for shot '{shot_id}' to validate")
            shot = self._get_shot(shot_id)

            report = self.validator_mgr.validate_frame(frames[0], shot, self.world_state)

            if report.passed:
                if self.world_state is not None:
                    self.world_state.set_shot_status(shot_id, "validated")
                if self.on_shot_update:
                    self.on_shot_update(shot_id, "validated", "all checks passed")
                return

            if attempt >= max_attempts:
                if self.world_state is not None:
                    self.world_state.set_shot_status(shot_id, "validation_failed")
                raise OrchestratorError(
                    f"Shot '{shot_id}' failed validation after {max_attempts} repair "
                    f"attempts: {report.failure_feedback}"
                )

            attempt += 1
            logger.warning(
                f"Shot '{shot_id}' failed validation (attempt {attempt}/{max_attempts}): "
                f"{report.failure_feedback}"
            )
            if self.on_shot_update:
                self.on_shot_update(shot_id, "repairing", report.failure_feedback)

            # Repair, then re-compose and re-render before looping back to
            # re-validate — a revised scene_description or character list
            # is meaningless until it's actually re-rendered into pixels.
            revised_shot = self.repair_engine.repair_shot(shot_id, report.failure_feedback)
            self._recompose_and_rerender(revised_shot)

    def _recompose_and_rerender(self, shot) -> None:
        """Re-run compose+render for an already-repaired shot.

        Assumes any newly-referenced characters already have assets — if
        Director's repair introduces a brand-new character name that was
        never generated, this will raise via SceneComposer's own missing-
        asset check, which is the correct, honest failure rather than
        silently rendering without it.
        """
        assets = {c: self._asset_paths[c] for c in shot.characters if c in self._asset_paths}
        missing = [c for c in shot.characters if c not in self._asset_paths]
        if missing:
            raise OrchestratorError(
                f"Repaired shot '{shot.shot_id}' references new character(s) {missing} "
                "with no generated asset — RepairEngine cannot introduce new characters "
                "that were never in the original task schedule"
            )

        scene_path = self.scene_composer.compose_shot(shot, assets)
        self._scene_paths[shot.shot_id] = scene_path

        if self.godot_renderer is None:
            raise OrchestratorError("No GodotRenderer configured — cannot re-render repaired shot")
        frames = self.godot_renderer.render_shot(
            scene_path, shot.shot_id, duration_seconds=shot.duration_seconds
        )
        self._rendered_frames[shot.shot_id] = frames

    def _get_shot(self, shot_id: str):
        """Reconstruct a lightweight Shot-like object from world state.

        Uses Director.Shot's constructor rather than duck-typing, so
        callers get a real Shot with the same defaults/validation.
        """
        from src.core.director.director import Shot

        if self.world_state is None:
            raise OrchestratorError("No WorldStateManager configured — cannot look up shot metadata")
        data = self.world_state.get_shot(shot_id)
        if data is None:
            raise OrchestratorError(f"Unknown shot: {shot_id}")
        return Shot(
            shot_id=shot_id,
            scene_description=data.get("scene_description", ""),
            characters=data.get("characters", []),
            camera_angle=data.get("camera_angle", "medium shot"),
            lighting=data.get("lighting", "natural daylight"),
            action=data.get("action", ""),
            duration_seconds=data.get("duration_seconds", 4.0),
        )
