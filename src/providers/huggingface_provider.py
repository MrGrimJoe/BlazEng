"""
HuggingFace local-model provider — text and vision via `transformers`.

Models are loaded lazily on first actual `generate()`/`analyze()` call,
not at construction — constructing a provider should never trigger a
multi-GB download, matching how the Gemini providers don't make a
network call just to build a client.

If the requested repo is gated/private and no token is available, this
does NOT fail outright: it prompts for a Hugging Face token (via an
injectable token_prompt_fn — defaults to a CLI prompt, but a UI caller
can pass a dialog-based one instead) and retries once. See hf_common.py.

Known limitation: this environment's network policy blocks
huggingface.co, so — like the Gemini live-API gap — this has been
implemented against the documented transformers/huggingface_hub API
surface and unit-tested with those calls mocked, but never exercised
against a real model download. Verify with a real repo before relying
on it in production.
"""

import logging
from pathlib import Path
from typing import Optional

from .base import TextProvider, VisionProvider
from .hf_common import HFAuthRequiredError, is_gated_repo_error, load_with_token_fallback

logger = logging.getLogger(__name__)

try:
    from transformers import pipeline as _hf_pipeline
    _TRANSFORMERS_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only when transformers isn't installed
    _TRANSFORMERS_AVAILABLE = False


class HuggingFaceConfigError(Exception):
    """Raised when the HuggingFace provider is misconfigured (no repo_id, etc.)."""


def _require_transformers() -> None:
    if not _TRANSFORMERS_AVAILABLE:
        raise HuggingFaceConfigError(
            "transformers is not installed. Run: pip install transformers torch accelerate"
        )


class HuggingFaceTextProvider(TextProvider):
    def __init__(
        self,
        repo_id: str,
        device: str = "auto",
        max_new_tokens: int = 1024,
        hf_token: Optional[str] = None,
        token_prompt_fn=None,
    ):
        if not repo_id:
            raise HuggingFaceConfigError("text_provider is 'huggingface' but hf_repo_id is empty")
        self.repo_id = repo_id
        self.device = device
        self.max_new_tokens = max_new_tokens
        self.hf_token = hf_token
        self.token_prompt_fn = token_prompt_fn
        self._pipe = None
        logger.info(f"HuggingFaceTextProvider configured (repo={repo_id}, not yet loaded)")

    def _load(self, token: Optional[str]) -> None:
        _require_transformers()
        try:
            self._pipe = _hf_pipeline(
                "text-generation", model=self.repo_id, token=token, device_map=self.device
            )
        except Exception as e:
            if is_gated_repo_error(e):
                raise HFAuthRequiredError(self.repo_id) from e
            raise

    def _ensure_loaded(self) -> None:
        if self._pipe is not None:
            return
        self.hf_token = load_with_token_fallback(
            self._load, self.repo_id, self.hf_token, self.token_prompt_fn
        )

    def generate(self, prompt: str, system_instruction: Optional[str] = None) -> str:
        self._ensure_loaded()
        full_prompt = f"{system_instruction}\n\n{prompt}" if system_instruction else prompt
        try:
            result = self._pipe(full_prompt, max_new_tokens=self.max_new_tokens, do_sample=True)
        except Exception as e:
            logger.error(f"HuggingFace text generation failed: {e}")
            raise
        text = result[0]["generated_text"]
        # Most text-generation pipelines return prompt+completion concatenated —
        # strip the echoed prompt so callers only get the new text, matching
        # what GeminiTextProvider.generate() returns.
        if text.startswith(full_prompt):
            text = text[len(full_prompt):]
        return text.strip()


class HuggingFaceVisionProvider(VisionProvider):
    def __init__(
        self,
        repo_id: str,
        device: str = "auto",
        hf_token: Optional[str] = None,
        token_prompt_fn=None,
    ):
        if not repo_id:
            raise HuggingFaceConfigError("vision_provider is 'huggingface' but hf_repo_id is empty")
        self.repo_id = repo_id
        self.device = device
        self.hf_token = hf_token
        self.token_prompt_fn = token_prompt_fn
        self._pipe = None
        logger.info(f"HuggingFaceVisionProvider configured (repo={repo_id}, not yet loaded)")

    def _load(self, token: Optional[str]) -> None:
        _require_transformers()
        try:
            self._pipe = _hf_pipeline(
                "visual-question-answering", model=self.repo_id, token=token, device_map=self.device
            )
        except Exception as e:
            if is_gated_repo_error(e):
                raise HFAuthRequiredError(self.repo_id) from e
            raise

    def _ensure_loaded(self) -> None:
        if self._pipe is not None:
            return
        self.hf_token = load_with_token_fallback(
            self._load, self.repo_id, self.hf_token, self.token_prompt_fn
        )

    def analyze(self, image_path: Path, prompt: str) -> str:
        image_path = Path(image_path)
        if not image_path.exists():
            raise FileNotFoundError(f"Image not found: {image_path}")
        self._ensure_loaded()
        try:
            result = self._pipe(image=str(image_path), question=prompt)
        except Exception as e:
            logger.error(f"HuggingFace vision analysis failed: {e}")
            raise
        # visual-question-answering pipeline returns a list of
        # {"answer": ..., "score": ...} dicts, ranked by confidence.
        if isinstance(result, list) and result:
            return str(result[0].get("answer", ""))
        return str(result)
