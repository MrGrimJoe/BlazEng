"""Abstract provider interfaces for text, vision, and image generation.

Every concrete provider (Gemini, HuggingFace, Ollama, Dummy) implements
one or more of these so the rest of the pipeline never has to know which
backend it's talking to.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional


class TextProvider(ABC):
    """Generates text completions for story planning and repair prompts."""

    @abstractmethod
    def generate(self, prompt: str, system_instruction: Optional[str] = None) -> str:
        """Return a text completion for prompt."""


class VisionProvider(ABC):
    """Answers questions about an image (frame validation)."""

    @abstractmethod
    def analyze(self, image_path: Path, prompt: str) -> str:
        """Return a text answer describing/evaluating the given image."""


class ImageProvider(ABC):
    """Generates an image from a text description."""

    @abstractmethod
    def generate_image(self, prompt: str, output_path: Path) -> Path:
        """Generate an image and save it to output_path. Returns the path."""
