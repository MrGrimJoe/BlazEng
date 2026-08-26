"""
Anthropic (Claude) provider — text and vision only.

Claude has no image-generation API, so there is no AnthropicImageProvider —
use image_provider: gemini or image_provider: openai for asset generation
even when text_provider/vision_provider are anthropic.

API patterns verified against Anthropic's official docs (Aug 2026):
  - Client: `anthropic.Anthropic(api_key=...)`
  - `client.messages.create(model=..., max_tokens=..., messages=[...])`
    (max_tokens is required on every call — no default)
  - Response text: `message.content` is a list of typed blocks; read
    `block.text` for blocks where `block.type == "text"`
  - Vision: an `{"type": "image", "source": {"type": "base64",
    "media_type": ..., "data": ...}}` content block alongside a text block
"""

import base64
import logging
from pathlib import Path
from typing import Optional

from .base import TextProvider, VisionProvider

logger = logging.getLogger(__name__)

try:
    import anthropic as anthropic_sdk
    _ANTHROPIC_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only when SDK isn't installed
    _ANTHROPIC_AVAILABLE = False


class AnthropicConfigError(Exception):
    """Raised when the Anthropic provider is misconfigured (missing key, etc.)."""


def _require_sdk() -> None:
    if not _ANTHROPIC_AVAILABLE:
        raise AnthropicConfigError("anthropic package is not installed. Run: pip install anthropic")


def _require_key(api_key: str) -> None:
    if not api_key or api_key in ("ANTHROPIC_API_KEY_HERE", ""):
        raise AnthropicConfigError("No Anthropic API key configured (config.yaml: anthropic_api_key)")


_DEFAULT_MAX_TOKENS = 2048


class AnthropicTextProvider(TextProvider):
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-5", max_tokens: int = _DEFAULT_MAX_TOKENS):
        _require_sdk()
        _require_key(api_key)
        self.client = anthropic_sdk.Anthropic(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens
        logger.info(f"AnthropicTextProvider ready (model={model})")

    def generate(self, prompt: str, system_instruction: Optional[str] = None) -> str:
        kwargs = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system_instruction:
            kwargs["system"] = system_instruction
        try:
            message = self.client.messages.create(**kwargs)
        except Exception as e:
            logger.error(f"Anthropic text generation failed: {e}")
            raise
        return _extract_text(message)


class AnthropicVisionProvider(VisionProvider):
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-5", max_tokens: int = _DEFAULT_MAX_TOKENS):
        _require_sdk()
        _require_key(api_key)
        self.client = anthropic_sdk.Anthropic(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens
        logger.info(f"AnthropicVisionProvider ready (model={model})")

    def analyze(self, image_path: Path, prompt: str) -> str:
        image_path = Path(image_path)
        if not image_path.exists():
            raise FileNotFoundError(f"Image not found: {image_path}")

        mime_type = _guess_mime_type(image_path)
        b64_data = base64.standard_b64encode(image_path.read_bytes()).decode("utf-8")

        try:
            message = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {
                            "type": "base64", "media_type": mime_type, "data": b64_data,
                        }},
                        {"type": "text", "text": prompt},
                    ],
                }],
            )
        except Exception as e:
            logger.error(f"Anthropic vision analysis failed: {e}")
            raise
        return _extract_text(message)


def _extract_text(message) -> str:
    """Concatenate all text blocks in an Anthropic Message's content list.

    content is a list of typed blocks (text, tool_use, etc.) — this skips
    non-text blocks rather than assuming content[0] is always text.
    """
    parts = [block.text for block in message.content if getattr(block, "type", None) == "text"]
    return "".join(parts)


def _guess_mime_type(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".webp": "image/webp", ".gif": "image/gif",
    }.get(suffix, "image/png")
