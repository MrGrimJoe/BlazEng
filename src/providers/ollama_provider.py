"""
Ollama local-model provider — NOT YET IMPLEMENTED.

Placeholder so `text_provider: ollama` / `vision_provider: ollama` fail
clearly instead of with a confusing import error. See CONTRIBUTING.md —
Priority 2 item.
"""

from pathlib import Path
from typing import Optional

from .base import TextProvider, VisionProvider


class OllamaTextProvider(TextProvider):
    def __init__(self, model: str = "llama3", url: str = "http://localhost:11434"):
        raise NotImplementedError(
            "Ollama text provider is not yet implemented. "
            "See CONTRIBUTING.md for how to add it, or use text_provider: gemini "
            "(cloud) or text_provider: dummy (offline testing) in the meantime."
        )

    def generate(self, prompt: str, system_instruction: Optional[str] = None) -> str:
        raise NotImplementedError


class OllamaVisionProvider(VisionProvider):
    def __init__(self, model: str = "llama3", url: str = "http://localhost:11434"):
        raise NotImplementedError(
            "Ollama vision provider is not yet implemented. "
            "See CONTRIBUTING.md for how to add it, or use vision_provider: gemini "
            "(cloud) or vision_provider: dummy (offline testing) in the meantime."
        )

    def analyze(self, image_path: Path, prompt: str) -> str:
        raise NotImplementedError
