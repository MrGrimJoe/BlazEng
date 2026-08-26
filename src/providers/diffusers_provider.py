"""
Diffusers (local Stable Diffusion) image provider.

Same lazy-load + token-fallback design as huggingface_provider.py — see
that module's docstring for why, and hf_common.py for the shared retry
logic. Same live-testing limitation applies: implemented against
diffusers' documented API, unit-tested with it mocked, never run against
a real download in this environment.
"""

import logging
from pathlib import Path
from typing import Optional

from .base import ImageProvider
from .hf_common import HFAuthRequiredError, is_gated_repo_error, load_with_token_fallback

logger = logging.getLogger(__name__)

try:
    from diffusers import AutoPipelineForText2Image as _AutoPipelineForText2Image
    _DIFFUSERS_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only when diffusers isn't installed
    _DIFFUSERS_AVAILABLE = False


class DiffusersConfigError(Exception):
    """Raised when the Diffusers provider is misconfigured (no repo_id, etc.)."""


def _require_diffusers() -> None:
    if not _DIFFUSERS_AVAILABLE:
        raise DiffusersConfigError(
            "diffusers is not installed. Run: pip install diffusers torch accelerate"
        )


class DiffusersImageProvider(ImageProvider):
    def __init__(
        self,
        repo_id: str,
        device: str = "auto",
        hf_token: Optional[str] = None,
        token_prompt_fn=None,
    ):
        if not repo_id:
            raise DiffusersConfigError("image_provider is 'diffusers' but hf_image_repo_id is empty")
        self.repo_id = repo_id
        self.device = device
        self.hf_token = hf_token
        self.token_prompt_fn = token_prompt_fn
        self._pipe = None
        logger.info(f"DiffusersImageProvider configured (repo={repo_id}, not yet loaded)")

    def _load(self, token: Optional[str]) -> None:
        _require_diffusers()
        try:
            pipe = _AutoPipelineForText2Image.from_pretrained(self.repo_id, token=token)
        except Exception as e:
            if is_gated_repo_error(e):
                raise HFAuthRequiredError(self.repo_id) from e
            raise

        device = self.device
        if device == "auto":
            try:
                import torch
                device = "cuda" if torch.cuda.is_available() else "cpu"
            except ImportError:
                device = "cpu"
        self._pipe = pipe.to(device)

    def _ensure_loaded(self) -> None:
        if self._pipe is not None:
            return
        self.hf_token = load_with_token_fallback(
            self._load, self.repo_id, self.hf_token, self.token_prompt_fn
        )

    def generate_image(self, prompt: str, output_path: Path) -> Path:
        self._ensure_loaded()
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            result = self._pipe(prompt)
        except Exception as e:
            logger.error(f"Diffusers image generation failed: {e}")
            raise
        image = result.images[0]
        image.save(output_path)
        return output_path
