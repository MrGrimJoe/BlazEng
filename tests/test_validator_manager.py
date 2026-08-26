import pytest

from src.core.director.director import Shot
from src.core.validator.validator_manager import (
    FrameValidationReport,
    ValidationResult,
    ValidatorManager,
    _parse_verdict,
)
from src.core.world_state.world_state import WorldStateManager
from src.providers.dummy_provider import DummyTextProvider, DummyVisionProvider


@pytest.fixture
def world_state(tmp_path):
    return WorldStateManager({"storage_path": str(tmp_path / "storage")})


@pytest.fixture
def frame_path(tmp_path):
    """A real, valid PNG — validators pass it straight to vision_provider.analyze,
    which for DummyVisionProvider doesn't inspect it, but a real path avoids
    masking a bug where code accidentally requires a real file to exist."""
    path = tmp_path / "frame.png"
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0"
        b"\x00\x00\x03\x01\x01\x00\x18\xdd\x8d\xb0\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    return path


class TestParseVerdict:
    def test_pass_uppercase(self):
        passed, reason = _parse_verdict("PASS: looks fine")
        assert passed is True
        assert "looks fine" in reason

    def test_fail_uppercase(self):
        passed, reason = _parse_verdict("FAIL: frame is blank")
        assert passed is False
        assert "frame is blank" in reason

    def test_case_insensitive(self):
        passed, _ = _parse_verdict("pass, all good")
        assert passed is True

    def test_pass_on_own_line_with_reason_below(self):
        passed, reason = _parse_verdict("PASS\nThe character matches the description.")
        assert passed is True
        assert "character matches" in reason

    def test_empty_response_fails_closed(self):
        passed, reason = _parse_verdict("")
        assert passed is False
        assert "Empty response" in reason

    def test_ambiguous_response_fails_closed(self):
        passed, reason = _parse_verdict("I'm not sure, it's kind of unclear.")
        assert passed is False
        assert "Could not parse" in reason

    def test_no_reason_gets_placeholder(self):
        passed, reason = _parse_verdict("PASS")
        assert passed is True
        assert reason == "(no reason given)"

    # --- Adversarial cases: realistic messy model formatting ---

    def test_markdown_bold_verdict(self):
        passed, reason = _parse_verdict("**PASS** - the frame looks correct")
        assert passed is True
        assert "looks correct" in reason

    def test_label_prefixed_verdict(self):
        passed, reason = _parse_verdict("Verdict: FAIL\nThe lighting is wrong.")
        assert passed is False
        assert "lighting is wrong" in reason

    def test_bullet_prefixed_verdict(self):
        passed, reason = _parse_verdict("- PASS: matches the description")
        assert passed is True

    def test_answer_label_prefix(self):
        passed, reason = _parse_verdict("Answer: PASS")
        assert passed is True

    def test_verdict_mentioned_later_in_prose_still_fails_closed(self):
        # A nuanced explanation that uses both words — must NOT scan the
        # whole response for a match, since that risks picking the wrong
        # one. Only the first line is examined, and this doesn't start
        # with a clean verdict token, so it should fail closed.
        passed, reason = _parse_verdict(
            "This would pass on lighting alone, but the character's coat "
            "is the wrong color, so overall: FAIL"
        )
        assert passed is False

    def test_verdict_alone_on_first_line_reason_on_next(self):
        passed, reason = _parse_verdict("PASS\nEverything checks out fine.")
        assert passed is True
        assert "checks out fine" in reason


class TestFrameValidationReport:
    def test_passed_true_when_all_results_pass(self):
        report = FrameValidationReport(shot_id="shot_001", results=[
            ValidationResult("A", True, "ok"),
            ValidationResult("B", True, "ok"),
        ])
        assert report.passed is True

    def test_passed_false_if_any_result_fails(self):
        report = FrameValidationReport(shot_id="shot_001", results=[
            ValidationResult("A", True, "ok"),
            ValidationResult("B", False, "bad lighting"),
        ])
        assert report.passed is False

    def test_failure_feedback_combines_only_failures(self):
        report = FrameValidationReport(shot_id="shot_001", results=[
            ValidationResult("A", True, "ok"),
            ValidationResult("B", False, "bad lighting"),
            ValidationResult("C", False, "wrong character"),
        ])
        feedback = report.failure_feedback
        assert "bad lighting" in feedback
        assert "wrong character" in feedback
        assert "A: ok" not in feedback

    def test_failure_feedback_empty_when_all_pass(self):
        report = FrameValidationReport(shot_id="shot_001", results=[
            ValidationResult("A", True, "ok"),
        ])
        assert report.failure_feedback == ""


class TestValidateFrameIntegration:
    def test_all_pass_when_vision_provider_says_pass(self, frame_path, world_state):
        world_state.add_character("detective", {"appearance": "tall, fedora"})
        manager = ValidatorManager(
            DummyTextProvider(),
            DummyVisionProvider(canned_response="PASS: looks correct"),
        )
        shot = Shot(
            shot_id="shot_001", scene_description="a detective", action="walking",
            characters=["detective"],
        )
        report = manager.validate_frame(frame_path, shot, world_state)

        assert report.passed is True
        assert len(report.results) == 4
        names = {r.validator_name for r in report.results}
        assert names == {"ScriptValidator", "VisualValidator", "CharacterValidator", "TemporalValidator"}

    def test_all_fail_when_vision_provider_says_fail(self, frame_path, world_state):
        manager = ValidatorManager(
            DummyTextProvider(),
            DummyVisionProvider(canned_response="FAIL: completely wrong"),
        )
        shot = Shot(shot_id="shot_001", scene_description="x", characters=["a"])
        world_state.add_character("a", {"appearance": "x"})
        report = manager.validate_frame(frame_path, shot, world_state)

        assert report.passed is False
        assert "completely wrong" in report.failure_feedback

    def test_character_validator_passes_trivially_with_no_characters(self, frame_path, world_state):
        manager = ValidatorManager(
            DummyTextProvider(), DummyVisionProvider(canned_response="FAIL: bad")
        )
        shot = Shot(shot_id="shot_001", scene_description="empty room", characters=[])
        report = manager.validate_frame(frame_path, shot, world_state)

        char_result = next(r for r in report.results if r.validator_name == "CharacterValidator")
        assert char_result.passed is True
        assert "No characters" in char_result.feedback

    def test_character_validator_passes_trivially_with_no_world_state(self, frame_path):
        manager = ValidatorManager(
            DummyTextProvider(), DummyVisionProvider(canned_response="FAIL: bad")
        )
        shot = Shot(shot_id="shot_001", scene_description="x", characters=["detective"])
        report = manager.validate_frame(frame_path, shot, world_state=None)

        char_result = next(r for r in report.results if r.validator_name == "CharacterValidator")
        assert char_result.passed is True
        assert "No world state" in char_result.feedback

    def test_temporal_validator_passes_trivially_for_first_shot(self, frame_path, world_state):
        manager = ValidatorManager(
            DummyTextProvider(), DummyVisionProvider(canned_response="FAIL: bad")
        )
        shot = Shot(shot_id="shot_001", scene_description="x", characters=[])
        report = manager.validate_frame(frame_path, shot, world_state, previous_frame_path=None)

        temporal_result = next(r for r in report.results if r.validator_name == "TemporalValidator")
        assert temporal_result.passed is True
        assert "No previous frame" in temporal_result.feedback

    def test_temporal_validator_runs_when_previous_frame_given(self, frame_path, world_state, tmp_path):
        prev_frame = tmp_path / "prev.png"
        prev_frame.write_bytes(frame_path.read_bytes())

        manager = ValidatorManager(
            DummyTextProvider(), DummyVisionProvider(canned_response="PASS: consistent")
        )
        shot = Shot(shot_id="shot_002", scene_description="x", characters=[])
        report = manager.validate_frame(frame_path, shot, world_state, previous_frame_path=prev_frame)

        temporal_result = next(r for r in report.results if r.validator_name == "TemporalValidator")
        assert temporal_result.passed is True
        assert "consistent" in temporal_result.feedback

    def test_missing_previous_frame_file_passes_trivially(self, frame_path, world_state, tmp_path):
        manager = ValidatorManager(
            DummyTextProvider(), DummyVisionProvider(canned_response="FAIL: bad")
        )
        shot = Shot(shot_id="shot_002", scene_description="x", characters=[])
        report = manager.validate_frame(
            frame_path, shot, world_state, previous_frame_path=tmp_path / "does_not_exist.png"
        )
        temporal_result = next(r for r in report.results if r.validator_name == "TemporalValidator")
        assert temporal_result.passed is True

    def test_vision_provider_exception_fails_closed(self, frame_path, world_state):
        class BrokenVisionProvider:
            def analyze(self, image_path, prompt):
                raise RuntimeError("API quota exceeded")

        manager = ValidatorManager(DummyTextProvider(), BrokenVisionProvider())
        shot = Shot(shot_id="shot_001", scene_description="x", characters=[])
        report = manager.validate_frame(frame_path, shot, world_state)

        assert report.passed is False
        script_result = next(r for r in report.results if r.validator_name == "ScriptValidator")
        assert "API quota exceeded" in script_result.feedback

    def test_character_validator_includes_appearance_notes_in_prompt(self, frame_path, world_state):
        world_state.add_character("detective", {"appearance": "tall, brown fedora"})
        vision = DummyVisionProvider(canned_response="PASS: matches")
        manager = ValidatorManager(DummyTextProvider(), vision)
        shot = Shot(shot_id="shot_001", scene_description="x", characters=["detective"])
        manager.validate_frame(frame_path, shot, world_state)

        # The CharacterValidator call should have included the character's
        # actual appearance text in its prompt to the vision provider.
        character_calls = [c for c in vision.call_log if "detective" in c["prompt"]]
        assert any("brown fedora" in c["prompt"] for c in character_calls)

    def test_character_referenced_but_never_registered_in_world_state(self, frame_path, world_state):
        """A shot can reference a character name that was never added via
        add_character() (e.g. a Director bug, or a manually-constructed
        shot in a test). This shouldn't crash — it should note the gap in
        the prompt and let the vision model judge on the frame alone."""
        vision = DummyVisionProvider(canned_response="PASS: fine")
        manager = ValidatorManager(DummyTextProvider(), vision)
        shot = Shot(shot_id="shot_001", scene_description="x", characters=["ghost"])

        report = manager.validate_frame(frame_path, shot, world_state)

        char_result = next(r for r in report.results if r.validator_name == "CharacterValidator")
        assert char_result.passed is True  # vision provider said PASS
        character_calls = [c for c in vision.call_log if "ghost" in c["prompt"]]
        assert any("no appearance on file" in c["prompt"] for c in character_calls)
