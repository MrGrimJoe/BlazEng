"""
OpenAI provider — text, vision, and image generation.

API patterns verified against OpenAI's official docs (Aug 2026):
  - Client: `OpenAI(api_key=...)`
  - Text/vision: `client.chat.completions.create(model=..., messages=[...])`,
    reading `response.choices[0].message.content`
  - Vision images: a `{"type": "image_url", "image_url": {"url": "data:<mime>;base64,<b64>"}}`
    content block alongside a text block
  - Image generation: `client.images.generate(model=..., prompt=..., size=...)`,
    reading `response.data[0].b64_json` (base64-decoded)
"""

import base64
import logging
from pathlib import Path
from typing import Optional

from .base import ImageProvider, TextProvider, VisionProvider

logger = logging.getLogger(__name__)

try:
    import openai as openai_sdk
    _OPENAI_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only when SDK isn't installed
    _OPENAI_AVAILABLE = False


class OpenAIConfigError(Exception):
    """Raised when the OpenAI provider is misconfigured (missing key, etc.)."""


def _require_openai() -> None:
    if not _OPENAI_AVAILABLE:
        raise OpenAIConfigError("openai package is not installed. Run: pip install openai")


def _require_key(api_key: str) -> None:
    if not api_key or api_key in ("OPENAI_API_KEY_HERE", ""):
        raise OpenAIConfigError("No OpenAI API key configured (config.yaml: openai_api_key)")


class OpenAITextProvider(TextProvider):
    def __init__(self, api_key: str, model: str = "gpt-5.5"):
        _require_openai()
        _require_key(api_key)
        self.client = openai_sdk.OpenAI(api_key=api_key)
        self.model = model
        logger.info(f"OpenAITextProvider ready (model={model})")

    def generate(self, prompt: str, system_instruction: Optional[str] = None) -> str:
        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt})
        try:
            response = self.client.chat.completions.create(model=self.model, messages=messages)
        except Exception as e:
            logger.error(f"OpenAI text generation failed: {e}")
            raise
        return response.choices[0].message.content or ""


class OpenAIVisionProvider(VisionProvider):
    def __init__(self, api_key: str, model: str = "gpt-5.5"):
        _require_openai()
        _require_key(api_key)
        self.client = openai_sdk.OpenAI(api_key=api_key)
        self.model = model
        logger.info(f"OpenAIVisionProvider ready (model={model})")

    def analyze(self, image_path: Path, prompt: str) -> str:
        image_path = Path(image_path)
        if not image_path.exists():
            raise FileNotFoundError(f"Image not found: {image_path}")

        mime_type = _guess_mime_type(image_path)
        b64_data = base64.b64encode(image_path.read_bytes()).decode("utf-8")

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {
                            "url": f"data:{mime_type};base64,{b64_data}"
                        }},
                    ],
                }],
            )
        except Exception as e:
            logger.error(f"OpenAI vision analysis failed: {e}")
            raise
        return response.choices[0].message.content or ""


class OpenAIImageProvider(ImageProvider):
    def __init__(self, api_key: str, model: str = "gpt-image-1"):
        _require_openai()
        _require_key(api_key)
        self.client = openai_sdk.OpenAI(api_key=api_key)
        self.model = model
        logger.info(f"OpenAIImageProvider ready (model={model})")

    def generate_image(self, prompt: str, output_path: Path) -> Path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            response = self.client.images.generate(model=self.model, prompt=prompt, size="1024x1024")
        except Exception as e:
            logger.error(f"OpenAI image generation failed: {e}")
            raise

        if not response.data or not response.data[0].b64_json:
            raise OpenAIConfigError(
                f"OpenAI response for model '{self.model}' contained no image data — "
                "check that the prompt wasn't filtered"
            )
        image_bytes = base64.b64decode(response.data[0].b64_json)
        output_path.write_bytes(image_bytes)
        return output_path


def _guess_mime_type(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
    }.get(suffix, "image/png")
