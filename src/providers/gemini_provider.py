"""
Gemini provider — text, vision, and image generation via google-genai.

API patterns here were verified against Google's own SDK docs and
changelog (Aug 2026), not guessed:
  - Client: `genai.Client(api_key=...)`
  - Text:   `client.models.generate_content(model=..., contents=...)`
  - Image:  same call, with `response_modalities=["IMAGE"]` in config,
            reading the result back out of `response.parts[i].inline_data`

Model names are read from config rather than hardcoded here, because
Google has deprecated/renamed Gemini models multiple times in 2026 —
see config.yaml for the current recommended defaults and links to the
changelog to check before upgrading.
"""

import logging
from pathlib import Path
from typing import Optional

from .base import ImageProvider, TextProvider, VisionProvider

logger = logging.getLogger(__name__)

try:
    from google import genai
    from google.genai import types as genai_types
    _GENAI_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only when SDK isn't installed
    _GENAI_AVAILABLE = False


class GeminiConfigError(Exception):
    """Raised when Gemini provider is misconfigured (missing key, bad model, etc.)."""


def _require_genai() -> None:
    if not _GENAI_AVAILABLE:
        raise GeminiConfigError(
            "google-genai is not installed. Run: pip install google-genai>=2.0.0"
        )


class GeminiTextProvider(TextProvider):
    """Text generation via Gemini (story planning, repair prompts)."""

    def __init__(self, api_key: str, model: str = "gemini-flash-latest"):
        _require_genai()
        if not api_key or api_key == "GEMINI_API_KEY_HERE":
            raise GeminiConfigError("No Gemini API key configured (config.yaml: gemini_api_key)")
        self.client = genai.Client(api_key=api_key)
        self.model = model
        logger.info(f"GeminiTextProvider ready (model={model})")

    def generate(self, prompt: str, system_instruction: Optional[str] = None) -> str:
        config = None
        if system_instruction:
            config = genai_types.GenerateContentConfig(system_instruction=system_instruction)
        try:
            response = self.client.models.generate_content(
                model=self.model, contents=prompt, config=config
            )
        except Exception as e:
            logger.error(f"Gemini text generation failed: {e}")
            raise
        return response.text or ""


class GeminiVisionProvider(VisionProvider):
    """Vision analysis via Gemini (frame validation)."""

    def __init__(self, api_key: str, model: str = "gemini-flash-latest"):
        _require_genai()
        if not api_key or api_key == "GEMINI_API_KEY_HERE":
            raise GeminiConfigError("No Gemini API key configured")
        self.client = genai.Client(api_key=api_key)
        self.model = model
        logger.info(f"GeminiVisionProvider ready (model={model})")

    def analyze(self, image_path: Path, prompt: str) -> str:
        image_path = Path(image_path)
        if not image_path.exists():
            raise FileNotFoundError(f"Image not found: {image_path}")

        image_bytes = image_path.read_bytes()
        mime_type = _guess_mime_type(image_path)

        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=[
                    genai_types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                    prompt,
                ],
            )
        except Exception as e:
            logger.error(f"Gemini vision analysis failed: {e}")
            raise
        return response.text or ""


class GeminiImageProvider(ImageProvider):
    """Image generation via Gemini's native image model ("Nano Banana 2").

    Note: Imagen (the older, separate image-only model line) is being
    retired by Google — this provider deliberately targets Gemini's own
    image generation instead, which also supports the conversational
    multi-image editing and character-consistency features this project
    needs for continuity across shots.
    """

    def __init__(self, api_key: str, model: str = "gemini-3.1-flash-image"):
        _require_genai()
        if not api_key or api_key == "GEMINI_API_KEY_HERE":
            raise GeminiConfigError("No Gemini API key configured")
        self.client = genai.Client(api_key=api_key)
        self.model = model
        logger.info(f"GeminiImageProvider ready (model={model})")

    def generate_image(self, prompt: str, output_path: Path) -> Path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=genai_types.GenerateContentConfig(response_modalities=["IMAGE"]),
            )
        except Exception as e:
            logger.error(f"Gemini image generation failed: {e}")
            raise

        for part in response.parts:
            if part.inline_data is not None and part.inline_data.data:
                output_path.write_bytes(part.inline_data.data)
                logger.debug(f"Image saved: {output_path}")
                return output_path

        raise GeminiConfigError(
            f"Gemini response for model '{self.model}' contained no (non-empty) "
            "image data — check that the model supports image output and the "
            "prompt wasn't filtered"
        )


def _guess_mime_type(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(suffix, "image/png")
