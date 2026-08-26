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


class DummyTextProvider(TextProvider):
    """Returns canned or lightly-templated text — no LLM call."""

    def __init__(self, canned_response: Optional[str] = None):
        self.canned_response = canned_response
        self.call_log = []  # test hook: inspect what was asked

    def generate(self, prompt: str, system_instruction: Optional[str] = None) -> str:
        self.call_log.append({"prompt": prompt, "system_instruction": system_instruction})
        if self.canned_response is not None:
            return self.canned_response
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
    """Writes a tiny valid PNG placeholder instead of calling an image model."""

    def __init__(self):
        self.call_log = []

    def generate_image(self, prompt: str, output_path: Path) -> Path:
        self.call_log.append({"prompt": prompt, "output_path": str(output_path)})
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(self._valid_placeholder_png())
        logger.debug(f"Dummy image written: {output_path}")
        return output_path

    @staticmethod
    def _valid_placeholder_png() -> bytes:
        # Minimal valid 1x1 white PNG, built at import time via zlib to
        # avoid hand-typing checksums incorrectly.
        import struct
        import zlib

        width, height = 1, 1
        raw = b"\x00\xff\xff\xff"  # filter byte + 1 white pixel (RGB)

        def chunk(tag: bytes, data: bytes) -> bytes:
            return (
                struct.pack(">I", len(data))
                + tag
                + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
            )

        sig = b"\x89PNG\r\n\x1a\n"
        ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
        idat = zlib.compress(raw)
        return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")
