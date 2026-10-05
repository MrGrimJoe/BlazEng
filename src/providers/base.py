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
    def generate_image(
        self, prompt: str, output_path: Path, reference_image: Optional[Path] = None
    ) -> Path:
        """Generate an image and save it to output_path. Returns the path.

        ``reference_image`` is an earlier image of the same subject. Providers
        that support image-conditioned generation use it to keep the subject
        recognisably the same (character consistency); providers that don't
        must accept and ignore it.
        """


class SpeechProvider(ABC):
    """Turns a line of dialogue into a spoken audio file (text-to-speech)."""

    @abstractmethod
    def synthesize(self, text: str, output_path: Path, voice: Optional[str] = None) -> Path:
        """Speak ``text`` into a WAV file at output_path. Returns the path."""

    def voices(self) -> "list[str]":
        """Voice names this provider can use (empty = single/default voice)."""
        return []

    # character name (lower-case) -> voice, set from ``voices:`` in config.yaml
    assignments: "dict[str, str]" = {}

    def voice_for(self, character: str) -> Optional[str]:
        """The voice for a character: an explicit assignment, else a stable pick from the pool."""
        key = character.strip().lower()
        if key in self.assignments:
            return self.assignments[key]
        pool = self.voices()
        if not pool:
            return None
        import hashlib

        return pool[int(hashlib.sha256(key.encode()).hexdigest(), 16) % len(pool)]
