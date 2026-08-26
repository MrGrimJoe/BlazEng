"""
Tests for PipelineOrchestrator.

Includes fully offline unit tests (dummy providers, no Godot needed) and
a real end-to-end integration test that runs an actual Director plan
through a real Godot binary, gated the same way as
test_godot_renderer.py's integration test.
"""

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.core.asset_manager.asset_manager import AssetManager
from src.core.director.director import Director, Task
from src.core.orchestrator.orchestrator import (
    OrchestratorError,
    PipelineOrchestrator,
    ShotFailure,
)
from src.core.scene_composer.scene_composer import SceneComposer
from src.core.world_state.world_state import WorldStateManager
from src.providers.dummy_provider import DummyImageProvider, DummyShotPlanTextProvider


@pytest.fixture
def wired(tmp_path):
    """A fully wired, offline pipeline: real Director/WorldState/AssetManager/
    SceneComposer, dummy providers, no GodotRenderer (added per-test as needed)."""
    config = {"storage_path": str(tmp_path / "storage")}
    world_state = WorldStateManager(config)
    text_provider = DummyShotPlanTextProvider(num_shots=2)
    image_provider = DummyImageProvider()
    director = Director(text_provider, world_state)
    asset_manager = AssetManager(config, image_provider)
    scene_composer = SceneComposer(config)

    plan = director.generate_production_plan("A test story")
    tasks = director.create_task_schedule(plan)

    orchestrator = PipelineOrchestrator(
        config, world_state, asset_manager, scene_composer,
        validator_mgr=MagicMock(), repair_engine=MagicMock(),
        godot_renderer=None,
    )
    return orchestrator, plan, tasks, world_state


class TestLoadTaskSchedule:
    def test_run_without_loading_raises(self, wired):
        orchestrator, _, _, _ = wired
        with pytest.raises(OrchestratorError, match="No tasks loaded"):
            orchestrator.run_pipeline()

    def test_load_stores_tasks(self, wired):
        orchestrator, _, tasks, _ = wired
        orchestrator.load_task_schedule(tasks)
        assert len(orchestrator._tasks) == len(tasks)


class TestAssetGeneration:
    def test_generate_asset_task_populates_asset_paths(self, wired):
        orchestrator, plan, tasks, world_state = wired
        asset_tasks = [t for t in tasks if t.task_type == "generate_asset"]
        orchestrator.load_task_schedule(asset_tasks)

        success = orchestrator.run_pipeline()

        assert success
        assert "protagonist" in orchestrator._asset_paths
        assert orchestrator._asset_paths["protagonist"].exists()

    def test_generate_asset_uses_world_state_appearance(self, wired):
        orchestrator, plan, tasks, world_state = wired
        image_provider = orchestrator.asset_manager.image_provider
        asset_tasks = [t for t in tasks if t.task_type == "generate_asset"]
        orchestrator.load_task_schedule(asset_tasks)
        orchestrator.run_pipeline()

        # DummyShotPlanTextProvider seeds "protagonist" with
        # appearance "unspecified" — verify that description made it
        # through to the image provider's call log, not a bare name.
        assert image_provider.call_log[0]["prompt"] == "unspecified"


class TestComposeScene:
    def test_compose_without_assets_fails_the_shot(self, wired):
        orchestrator, plan, tasks, world_state = wired
        compose_tasks = [t for t in tasks if t.task_type == "compose_scene"]
        orchestrator.load_task_schedule(compose_tasks)

        success = orchestrator.run_pipeline()

        assert success is False
        assert len(orchestrator.failures) == len(compose_tasks)
        assert all(isinstance(f, ShotFailure) for f in orchestrator.failures)

    def test_compose_after_assets_succeeds(self, wired):
        orchestrator, plan, tasks, world_state = wired
        asset_tasks = [t for t in tasks if t.task_type == "generate_asset"]
        compose_tasks = [t for t in tasks if t.task_type == "compose_scene"]
        orchestrator.load_task_schedule(asset_tasks + compose_tasks)

        success = orchestrator.run_pipeline()

        assert success
        assert len(orchestrator._scene_paths) == len(compose_tasks)
        for shot in plan.shots:
            assert world_state.get_shot(shot.shot_id)["status"] == "composed"


class TestRenderWithoutRenderer:
    def test_render_without_godot_renderer_fails_clearly(self, wired):
        orchestrator, plan, tasks, world_state = wired
        all_but_validate = [t for t in tasks if t.task_type != "validate"]
        orchestrator.load_task_schedule(all_but_validate)

        success = orchestrator.run_pipeline()

        assert success is False
        render_failures = [f for f in orchestrator.failures if f.task_type == "render"]
        assert len(render_failures) == len(plan.shots)
        assert "GodotRenderer" in str(render_failures[0].error)

    def test_render_without_composed_scene_fails_clearly(self, wired):
        """Rendering a shot that never got a composed scene (e.g. because
        compose_scene failed or was skipped) should fail with a clear
        message pointing at the missing scene, not a KeyError."""
        orchestrator, plan, tasks, world_state = wired
        mock_renderer = MagicMock()
        orchestrator.godot_renderer = mock_renderer

        render_tasks = [t for t in tasks if t.task_type == "render"]
        orchestrator.load_task_schedule(render_tasks)
        success = orchestrator.run_pipeline()

        assert success is False
        assert "No composed scene" in str(orchestrator.failures[0].error)
        mock_renderer.render_shot.assert_not_called()


class TestValidateEdgeCases:
    def test_validate_without_rendered_frames_fails_clearly(self, wired):
        orchestrator, plan, tasks, world_state = wired
        orchestrator.skip_validation = False
        validate_tasks = [t for t in tasks if t.task_type == "validate"]
        orchestrator.load_task_schedule(validate_tasks)

        success = orchestrator.run_pipeline()

        assert success is False
        assert "No rendered frames" in str(orchestrator.failures[0].error)

    def test_validate_calls_validator_with_first_frame_and_shot(self, wired):
        orchestrator, plan, tasks, world_state = wired
        orchestrator.skip_validation = False
        shot_id = plan.shots[0].shot_id
        fake_frames = [Path("/tmp/f0.png"), Path("/tmp/f1.png")]
        orchestrator._rendered_frames[shot_id] = fake_frames

        validate_task = next(t for t in tasks if t.task_type == "validate" and t.shot_id == shot_id)
        orchestrator.load_task_schedule([validate_task])
        orchestrator.run_pipeline()

        orchestrator.validator_mgr.validate_frame.assert_called_once()
        call_args = orchestrator.validator_mgr.validate_frame.call_args.args
        assert call_args[0] == fake_frames[0]  # first frame only


class TestGetShotEdgeCases:
    def test_get_shot_without_world_state_raises(self, wired):
        orchestrator, plan, tasks, world_state = wired
        orchestrator.world_state = None
        compose_tasks = [t for t in tasks if t.task_type == "compose_scene"]
        # generate assets first so we reach the world-state lookup inside
        # compose, not an earlier unrelated failure
        asset_tasks = [t for t in tasks if t.task_type == "generate_asset"]
        # Populate asset paths directly to isolate the world_state=None path
        for t in asset_tasks:
            orchestrator._asset_paths[t.payload["name"]] = Path("/tmp/fake.png")

        orchestrator.load_task_schedule(compose_tasks)
        success = orchestrator.run_pipeline()

        assert success is False
        assert "No WorldStateManager" in str(orchestrator.failures[0].error)

    def test_get_shot_unknown_shot_id_raises(self, wired):
        orchestrator, plan, tasks, world_state = wired
        bogus_task = Task(task_type="compose_scene", shot_id="nonexistent_shot", payload={})
        orchestrator.load_task_schedule([bogus_task])
        success = orchestrator.run_pipeline()

        assert success is False
        assert "Unknown shot" in str(orchestrator.failures[0].error)


class TestRenderWithMockRenderer:
    def test_full_pipeline_with_mocked_renderer(self, wired):
        orchestrator, plan, tasks, world_state = wired
        fake_frames = [Path("/tmp/fake_frame.png")]
        mock_renderer = MagicMock()
        mock_renderer.render_shot.return_value = fake_frames
        orchestrator.godot_renderer = mock_renderer

        all_but_validate = [t for t in tasks if t.task_type != "validate"]
        orchestrator.load_task_schedule(all_but_validate)
        success = orchestrator.run_pipeline()

        assert success
        for shot in plan.shots:
            assert orchestrator._rendered_frames[shot.shot_id] == fake_frames
            assert world_state.get_shot(shot.shot_id)["status"] == "rendered"

    def test_render_passes_duration_from_world_state(self, wired):
        orchestrator, plan, tasks, world_state = wired
        mock_renderer = MagicMock()
        mock_renderer.render_shot.return_value = [Path("/tmp/f.png")]
        orchestrator.godot_renderer = mock_renderer

        all_but_validate = [t for t in tasks if t.task_type != "validate"]
        orchestrator.load_task_schedule(all_but_validate)
        orchestrator.run_pipeline()

        # DummyShotPlanTextProvider sets duration_seconds=4.0 on every shot.
        for call in mock_renderer.render_shot.call_args_list:
            assert call.kwargs["duration_seconds"] == 4.0


class TestValidationSkipping:
    def test_validation_skipped_by_default(self, wired):
        orchestrator, plan, tasks, world_state = wired
        mock_renderer = MagicMock()
        mock_renderer.render_shot.return_value = [Path("/tmp/f.png")]
        orchestrator.godot_renderer = mock_renderer

        orchestrator.load_task_schedule(tasks)  # includes validate tasks
        success = orchestrator.run_pipeline()

        assert success
        orchestrator.validator_mgr.validate_frame.assert_not_called()
        for shot in plan.shots:
            assert world_state.get_shot(shot.shot_id)["status"] == "rendered_unvalidated"

    def test_validation_not_skipped_when_configured(self, wired):
        orchestrator, plan, tasks, world_state = wired
        orchestrator.skip_validation = False
        mock_renderer = MagicMock()
        mock_renderer.render_shot.return_value = [Path("/tmp/f.png")]
        orchestrator.godot_renderer = mock_renderer

        orchestrator.load_task_schedule(tasks)
        orchestrator.run_pipeline()

        assert orchestrator.validator_mgr.validate_frame.called


class TestProgressHooks:
    def test_on_progress_called_for_every_task(self, wired):
        orchestrator, plan, tasks, world_state = wired
        asset_tasks = [t for t in tasks if t.task_type == "generate_asset"]
        calls = []
        orchestrator.on_progress = lambda i, total: calls.append((i, total))

        orchestrator.load_task_schedule(asset_tasks)
        orchestrator.run_pipeline()

        assert calls == [(i + 1, len(asset_tasks)) for i in range(len(asset_tasks))]

    def test_on_shot_update_called_on_failure(self, wired):
        orchestrator, plan, tasks, world_state = wired
        compose_tasks = [t for t in tasks if t.task_type == "compose_scene"]
        updates = []
        orchestrator.on_shot_update = lambda shot_id, status, details: updates.append((shot_id, status))

        orchestrator.load_task_schedule(compose_tasks)
        orchestrator.run_pipeline()  # will fail — no assets generated

        assert all(status == "failed" for _, status in updates)


class TestCancel:
    def test_cancel_clears_tasks(self, wired):
        orchestrator, plan, tasks, world_state = wired
        orchestrator.load_task_schedule(tasks)
        orchestrator.cancel()
        with pytest.raises(OrchestratorError, match="No tasks loaded"):
            orchestrator.run_pipeline()


class TestUnknownTaskType:
    def test_unknown_task_type_is_skipped_not_fatal(self, wired):
        orchestrator, plan, tasks, world_state = wired
        bogus_task = Task(task_type="mystery_task", shot_id="shot_001", payload={})
        orchestrator.load_task_schedule([bogus_task])
        success = orchestrator.run_pipeline()
        assert success  # unknown task types are skipped, not failures


@pytest.mark.skipif(
    not os.environ.get("BLAZENG_GODOT_BINARY"),
    reason="Set BLAZENG_GODOT_BINARY to a real Godot binary path to run this integration test",
)
class TestFullPipelineRealGodotIntegration:
    """Prompt -> plan -> assets -> composed scenes -> real rendered frames,
    entirely through PipelineOrchestrator, using a real Godot binary."""

    def test_full_pipeline_produces_real_frames(self, tmp_path):
        from src.integrations.godot.renderer import GodotRenderer

        config = {
            "storage_path": str(tmp_path / "storage"),
            "godot_binary_path": os.environ["BLAZENG_GODOT_BINARY"],
        }
        world_state = WorldStateManager(config)
        text_provider = DummyShotPlanTextProvider(num_shots=2)
        image_provider = DummyImageProvider()
        director = Director(text_provider, world_state)
        asset_manager = AssetManager(config, image_provider)
        scene_composer = SceneComposer(config)
        godot_renderer = GodotRenderer(config)

        plan = director.generate_production_plan("A real end-to-end test")
        tasks = director.create_task_schedule(plan)

        orchestrator = PipelineOrchestrator(
            config, world_state, asset_manager, scene_composer,
            validator_mgr=MagicMock(), repair_engine=MagicMock(),
            godot_renderer=godot_renderer,
        )
        # Keep frame counts small so the real Godot invocation is fast.
        orchestrator.load_task_schedule(tasks)

        # Override render frame count via a thin wrapper, since Shot's
        # default duration (4s @ 24fps = 96 frames/shot) would make this
        # test slow for no extra verification value.
        original_render = godot_renderer.render_shot
        godot_renderer.render_shot = lambda scene_path, shot_id, **kw: original_render(
            scene_path, shot_id, num_frames=2
        )

        success = orchestrator.run_pipeline()

        assert success, f"Pipeline failed: {orchestrator.failures}"
        for shot in plan.shots:
            frames = orchestrator._rendered_frames[shot.shot_id]
            assert len(frames) == 2
            for f in frames:
                assert f.exists()
                assert f.stat().st_size > 100
