"""
Dummy providers — deterministic, offline stand-ins for testing.

No network calls, no API keys. Used by the test suite and by anyone
who wants to exercise the pipeline's control flow without burning API
quota. Swap in via config: text_provider: dummy / vision_provider: dummy
/ image_provider: dummy.
"""

import json
import logging
from pathlib import Path
from typing import Optional

from .base import ImageProvider, TextProvider, VisionProvider

logger = logging.getLogger(__name__)


def _dummy_plan_json(prompt: str, num_shots: int = 3) -> str:
    shots = [
        {
            "shot_id": f"shot_{i:03d}",
            "scene_description": f"Dummy scene {i} derived from: {prompt[:60]}",
            "characters": ["protagonist"],
            "camera_angle": ["wide shot", "medium shot", "close-up"][(i - 1) % 3],
            "lighting": "natural daylight",
            "action": f"Action beat {i}",
            "dialogue": [{"character": "protagonist", "line": f"This is line number {i}."}],
            "duration_seconds": 4.0,
        }
        for i in range(1, num_shots + 1)
    ]
    return json.dumps({
        "shots": shots,
        "world_state_seed": {"protagonist": {"appearance": "unspecified"}},
    })


class DummyTextProvider(TextProvider):
    """Returns canned or lightly-templated text — no LLM call.

    When asked for a shot plan (the Director's system prompt asks for JSON
    with a "shots" array) and no canned response was set, it returns a valid
    three-shot plan, so ``text_provider: dummy`` runs the whole pipeline
    offline instead of failing on unparseable output.
    """

    def __init__(self, canned_response: Optional[str] = None):
        self.canned_response = canned_response
        self.call_log = []  # test hook: inspect what was asked

    def generate(self, prompt: str, system_instruction: Optional[str] = None) -> str:
        self.call_log.append({"prompt": prompt, "system_instruction": system_instruction})
        if self.canned_response is not None:
            return self.canned_response
        if system_instruction and '"shots"' in system_instruction:
            return _dummy_plan_json(prompt)
        return f"[DUMMY RESPONSE TO {len(prompt)}-char prompt]"


class DummyShotPlanTextProvider(TextProvider):
    """A DummyTextProvider that returns a valid Director shot-plan JSON.

    Useful for testing Director without a real LLM: it fabricates a
    plausible plan whose shot count scales lightly with prompt length,
    so tests can assert on structure without needing real story logic.
    """

    def __init__(self, num_shots: int = 3):
        self.num_shots = num_shots
        self.call_log = []

    def generate(self, prompt: str, system_instruction: Optional[str] = None) -> str:
        self.call_log.append({"prompt": prompt, "system_instruction": system_instruction})
        shots = []
        for i in range(1, self.num_shots + 1):
            shots.append(
                {
                    "shot_id": f"shot_{i:03d}",
                    "scene_description": f"Dummy scene {i} derived from: {prompt[:60]}",
                    "characters": ["protagonist"],
                    "camera_angle": "medium shot",
                    "lighting": "natural daylight",
                    "action": f"Action beat {i}",
                    "duration_seconds": 4.0,
                }
            )
        plan = {
            "shots": shots,
            "world_state_seed": {"protagonist": {"appearance": "unspecified"}},
        }
        return json.dumps(plan)


class DummyVisionProvider(VisionProvider):
    """Returns a canned analysis without looking at the image."""

    def __init__(self, canned_response: str = "PASS: looks fine"):
        self.canned_response = canned_response
        self.call_log = []

    def analyze(self, image_path: Path, prompt: str) -> str:
        self.call_log.append({"image_path": str(image_path), "prompt": prompt})
        return self.canned_response


class DummyImageProvider(ImageProvider):
    """Writes a simple coloured character silhouette instead of calling an image model.

    The colours are derived from the prompt, so different characters look
    different in offline runs. Pure standard library (zlib/struct): no Pillow
    needed. Output is a transparent-background RGBA PNG, which is what the
    renderers expect for character cut-outs.
    """

    WIDTH, HEIGHT = 128, 256

    def __init__(self):
        self.call_log = []

    def generate_image(
        self, prompt: str, output_path: Path, reference_image: Optional[Path] = None
    ) -> Path:
        self.call_log.append({
            "prompt": prompt, "output_path": str(output_path),
            "reference_image": str(reference_image) if reference_image else None,
        })
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if reference_image is not None and Path(reference_image).is_file():
            # "Image-conditioned" generation, offline: keep the same character.
            output_path.write_bytes(Path(reference_image).read_bytes())
        else:
            output_path.write_bytes(self._silhouette_png(prompt))
        logger.debug(f"Dummy image written: {output_path}")
        return output_path

    @classmethod
    def _silhouette_png(cls, prompt: str) -> bytes:
        import hashlib
        import struct
        import zlib

        w, h = cls.WIDTH, cls.HEIGHT
        digest = hashlib.sha256(prompt.encode("utf-8")).digest()
        skin = (200 + digest[0] % 40, 150 + digest[1] % 50, 110 + digest[2] % 50, 255)
        shirt = (40 + digest[3] % 180, 40 + digest[4] % 180, 40 + digest[5] % 180, 255)
        trousers = (30 + digest[6] % 90, 30 + digest[7] % 90, 50 + digest[8] % 120, 255)
        clear = (0, 0, 0, 0)

        def pixel(x: int, y: int):
            # head: circle; torso and legs: rectangles
            cx, cy, r = w // 2, int(h * 0.14), int(h * 0.10)
            if (x - cx) ** 2 + (y - cy) ** 2 <= r * r:
                return skin
            if int(h * 0.26) <= y < int(h * 0.62) and int(w * 0.22) <= x < int(w * 0.78):
                return shirt
            if int(h * 0.62) <= y < int(h * 0.98) and int(w * 0.30) <= x < int(w * 0.70):
                return trousers
            return clear

        raw = bytearray()
        for y in range(h):
            raw.append(0)  # PNG filter type 0 for this scanline
            for x in range(w):
                raw.extend(pixel(x, y))

        def chunk(tag: bytes, data: bytes) -> bytes:
            return (
                struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
            )

        ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)  # 8-bit RGBA
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
                + chunk(b"IDAT", zlib.compress(bytes(raw))) + chunk(b"IEND", b""))
