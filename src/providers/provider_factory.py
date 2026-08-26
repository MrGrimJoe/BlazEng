"""
Provider factory — wires config.yaml choices to concrete provider instances.

Three independent slots (text/vision/image) each resolve separately, so
you can mix e.g. Claude for text, Gemini for vision, and OpenAI for image
generation. This is the single place that needs to change when a new
provider is added.

Supported providers per slot:
  text/vision: gemini, openai, anthropic, huggingface, ollama, dummy
  image:       gemini, openai, diffusers, dummy
  (Anthropic has no image-generation API, so it's not an image option.)

token_prompt_fn (optional, on every get_*_provider call): only used by
huggingface/diffusers, only invoked if a repo turns out to be gated and
no token was configured — see hf_common.py. Pass your own to show a UI
dialog instead of the default CLI prompt.
"""

import logging
from typing import Any, Callable, Dict, Optional, Tuple

from .base import ImageProvider, TextProvider, VisionProvider

logger = logging.getLogger(__name__)

_TEXT_PROVIDERS = {"gemini", "openai", "anthropic", "huggingface", "ollama", "dummy"}
_VISION_PROVIDERS = {"gemini", "openai", "anthropic", "huggingface", "ollama", "dummy"}
_IMAGE_PROVIDERS = {"gemini", "openai", "diffusers", "dummy"}

_KEY_CONFIG_FIELD = {
    "gemini": "gemini_api_key",
    "openai": "openai_api_key",
    "anthropic": "anthropic_api_key",
}


class ProviderConfigError(Exception):
    """Raised when config.yaml specifies an invalid or incomplete provider setup."""


def validate_provider_config(config: Dict[str, Any]) -> Tuple[bool, str]:
    """Check that config names known providers and has the keys they need.

    Returns (is_valid, message). Collects every problem found rather than
    stopping at the first. Does not attempt to construct providers or make
    network calls, and does NOT flag huggingface/diffusers missing a token
    as an error — those are allowed to be ungated, and the token-prompt
    fallback (hf_common.py) handles the gated case at load time instead.
    """
    text_p = config.get("text_provider", "gemini")
    vision_p = config.get("vision_provider", "gemini")
    image_p = config.get("image_provider", "gemini")

    problems = []

    if text_p not in _TEXT_PROVIDERS:
        problems.append(f"Unknown text_provider '{text_p}' (expected one of {sorted(_TEXT_PROVIDERS)})")
    if vision_p not in _VISION_PROVIDERS:
        problems.append(f"Unknown vision_provider '{vision_p}' (expected one of {sorted(_VISION_PROVIDERS)})")
    if image_p not in _IMAGE_PROVIDERS:
        problems.append(f"Unknown image_provider '{image_p}' (expected one of {sorted(_IMAGE_PROVIDERS)})")

    for provider_name in (text_p, vision_p, image_p):
        key_field = _KEY_CONFIG_FIELD.get(provider_name)
        if key_field is None:
            continue
        key = config.get(key_field, "")
        if not key or key.endswith("_HERE"):
            problems.append(f"{key_field} is not set in config.yaml")

    if text_p == "huggingface" and not config.get("hf_repo_id"):
        problems.append("text_provider is 'huggingface' but hf_repo_id is empty")
    if image_p == "diffusers" and not config.get("hf_image_repo_id"):
        problems.append("image_provider is 'diffusers' but hf_image_repo_id is empty")

    if problems:
        return False, "; ".join(problems)
    return True, "Configuration valid"


def get_text_provider(
    config: Dict[str, Any], token_prompt_fn: Optional[Callable[[str], str]] = None
) -> TextProvider:
    provider = config.get("text_provider", "gemini")

    if provider == "gemini":
        from .gemini_provider import GeminiTextProvider

        return GeminiTextProvider(
            api_key=config.get("gemini_api_key", ""),
            model=config.get("gemini_model", "gemini-flash-latest"),
        )
    elif provider == "openai":
        from .openai_provider import OpenAITextProvider

        return OpenAITextProvider(
            api_key=config.get("openai_api_key", ""),
            model=config.get("openai_model", "gpt-5.5"),
        )
    elif provider == "anthropic":
        from .anthropic_provider import AnthropicTextProvider

        return AnthropicTextProvider(
            api_key=config.get("anthropic_api_key", ""),
            model=config.get("anthropic_model", "claude-sonnet-4-5"),
        )
    elif provider == "huggingface":
        from .huggingface_provider import HuggingFaceTextProvider

        return HuggingFaceTextProvider(
            repo_id=config.get("hf_repo_id", ""),
            device=config.get("hf_device", "auto"),
            max_new_tokens=config.get("hf_max_new_tokens", 1024),
            hf_token=config.get("hf_token") or None,
            token_prompt_fn=token_prompt_fn,
        )
    elif provider == "ollama":
        from .ollama_provider import OllamaTextProvider

        return OllamaTextProvider(
            model=config.get("ollama_model", "llama3"),
            url=config.get("ollama_url", "http://localhost:11434"),
        )
    elif provider == "dummy":
        from .dummy_provider import DummyTextProvider

        return DummyTextProvider()
    else:
        raise ProviderConfigError(f"Unknown text_provider: {provider}")


def get_vision_provider(
    config: Dict[str, Any], token_prompt_fn: Optional[Callable[[str], str]] = None
) -> VisionProvider:
    provider = config.get("vision_provider", "gemini")

    if provider == "gemini":
        from .gemini_provider import GeminiVisionProvider

        key = config.get("vision_gemini_api_key") or config.get("gemini_api_key", "")
        return GeminiVisionProvider(
            api_key=key,
            model=config.get("gemini_model", "gemini-flash-latest"),
        )
    elif provider == "openai":
        from .openai_provider import OpenAIVisionProvider

        key = config.get("vision_openai_api_key") or config.get("openai_api_key", "")
        return OpenAIVisionProvider(
            api_key=key,
            model=config.get("openai_model", "gpt-5.5"),
        )
    elif provider == "anthropic":
        from .anthropic_provider import AnthropicVisionProvider

        key = config.get("vision_anthropic_api_key") or config.get("anthropic_api_key", "")
        return AnthropicVisionProvider(
            api_key=key,
            model=config.get("anthropic_model", "claude-sonnet-4-5"),
        )
    elif provider == "huggingface":
        from .huggingface_provider import HuggingFaceVisionProvider

        return HuggingFaceVisionProvider(
            repo_id=config.get("hf_repo_id", ""),
            device=config.get("hf_device", "auto"),
            hf_token=config.get("hf_token") or None,
            token_prompt_fn=token_prompt_fn,
        )
    elif provider == "ollama":
        from .ollama_provider import OllamaVisionProvider

        return OllamaVisionProvider(
            model=config.get("ollama_model", "llama3"),
            url=config.get("ollama_url", "http://localhost:11434"),
        )
    elif provider == "dummy":
        from .dummy_provider import DummyVisionProvider

        return DummyVisionProvider()
    else:
        raise ProviderConfigError(f"Unknown vision_provider: {provider}")


def get_image_provider(
    config: Dict[str, Any], token_prompt_fn: Optional[Callable[[str], str]] = None
) -> ImageProvider:
    provider = config.get("image_provider", "gemini")

    if provider == "gemini":
        from .gemini_provider import GeminiImageProvider

        key = config.get("image_api_key") or config.get("gemini_api_key", "")
        return GeminiImageProvider(
            api_key=key,
            model=config.get("gemini_image_model", "gemini-3.1-flash-image"),
        )
    elif provider == "openai":
        from .openai_provider import OpenAIImageProvider

        key = config.get("image_openai_api_key") or config.get("openai_api_key", "")
        return OpenAIImageProvider(
            api_key=key,
            model=config.get("openai_image_model", "gpt-image-1"),
        )
    elif provider == "diffusers":
        from .diffusers_provider import DiffusersImageProvider

        return DiffusersImageProvider(
            repo_id=config.get("hf_image_repo_id", ""),
            device=config.get("hf_device", "auto"),
            hf_token=config.get("hf_token") or None,
            token_prompt_fn=token_prompt_fn,
        )
    elif provider == "dummy":
        from .dummy_provider import DummyImageProvider

        return DummyImageProvider()
    else:
        raise ProviderConfigError(f"Unknown image_provider: {provider}")
