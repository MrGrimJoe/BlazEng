"""
Shared Hugging Face Hub authentication handling.

Both HuggingFaceTextProvider/VisionProvider and DiffusersImageProvider hit
the same failure mode — a repo that's gated or private and needs a token —
so the detection and the "ask for a token, only when needed" retry flow
live here once instead of being duplicated.
"""

import getpass
import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class HFAuthRequiredError(Exception):
    """Raised when a Hugging Face repo needs authentication we don't have."""

    def __init__(self, repo_id: str):
        self.repo_id = repo_id
        super().__init__(
            f"Repo '{repo_id}' is gated or private and requires a Hugging Face "
            "access token to download."
        )


def is_gated_repo_error(exc: Exception) -> bool:
    """Detect a gated/private-repo auth failure across huggingface_hub versions.

    Prefers the real exception class when available (huggingface_hub has
    moved this between `.utils.GatedRepoError` and `.errors.GatedRepoError`
    across versions — try both), and falls back to matching the wording
    transformers/huggingface_hub actually use in their own error messages
    when the exception has been wrapped in a plain OSError along the way
    (verified against real error text reported in HF's own issue tracker).
    """
    try:
        from huggingface_hub.utils import GatedRepoError as _GatedUtils
        if isinstance(exc, _GatedUtils):
            return True
    except ImportError:
        pass
    try:
        from huggingface_hub.errors import GatedRepoError as _GatedErrors
        if isinstance(exc, _GatedErrors):
            return True
    except ImportError:
        pass

    msg = str(exc).lower()
    return (
        "gated repo" in msg
        or ("restricted" in msg and "authenticat" in msg)
        or ("you must be authenticated" in msg)
        or ("401" in msg and "huggingface" in msg)
    )


def default_token_prompt(repo_id: str) -> str:
    """CLI fallback for asking the user for an HF token when one is needed.

    UI callers (e.g. the Qt app) should pass their own token_prompt_fn
    instead — a dialog box, not a terminal prompt — this is only the
    sane default for scripts/tests.
    """
    print(f"\n'{repo_id}' requires a Hugging Face access token to download.")
    print("Create one (read access is enough) at: https://huggingface.co/settings/tokens")
    try:
        return getpass.getpass("Enter your Hugging Face token (input hidden): ").strip()
    except Exception:
        return input("Enter your Hugging Face token: ").strip()


def load_with_token_fallback(
    load_fn: Callable[[Optional[str]], None],
    repo_id: str,
    initial_token: Optional[str],
    token_prompt_fn: Optional[Callable[[str], str]],
) -> Optional[str]:
    """Call load_fn(token), retrying once with a prompted token on auth failure.

    load_fn should attempt the real load and raise HFAuthRequiredError (via
    is_gated_repo_error) if it fails for auth reasons. Returns the token
    that ultimately worked (None if no token was ever needed), so the
    caller can remember it for subsequent loads in the same provider
    instance instead of prompting again.

    Only prompts when the load actually fails for an auth reason — a repo
    that doesn't need a token never triggers the prompt. If a token was
    already supplied and still fails, that failure is raised as-is rather
    than looping forever.
    """
    try:
        load_fn(initial_token)
        return initial_token
    except HFAuthRequiredError:
        if initial_token:
            raise  # already tried with a token; don't loop
        logger.info(f"'{repo_id}' requires authentication — prompting for a token")
        prompt_fn = token_prompt_fn or default_token_prompt
        token = prompt_fn(repo_id)
        if not token:
            raise
        load_fn(token)  # let this raise plainly if it still fails with a token
        return token
