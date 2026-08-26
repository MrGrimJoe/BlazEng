"""
Director — turns a user's story prompt into a structured shot plan.

Prompts the text provider for JSON directly (rather than free text +
regex parsing), then validates and repairs the structure defensively,
since LLM JSON output is reliably *almost* valid, not reliably valid.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.providers.base import TextProvider

logger = logging.getLogger(__name__)


class DirectorError(Exception):
    """Raised when a prompt can't be turned into a usable shot plan."""


@dataclass
class Shot:
    shot_id: str
    scene_description: str
    characters: List[str] = field(default_factory=list)
    camera_angle: str = "medium shot"
    lighting: str = "natural daylight"
    action: str = ""
    duration_seconds: float = 4.0

    @classmethod
    def from_dict(cls, d: Dict[str, Any], index: int) -> "Shot":
        return cls(
            shot_id=str(d.get("shot_id") or f"shot_{index:03d}"),
            scene_description=str(d.get("scene_description", "")).strip(),
            characters=_coerce_str_list(d.get("characters")),
            camera_angle=str(d.get("camera_angle") or "medium shot"),
            lighting=str(d.get("lighting") or "natural daylight"),
            action=str(d.get("action", "")),
            duration_seconds=_coerce_float(d.get("duration_seconds"), default=4.0),
        )


@dataclass
class ProductionPlan:
    prompt: str
    shots: List[Shot]
    world_state_seed: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Task:
    """A single unit of pipeline work, in dependency order."""
    task_type: str        # "generate_asset" | "compose_scene" | "render" | "validate"
    shot_id: str
    payload: Dict[str, Any] = field(default_factory=dict)


_PLAN_SYSTEM_PROMPT = """You are a film director breaking a story prompt into shots \
for an automated video production pipeline. Respond with ONLY valid JSON — no \
markdown fences, no commentary — matching exactly this schema:

{
  "shots": [
    {
      "shot_id": "shot_001",
      "scene_description": "one or two sentences describing what's visible",
      "characters": ["character_name", ...],
      "camera_angle": "wide shot | medium shot | close-up | overhead | etc",
      "lighting": "brief lighting description",
      "action": "what happens during this shot",
      "duration_seconds": 4.0
    }
  ],
  "world_state_seed": {
    "character_name": {"appearance": "brief physical description"}
  }
}

Break the story into 5-15 shots depending on complexity. Every character \
referenced in a shot's "characters" list must have an entry in world_state_seed. \
Keep scene_description concrete and visual — describe what a camera would see, \
not internal thoughts or backstory."""


class Director:
    """Parses prompts into shot plans and builds task schedules for them."""

    def __init__(self, text_provider: TextProvider, world_state):
        self.text_provider = text_provider
        self.world_state = world_state

    def generate_production_plan(self, prompt: str) -> ProductionPlan:
        """Turn a story prompt into a validated ProductionPlan.

        Raises DirectorError if the prompt is empty or the LLM's response
        can't be salvaged into a usable plan after retrying once.
        """
        if not prompt or not prompt.strip():
            raise DirectorError("Cannot plan an empty prompt")

        raw = self.text_provider.generate(prompt, system_instruction=_PLAN_SYSTEM_PROMPT)
        data = _extract_json(raw)

        if data is None:
            # One retry with an explicit correction nudge — LLMs commonly
            # wrap JSON in prose or code fences on the first attempt.
            logger.warning("First plan response wasn't parseable JSON, retrying")
            retry_prompt = (
                f"{prompt}\n\nYour previous response could not be parsed as JSON. "
                "Respond with ONLY the JSON object, nothing else."
            )
            raw = self.text_provider.generate(retry_prompt, system_instruction=_PLAN_SYSTEM_PROMPT)
            data = _extract_json(raw)

        if data is None:
            raise DirectorError(
                "Text provider did not return parseable JSON after retry. "
                f"Last response (truncated): {raw[:200]!r}"
            )

        return self._build_plan(prompt, data)

    def _build_plan(self, prompt: str, data: Dict[str, Any]) -> ProductionPlan:
        raw_shots = data.get("shots")
        if not isinstance(raw_shots, list) or not raw_shots:
            raise DirectorError("Plan JSON has no non-empty 'shots' list")

        shots = [Shot.from_dict(s, i) for i, s in enumerate(raw_shots, start=1)]

        # De-duplicate shot_ids defensively — an LLM repeating "shot_001"
        # twice would silently corrupt downstream world-state tracking.
        seen_ids = set()
        for i, shot in enumerate(shots, start=1):
            if shot.shot_id in seen_ids:
                shot.shot_id = f"{shot.shot_id}_{i}"
            seen_ids.add(shot.shot_id)

        world_state_seed = data.get("world_state_seed") or {}
        if not isinstance(world_state_seed, dict):
            world_state_seed = {}

        plan = ProductionPlan(prompt=prompt, shots=shots, world_state_seed=world_state_seed)
        self._seed_world_state(plan)
        return plan

    def _seed_world_state(self, plan: ProductionPlan) -> None:
        if self.world_state is None:
            return
        for name, metadata in plan.world_state_seed.items():
            if isinstance(metadata, dict):
                self.world_state.add_character(name, metadata)
        for shot in plan.shots:
            self.world_state.add_shot(shot.shot_id, {
                "scene_description": shot.scene_description,
                "characters": shot.characters,
                "camera_angle": shot.camera_angle,
                "lighting": shot.lighting,
                "action": shot.action,
                "duration_seconds": shot.duration_seconds,
            })

    def create_task_schedule(self, plan: ProductionPlan) -> List[Task]:
        """Build a dependency-ordered task list: assets -> compose -> render -> validate.

        Asset generation tasks are deduplicated per character/object name so
        a character appearing in 10 shots gets one generation task, not ten.
        """
        tasks: List[Task] = []
        seen_assets = set()

        for shot in plan.shots:
            for character in shot.characters:
                if character not in seen_assets:
                    tasks.append(Task("generate_asset", shot.shot_id, {
                        "asset_type": "character", "name": character,
                    }))
                    seen_assets.add(character)

        for shot in plan.shots:
            tasks.append(Task("compose_scene", shot.shot_id, {"shot": shot}))
        for shot in plan.shots:
            tasks.append(Task("render", shot.shot_id, {}))
        for shot in plan.shots:
            tasks.append(Task("validate", shot.shot_id, {}))

        return tasks

    def repair_shot(self, shot_id: str, feedback: str) -> Shot:
        """Re-prompt for a single shot's description based on validator feedback."""
        shot_data = self.world_state.get_shot(shot_id) if self.world_state else None
        if shot_data is None:
            raise DirectorError(f"Cannot repair unknown shot: {shot_id}")

        repair_prompt = (
            f"A rendered frame for this shot failed validation.\n"
            f"Original scene description: {shot_data.get('scene_description', '')}\n"
            f"Validator feedback: {feedback}\n\n"
            "Respond with ONLY a JSON object with one key, \"scene_description\", "
            "containing a revised description that addresses the feedback."
        )
        raw = self.text_provider.generate(repair_prompt)
        data = _extract_json(raw) or {}
        new_description = data.get("scene_description") or shot_data.get("scene_description", "")

        return Shot(
            shot_id=shot_id,
            scene_description=new_description,
            characters=shot_data.get("characters", []),
            camera_angle=shot_data.get("camera_angle", "medium shot"),
            lighting=shot_data.get("lighting", "natural daylight"),
            action=shot_data.get("action", ""),
            duration_seconds=shot_data.get("duration_seconds", 4.0),
        )


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Best-effort extraction of a JSON object from an LLM response.

    Tries, in order: every fenced code block (not just the first — a
    "let me think" response sometimes has a scratch block before the real
    one), the full trimmed text, and a brace-scanned span as a last
    resort. Each candidate is also retried with trailing commas stripped,
    since that's a common small LLM formatting slip that would otherwise
    force an expensive full retry for a single stray character.
    """
    if not text:
        return None

    candidates = []
    fence_matches = list(_JSON_FENCE_RE.finditer(text))
    for fence_match in reversed(fence_matches):
        candidates.append(fence_match.group(1).strip())
    candidates.append(text.strip())

    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start != -1 and brace_end != -1 and brace_end > brace_start:
        candidates.append(text[brace_start:brace_end + 1])

    for candidate in candidates:
        result = _try_parse_json_object(candidate)
        if result is not None:
            return result
    return None


_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


def _try_parse_json_object(candidate: str) -> Optional[Dict[str, Any]]:
    for text in (candidate, _TRAILING_COMMA_RE.sub(r"\1", candidate)):
        try:
            result = json.loads(text)
            if isinstance(result, dict):
                return result
        except (json.JSONDecodeError, ValueError):
            continue
    return None


def _coerce_str_list(value: Any) -> List[str]:
    """Coerce a JSON field expected to be a list-of-strings.

    Guards against a real LLM failure mode: giving a bare string
    ("detective") instead of a single-element list (["detective"]) when
    there's only one item. `list("detective")` would silently explode
    that into individual letters — this treats a bare string as one item
    instead.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
