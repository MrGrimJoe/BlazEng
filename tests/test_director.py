import json

import pytest

from src.core.director.director import Director, DirectorError, _extract_json
from src.core.world_state.world_state import WorldStateManager
from src.providers.dummy_provider import DummyShotPlanTextProvider, DummyTextProvider


@pytest.fixture
def world_state(tmp_path):
    return WorldStateManager({"storage_path": str(tmp_path / "storage")})


class TestExtractJson:
    def test_pure_json(self):
        assert _extract_json('{"a": 1}') == {"a": 1}

    def test_json_in_markdown_fence(self):
        text = '```json\n{"a": 1}\n```'
        assert _extract_json(text) == {"a": 1}

    def test_json_with_surrounding_prose(self):
        text = 'Sure, here is the plan:\n{"a": 1}\nLet me know if you need changes!'
        assert _extract_json(text) == {"a": 1}

    def test_invalid_json_returns_none(self):
        assert _extract_json("not json at all") is None

    def test_empty_string_returns_none(self):
        assert _extract_json("") is None

    def test_non_dict_json_returns_none(self):
        assert _extract_json("[1, 2, 3]") is None

    # --- Adversarial cases: realistic messy LLM output, not clean examples ---

    def test_trailing_comma_in_object_recovered(self):
        text = '{"shots": [{"shot_id": "shot_001"},], "world_state_seed": {},}'
        result = _extract_json(text)
        assert result is not None
        assert result["shots"][0]["shot_id"] == "shot_001"

    def test_multiple_fenced_blocks_scratch_then_real(self):
        # Plausible "let me think" response: a scratch/example block first,
        # then the actual answer in a second fence.
        text = (
            "Let me sketch the format first:\n"
            "```json\n{\"example\": true}\n```\n\n"
            "Here's the actual plan:\n"
            "```json\n{\"shots\": [{\"shot_id\": \"shot_001\"}]}\n```"
        )
        result = _extract_json(text)
        assert result is not None
        assert "shots" in result

    def test_prose_with_stray_braces_does_not_break_extraction(self):
        # Prose describing a schema with braces, before the real fenced JSON.
        text = (
            "I'll use the schema {shot_id, description} for each shot.\n"
            "```json\n{\"shots\": [{\"shot_id\": \"shot_001\", \"scene_description\": \"x\"}]}\n```"
        )
        result = _extract_json(text)
        assert result is not None
        assert result["shots"][0]["shot_id"] == "shot_001"

    def test_truncated_json_stays_unparseable(self):
        # A response cut off mid-generation (e.g. hit max_tokens) has no
        # recoverable structure — should correctly fail, not guess.
        text = '{"shots": [{"shot_id": "shot_001", "scene_description": "A detective enters the war'
        assert _extract_json(text) is None

    def test_top_level_list_instead_of_dict_fails_honestly(self, world_state):
        # A model that forgets to wrap shots in {"shots": [...]} and returns
        # a bare array. The brace-scan fallback may pull out a nested
        # object rather than returning None outright — but the pipeline
        # must still fail cleanly (no "shots" key) rather than silently
        # treating the extracted fragment as a valid plan.
        provider = DummyTextProvider(canned_response='[{"shot_id": "shot_001"}]')
        director = Director(provider, world_state)
        with pytest.raises(DirectorError):
            director.generate_production_plan("test")


class TestCharactersFieldCoercion:
    """Real LLM failure mode: giving a bare string instead of a
    single-element list when there's only one character. list("detective")
    would silently explode this into individual letters — must not."""

    def test_bare_string_characters_becomes_single_item_list(self, world_state):
        response = json.dumps({
            "shots": [{"scene_description": "x", "characters": "detective"}]
        })
        provider = DummyTextProvider(canned_response=response)
        director = Director(provider, world_state)
        plan = director.generate_production_plan("test")

        assert plan.shots[0].characters == ["detective"]

    def test_empty_string_characters_becomes_empty_list(self, world_state):
        response = json.dumps({
            "shots": [{"scene_description": "x", "characters": ""}]
        })
        provider = DummyTextProvider(canned_response=response)
        director = Director(provider, world_state)
        plan = director.generate_production_plan("test")

        assert plan.shots[0].characters == []

    def test_normal_list_characters_still_works(self, world_state):
        response = json.dumps({
            "shots": [{"scene_description": "x", "characters": ["a", "b"]}]
        })
        provider = DummyTextProvider(canned_response=response)
        director = Director(provider, world_state)
        plan = director.generate_production_plan("test")

        assert plan.shots[0].characters == ["a", "b"]


class TestGenerateProductionPlan:
    def test_empty_prompt_raises(self, world_state):
        director = Director(DummyTextProvider(), world_state)
        with pytest.raises(DirectorError):
            director.generate_production_plan("")

    def test_valid_plan_parses(self, world_state):
        provider = DummyShotPlanTextProvider(num_shots=5)
        director = Director(provider, world_state)
        plan = director.generate_production_plan("A detective story")

        assert len(plan.shots) == 5
        assert all(s.shot_id for s in plan.shots)
        assert plan.shots[0].characters == ["protagonist"]

    def test_plan_seeds_world_state(self, world_state):
        provider = DummyShotPlanTextProvider(num_shots=2)
        director = Director(provider, world_state)
        director.generate_production_plan("A detective story")

        assert "protagonist" in world_state.list_characters()
        shots = world_state.list_shots()
        assert len(shots) == 2

    def test_unparseable_response_raises_after_retry(self, world_state):
        provider = DummyTextProvider(canned_response="I cannot help with that.")
        director = Director(provider, world_state)
        with pytest.raises(DirectorError):
            director.generate_production_plan("A detective story")
        # Confirms it actually retried once (2 calls) rather than
        # giving up after a single failed parse.
        assert len(provider.call_log) == 2

    def test_empty_shots_list_raises(self, world_state):
        provider = DummyTextProvider(canned_response=json.dumps({"shots": []}))
        director = Director(provider, world_state)
        with pytest.raises(DirectorError):
            director.generate_production_plan("A detective story")

    def test_duplicate_shot_ids_are_deduplicated(self, world_state):
        response = json.dumps({
            "shots": [
                {"shot_id": "shot_001", "scene_description": "first"},
                {"shot_id": "shot_001", "scene_description": "second"},
            ]
        })
        provider = DummyTextProvider(canned_response=response)
        director = Director(provider, world_state)
        plan = director.generate_production_plan("test")

        ids = [s.shot_id for s in plan.shots]
        assert len(ids) == len(set(ids)), f"Expected unique shot IDs, got {ids}"

    def test_missing_optional_fields_get_defaults(self, world_state):
        response = json.dumps({
            "shots": [{"scene_description": "bare minimum shot"}]
        })
        provider = DummyTextProvider(canned_response=response)
        director = Director(provider, world_state)
        plan = director.generate_production_plan("test")

        shot = plan.shots[0]
        assert shot.camera_angle == "medium shot"
        assert shot.lighting == "natural daylight"
        assert shot.duration_seconds == 4.0
        assert shot.shot_id  # auto-generated, non-empty


class TestRepairShot:
    def test_repair_unknown_shot_raises(self, world_state):
        director = Director(DummyTextProvider(), world_state)
        with pytest.raises(DirectorError):
            director.repair_shot("nonexistent_shot", "too dark")

    def test_repair_updates_description(self, world_state):
        world_state.add_shot("shot_001", {
            "scene_description": "original description",
            "characters": ["detective"],
            "camera_angle": "wide shot",
            "lighting": "dim",
            "action": "walks in",
            "duration_seconds": 5.0,
        })
        provider = DummyTextProvider(
            canned_response=json.dumps({"scene_description": "revised, brighter description"})
        )
        director = Director(provider, world_state)

        revised = director.repair_shot("shot_001", "frame was too dark")

        assert revised.scene_description == "revised, brighter description"
        assert revised.shot_id == "shot_001"
        # Unrelated fields should carry over unchanged from stored shot data
        assert revised.characters == ["detective"]
        assert revised.camera_angle == "wide shot"

    def test_repair_falls_back_to_original_if_response_unparseable(self, world_state):
        world_state.add_shot("shot_001", {
            "scene_description": "original description",
        })
        provider = DummyTextProvider(canned_response="not valid json")
        director = Director(provider, world_state)

        revised = director.repair_shot("shot_001", "feedback")
        assert revised.scene_description == "original description"

    def test_repair_without_world_state_raises(self):
        director = Director(DummyTextProvider(), world_state=None)
        with pytest.raises(DirectorError):
            director.repair_shot("shot_001", "feedback")


class TestSeedWorldStateEdgeCases:
    def test_none_world_state_does_not_crash(self):
        provider = DummyShotPlanTextProvider(num_shots=2)
        director = Director(provider, world_state=None)
        # Should not raise even though there's nowhere to seed state
        plan = director.generate_production_plan("test")
        assert len(plan.shots) == 2

    def test_non_dict_world_state_seed_entries_are_skipped(self, world_state):
        response = json.dumps({
            "shots": [{"scene_description": "a shot"}],
            "world_state_seed": {"detective": "not a dict, should be skipped"},
        })
        provider = DummyTextProvider(canned_response=response)
        director = Director(provider, world_state)
        director.generate_production_plan("test")
        # Should not have crashed, and should not have added the malformed entry
        assert "detective" not in world_state.list_characters()

    def test_non_dict_world_state_seed_top_level_defaults_to_empty(self, world_state):
        response = json.dumps({
            "shots": [{"scene_description": "a shot"}],
            "world_state_seed": "not a dict at all",
        })
        provider = DummyTextProvider(canned_response=response)
        director = Director(provider, world_state)
        plan = director.generate_production_plan("test")
        assert plan.world_state_seed == {}


class TestCreateTaskSchedule:
    def test_task_schedule_has_all_phases(self, world_state):
        provider = DummyShotPlanTextProvider(num_shots=3)
        director = Director(provider, world_state)
        plan = director.generate_production_plan("test")
        tasks = director.create_task_schedule(plan)

        task_types = {t.task_type for t in tasks}
        assert task_types == {"generate_asset", "compose_scene", "render", "validate"}

    def test_asset_generation_deduplicated_across_shots(self, world_state):
        # All 3 dummy shots share the same "protagonist" character —
        # should get exactly one generate_asset task, not three.
        provider = DummyShotPlanTextProvider(num_shots=3)
        director = Director(provider, world_state)
        plan = director.generate_production_plan("test")
        tasks = director.create_task_schedule(plan)

        asset_tasks = [t for t in tasks if t.task_type == "generate_asset"]
        assert len(asset_tasks) == 1

    def test_compose_render_validate_counts_match_shot_count(self, world_state):
        provider = DummyShotPlanTextProvider(num_shots=4)
        director = Director(provider, world_state)
        plan = director.generate_production_plan("test")
        tasks = director.create_task_schedule(plan)

        for task_type in ("compose_scene", "render", "validate"):
            matching = [t for t in tasks if t.task_type == task_type]
            assert len(matching) == 4, f"{task_type} count mismatch"
