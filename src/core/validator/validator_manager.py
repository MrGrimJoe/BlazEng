"""
ValidatorManager — four independent checks on a rendered frame.

Each validator asks the vision provider a single, narrowly-scoped
question and parses a PASS/FAIL verdict plus a short reason out of the
response. LLM output is reliably *almost* structured, not reliably
structured, so parsing is defensive (see `_parse_verdict`) the same way
Director defensively parses JSON — an ambiguous response fails closed
(treated as FAIL) rather than silently passing, since a false failure
just costs an extra repair attempt while a false pass ships a broken
frame unnoticed.

TemporalValidator is a partial exception: with no previous shot to
compare against (the first shot in a production), there's nothing to be
inconsistent with, so it passes trivially rather than failing on an
input it can't evaluate.
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from src.providers.base import TextProvider, VisionProvider

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    validator_name: str
    passed: bool
    feedback: str


@dataclass
class FrameValidationReport:
    shot_id: str
    results: List[ValidationResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def failure_feedback(self) -> str:
        """Combined feedback from every failed validator, for RepairEngine."""
        failures = [r for r in self.results if not r.passed]
        if not failures:
            return ""
        return " | ".join(f"{r.validator_name}: {r.feedback}" for r in failures)


_SCRIPT_PROMPT_TEMPLATE = (
    "You are validating a rendered video frame against its intended script.\n"
    "Intended scene: {scene_description}\n"
    "Intended action: {action}\n\n"
    "Does this frame plausibly depict that scene? Respond with PASS or FAIL "
    "on the first line, then one short sentence explaining why."
)

_VISUAL_PROMPT = (
    "Rate the visual quality of this rendered frame. Look for glaring "
    "issues: completely blank/black frame, obviously broken rendering, "
    "missing content. Minor stylization is fine. Respond with PASS or FAIL "
    "on the first line, then one short sentence explaining why."
)

_CHARACTER_PROMPT_TEMPLATE = (
    "This frame should show the following character(s): {characters}\n"
    "Expected appearance notes: {appearance_notes}\n\n"
    "Does the frame show characters consistent with these descriptions? "
    "Respond with PASS or FAIL on the first line, then one short sentence "
    "explaining why."
)

_TEMPORAL_PROMPT = (
    "These are two consecutive frames from a video, in order. Does the "
    "second frame follow plausibly from the first (consistent lighting, "
    "positioning, and setting — not necessarily identical, just not "
    "jarringly discontinuous)? Respond with PASS or FAIL on the first "
    "line, then one short sentence explaining why."
)


class ValidatorManager:
    """Runs script, visual, character, and temporal validators on a frame."""

    def __init__(self, text_provider: TextProvider, vision_provider: VisionProvider):
        self.text_provider = text_provider
        self.vision_provider = vision_provider
        logger.info("ValidatorManager ready")

    def validate_frame(
        self,
        frame_path: Path,
        shot,
        world_state,
        previous_frame_path: Optional[Path] = None,
    ) -> FrameValidationReport:
        """Run all four validators against `frame_path` and return a report.

        `shot` is a Director.Shot (or duck-typed equivalent) with
        scene_description/action/characters. `world_state` supplies
        character appearance notes for CharacterValidator, and may be
        None (that validator degrades to a no-op pass if so, since there's
        nothing to check consistency against).
        """
        results = [
            self._run_script_validator(frame_path, shot),
            self._run_visual_validator(frame_path),
            self._run_character_validator(frame_path, shot, world_state),
            self._run_temporal_validator(frame_path, previous_frame_path),
        ]
        return FrameValidationReport(shot_id=shot.shot_id, results=results)

    # ------------------------------------------------------------------
    # Individual validators
    # ------------------------------------------------------------------

    def _run_script_validator(self, frame_path: Path, shot) -> ValidationResult:
        prompt = _SCRIPT_PROMPT_TEMPLATE.format(
            scene_description=shot.scene_description or "(none given)",
            action=shot.action or "(none given)",
        )
        return self._ask_vision("ScriptValidator", frame_path, prompt)

    def _run_visual_validator(self, frame_path: Path) -> ValidationResult:
        return self._ask_vision("VisualValidator", frame_path, _VISUAL_PROMPT)

    def _run_character_validator(self, frame_path: Path, shot, world_state) -> ValidationResult:
        characters = list(getattr(shot, "characters", []) or [])
        if not characters:
            return ValidationResult("CharacterValidator", True, "No characters in this shot")

        if world_state is None:
            return ValidationResult(
                "CharacterValidator", True, "No world state available to check against"
            )

        notes = []
        for name in characters:
            char = world_state.get_character(name)
            if char:
                notes.append(f"{name}: {char.get('appearance', 'unspecified')}")
            else:
                notes.append(f"{name}: (no appearance on file)")

        prompt = _CHARACTER_PROMPT_TEMPLATE.format(
            characters=", ".join(characters),
            appearance_notes="; ".join(notes),
        )
        return self._ask_vision("CharacterValidator", frame_path, prompt)

    def _run_temporal_validator(
        self, frame_path: Path, previous_frame_path: Optional[Path]
    ) -> ValidationResult:
        if previous_frame_path is None:
            return ValidationResult(
                "TemporalValidator", True, "No previous frame to compare (first shot)"
            )
        if not Path(previous_frame_path).exists():
            return ValidationResult(
                "TemporalValidator", True, "Previous frame file missing — skipping comparison"
            )

        # Vision providers in this codebase take a single image path — for
        # a two-frame comparison we describe both frames' context in the
        # prompt and analyze the current frame, noting the limitation
        # rather than silently pretending to compare pixels we never sent.
        prompt = (
            f"{_TEMPORAL_PROMPT}\n\n"
            f"(Note: only the second/current frame is attached for this check; "
            f"evaluate it for anything that looks like a jarring discontinuity "
            f"on its own, such as impossible lighting or an empty/corrupt frame.)"
        )
        return self._ask_vision("TemporalValidator", frame_path, prompt)

    # ------------------------------------------------------------------
    # Shared vision-call + parsing logic
    # ------------------------------------------------------------------

    def _ask_vision(self, validator_name: str, frame_path: Path, prompt: str) -> ValidationResult:
        try:
            response = self.vision_provider.analyze(Path(frame_path), prompt)
        except Exception as e:
            logger.error(f"{validator_name} vision call failed: {e}")
            return ValidationResult(validator_name, False, f"Vision provider error: {e}")

        passed, feedback = _parse_verdict(response)
        return ValidationResult(validator_name, passed, feedback)


_VERDICT_RE = re.compile(r"(PASS|FAIL)\b", re.IGNORECASE)
_LEADING_NOISE_RE = re.compile(
    r"^[\s*_#>-]*"                              # markdown emphasis/quote/bullet chars
    r"(verdict|answer|result|assessment)?\s*:?\s*",  # optional label prefix
    re.IGNORECASE,
)


def _parse_verdict(response: str) -> "tuple[bool, str]":
    """Extract a PASS/FAIL verdict and reason from a vision response.

    Only looks at the first non-empty line, and only for a PASS/FAIL
    token near its start — deliberately does NOT scan the whole response,
    since a nuanced explanation can contain both words ("this would pass
    if not for the lighting, so: FAIL") and scanning the full text risks
    matching the wrong one.

    Tolerates markdown noise a real model plausibly adds around the
    verdict (**PASS**, "Verdict: FAIL", "- PASS") by stripping it before
    matching. Still fails closed — see module docstring — if no token is
    found near the start of the first line even after cleanup.
    """
    if not response:
        return False, "Empty response from vision provider"

    first_line = response.strip().splitlines()[0] if response.strip() else ""
    cleaned = _LEADING_NOISE_RE.sub("", first_line)
    match = _VERDICT_RE.match(cleaned.strip())

    if match is None:
        truncated = response.strip()[:150]
        return False, f"Could not parse a PASS/FAIL verdict from response: {truncated!r}"

    verdict = match.group(1).upper()
    reason = cleaned.strip()[match.end():].strip(" \n.-:*")
    if not reason:
        # Reason may be on a later line if the verdict was alone on the first.
        rest = response.strip().splitlines()[1:]
        reason = " ".join(l.strip() for l in rest if l.strip()) or "(no reason given)"
    return verdict == "PASS", reason
