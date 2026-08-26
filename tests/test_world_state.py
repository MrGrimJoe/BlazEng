import pytest
import sqlite3

from src.core.world_state.world_state import WorldStateManager, WorldStateError


@pytest.fixture
def world_state(tmp_path):
    return WorldStateManager({"storage_path": str(tmp_path / "storage")})


def test_add_and_get_character(world_state):
    world_state.add_character("detective", {"appearance": "tall, brown fedora"})
    char = world_state.get_character("detective")
    assert char is not None
    assert char["appearance"] == "tall, brown fedora"
    assert char["version"] == 1


def test_get_unknown_character_returns_none(world_state):
    assert world_state.get_character("nobody") is None


def test_add_character_twice_bumps_version(world_state):
    world_state.add_character("detective", {"appearance": "v1 look"})
    world_state.add_character("detective", {"appearance": "v2 look"})
    char = world_state.get_character("detective")
    assert char["appearance"] == "v2 look"
    assert char["version"] == 2


def test_update_character_requires_existing_character(world_state):
    with pytest.raises(WorldStateError):
        world_state.update_character("ghost", {"injuries": "none"})


def test_update_character_records_latest_state(world_state):
    world_state.add_character("detective", {"appearance": "healthy"})
    world_state.update_character("detective", {"injuries": "bullet wound, left arm"})
    char = world_state.get_character("detective")
    assert char["injuries"] == "bullet wound, left arm"


def test_update_character_state_history_returns_latest_only(world_state):
    world_state.add_character("detective", {"appearance": "healthy"})
    world_state.update_character("detective", {"clothing": "trench coat"})
    world_state.update_character("detective", {"clothing": "torn trench coat"})
    char = world_state.get_character("detective")
    assert char["clothing"] == "torn trench coat"


def test_add_character_extra_metadata_preserved(world_state):
    world_state.add_character("detective", {"appearance": "tall", "height": "6ft"})
    char = world_state.get_character("detective")
    assert char["height"] == "6ft"


def test_list_characters(world_state):
    world_state.add_character("alice", {"appearance": "..."})
    world_state.add_character("bob", {"appearance": "..."})
    assert world_state.list_characters() == ["alice", "bob"]


def test_add_and_get_object(world_state):
    world_state.add_object("brass_key", {"description": "ornate brass key"})
    obj = world_state.get_object("brass_key")
    assert obj["description"] == "ornate brass key"


def test_events_scoped_to_shot(world_state):
    world_state.add_event("Detective enters warehouse", shot_id="shot_001")
    world_state.add_event("Detective finds clue", shot_id="shot_002")
    world_state.add_event("General world event")

    shot_1_events = world_state.get_events(shot_id="shot_001")
    assert len(shot_1_events) == 1
    assert shot_1_events[0]["description"] == "Detective enters warehouse"

    all_events = world_state.get_events()
    assert len(all_events) == 3


def test_add_and_get_shot(world_state):
    world_state.add_shot("shot_001", {"scene_description": "A dark warehouse"})
    shot = world_state.get_shot("shot_001")
    assert shot["scene_description"] == "A dark warehouse"
    assert shot["status"] == "pending"


def test_set_shot_status(world_state):
    world_state.add_shot("shot_001", {})
    world_state.set_shot_status("shot_001", "rendered")
    shot = world_state.get_shot("shot_001")
    assert shot["status"] == "rendered"


def test_list_shots(world_state):
    world_state.add_shot("shot_002", {})
    world_state.add_shot("shot_001", {})
    shots = world_state.list_shots()
    assert [s["shot_id"] for s in shots] == ["shot_001", "shot_002"]


def test_persists_across_reopen(tmp_path):
    storage = str(tmp_path / "storage")
    ws1 = WorldStateManager({"storage_path": storage})
    ws1.add_character("detective", {"appearance": "tall"})
    ws1.close()

    ws2 = WorldStateManager({"storage_path": storage})
    char = ws2.get_character("detective")
    assert char is not None
    assert char["appearance"] == "tall"


def test_context_manager_closes_connection(tmp_path):
    storage = str(tmp_path / "storage")
    with WorldStateManager({"storage_path": storage}) as ws:
        ws.add_character("detective", {"appearance": "tall"})
    # Connection should be closed; further use should raise.
    with pytest.raises(Exception):
        ws.get_character("detective")


def test_failed_write_rolls_back_rather_than_partially_committing(world_state):
    """_cursor() should roll back on any exception raised inside the
    `with` block, not leave a half-applied write committed. Verified by
    forcing a failure partway through a multi-statement operation."""
    world_state.add_character("detective", {"appearance": "original"})

    with pytest.raises(sqlite3.IntegrityError):
        with world_state._cursor() as cur:
            cur.execute(
                "UPDATE characters SET appearance = ? WHERE name = ?",
                ("should not stick", "detective"),
            )
            # Force a genuine constraint violation to trigger rollback:
            # character_states.character_name has a REFERENCES constraint,
            # so pointing it at a nonexistent character fails here.
            cur.execute(
                "INSERT INTO character_states (character_name) VALUES (?)",
                ("nobody_registered",),
            )

    # The UPDATE earlier in the same transaction must NOT have stuck,
    # proving _cursor() actually rolled back rather than partially committing.
    char = world_state.get_character("detective")
    assert char["appearance"] == "original"
