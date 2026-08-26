"""
End-to-end integration test: prompt -> plan -> tasks -> generated assets.

Uses dummy providers throughout so it runs offline and deterministically,
but exercises the real Director, WorldStateManager, and AssetManager
wired together exactly as main.py wires them — this is the test that
would have caught the AttributeError/interface mismatches unit tests
in isolation cannot.
"""

from src.core.asset_manager.asset_manager import AssetManager
from src.core.director.director import Director
from src.core.world_state.world_state import WorldStateManager
from src.providers.dummy_provider import DummyImageProvider, DummyShotPlanTextProvider


def test_full_prompt_to_assets_pipeline(tmp_path):
    config = {"storage_path": str(tmp_path / "storage")}

    world_state = WorldStateManager(config)
    text_provider = DummyShotPlanTextProvider(num_shots=4)
    image_provider = DummyImageProvider()
    director = Director(text_provider, world_state)
    asset_manager = AssetManager(config, image_provider)

    # 1. Prompt -> plan
    plan = director.generate_production_plan(
        "A detective investigates a warehouse in 1940s rain"
    )
    assert len(plan.shots) == 4

    # 2. Plan -> task schedule
    tasks = director.create_task_schedule(plan)
    asset_tasks = [t for t in tasks if t.task_type == "generate_asset"]
    assert len(asset_tasks) >= 1

    # 3. Execute asset generation tasks against AssetManager
    generated_paths = {}
    for task in asset_tasks:
        name = task.payload["name"]
        character = world_state.get_character(name)
        assert character is not None, f"Director should have seeded '{name}' into world state"
        description = character.get("appearance", "a character")
        path = asset_manager.get_asset("character", name, description)
        generated_paths[name] = path
        assert path.exists()

    # 4. Re-requesting the same asset should hit cache, not regenerate
    for task in asset_tasks:
        name = task.payload["name"]
        character = world_state.get_character(name)
        description = character.get("appearance", "a character")
        path_again = asset_manager.get_asset("character", name, description)
        assert path_again == generated_paths[name]

    # 5. Shot metadata should be queryable after the full run
    for shot in plan.shots:
        stored = world_state.get_shot(shot.shot_id)
        assert stored is not None
        assert stored["scene_description"] == shot.scene_description

    world_state.close()


def test_pipeline_survives_shot_repair_cycle(tmp_path):
    """Simulates a validation failure and repair without a real renderer."""
    config = {"storage_path": str(tmp_path / "storage")}
    world_state = WorldStateManager(config)
    text_provider = DummyShotPlanTextProvider(num_shots=2)
    director = Director(text_provider, world_state)

    plan = director.generate_production_plan("A quiet morning in a cafe")
    failing_shot = plan.shots[0]

    # Simulate a validator rejecting the shot and Director repairing it.
    from src.providers.dummy_provider import DummyTextProvider
    import json

    repair_provider = DummyTextProvider(
        canned_response=json.dumps({"scene_description": "brighter, warmer cafe interior"})
    )
    director.text_provider = repair_provider  # swap provider for the repair call

    revised_shot = director.repair_shot(failing_shot.shot_id, "too dark and cold-toned")
    assert revised_shot.scene_description == "brighter, warmer cafe interior"
    assert revised_shot.shot_id == failing_shot.shot_id

    world_state.close()
