"""
Tests for the "only ask for a Hugging Face token when actually needed"
flow — this is the behavior explicitly requested: don't fail outright on
a gated repo, prompt once, retry, and don't prompt at all for an ungated
repo.
"""

import pytest

from src.providers.hf_common import (
    HFAuthRequiredError,
    is_gated_repo_error,
    load_with_token_fallback,
)


class FakeGatedRepoError(Exception):
    """Stands in for huggingface_hub's real GatedRepoError in tests that
    shouldn't depend on that package's exact exception hierarchy."""


class TestIsGatedRepoError:
    def test_matches_gated_repo_wording(self):
        assert is_gated_repo_error(Exception("Cannot access gated repo for url ..."))

    def test_matches_restricted_authenticated_wording(self):
        assert is_gated_repo_error(
            Exception("Access to model foo/bar is restricted. You must be authenticated.")
        )

    def test_matches_401_huggingface_wording(self):
        assert is_gated_repo_error(Exception("401 Client Error for huggingface.co"))

    def test_unrelated_error_not_matched(self):
        assert not is_gated_repo_error(Exception("Connection timed out"))

    def test_unrelated_401_without_huggingface_not_matched(self):
        assert not is_gated_repo_error(Exception("401 Unauthorized"))

    def test_real_gated_repo_error_class_detected(self):
        # Not just the text-matching fallback — the real huggingface_hub
        # exception class should be detected directly via isinstance.
        from huggingface_hub.utils import GatedRepoError
        import requests

        fake_response = requests.Response()
        fake_response.status_code = 403
        real_error = GatedRepoError("Cannot access gated repo", response=fake_response)
        assert is_gated_repo_error(real_error)


class TestDefaultTokenPrompt:
    def test_uses_getpass_and_strips_result(self, monkeypatch, capsys):
        from src.providers import hf_common

        monkeypatch.setattr(hf_common.getpass, "getpass", lambda prompt: "  hf_realtoken  \n")
        result = hf_common.default_token_prompt("org/gated-model")

        assert result == "hf_realtoken"
        captured = capsys.readouterr()
        assert "org/gated-model" in captured.out
        assert "huggingface.co/settings/tokens" in captured.out

    def test_falls_back_to_input_if_getpass_fails(self, monkeypatch):
        from src.providers import hf_common

        def broken_getpass(prompt):
            raise RuntimeError("no tty available")

        monkeypatch.setattr(hf_common.getpass, "getpass", broken_getpass)
        monkeypatch.setattr("builtins.input", lambda prompt: "  hf_fallbacktoken  ")

        result = hf_common.default_token_prompt("org/gated-model")
        assert result == "hf_fallbacktoken"


class TestLoadWithTokenFallback:
    def test_ungated_repo_never_prompts(self):
        """The core requirement: a repo that doesn't need a token should
        never trigger the prompt at all."""
        calls = []

        def load_fn(token):
            calls.append(token)  # succeeds immediately, no error

        prompt_calls = []
        def fake_prompt(repo_id):
            prompt_calls.append(repo_id)
            return "should-never-be-used"

        result = load_with_token_fallback(load_fn, "some/ungated-model", None, fake_prompt)

        assert result is None  # no token was ever needed
        assert calls == [None]  # loaded once, with no token
        assert prompt_calls == []  # prompt was never invoked

    def test_gated_repo_with_no_token_prompts_and_retries(self):
        """The requested behavior: instead of failing outright, ask for a
        token and retry once it's provided."""
        attempts = []

        def load_fn(token):
            attempts.append(token)
            if token is None:
                raise HFAuthRequiredError("org/gated-model")
            # second attempt with a token succeeds

        def fake_prompt(repo_id):
            assert repo_id == "org/gated-model"
            return "hf_faketoken123"

        result = load_with_token_fallback(load_fn, "org/gated-model", None, fake_prompt)

        assert result == "hf_faketoken123"
        assert attempts == [None, "hf_faketoken123"]  # first failed, retried with token

    def test_already_has_token_and_still_fails_does_not_prompt_again(self):
        """If a token was already configured and it still doesn't work,
        don't loop asking for another one — raise plainly."""
        def load_fn(token):
            raise HFAuthRequiredError("org/gated-model")

        def fake_prompt(repo_id):
            pytest.fail("Should not prompt when a token was already supplied and failed")

        with pytest.raises(HFAuthRequiredError):
            load_with_token_fallback(load_fn, "org/gated-model", "already-have-a-token", fake_prompt)

    def test_user_declines_prompt_raises(self):
        """An empty response to the token prompt (user cancels) should
        raise rather than looping or silently proceeding without auth."""
        def load_fn(token):
            if token is None:
                raise HFAuthRequiredError("org/gated-model")

        def fake_prompt(repo_id):
            return ""  # user declined / cancelled

        with pytest.raises(HFAuthRequiredError):
            load_with_token_fallback(load_fn, "org/gated-model", None, fake_prompt)

    def test_non_auth_error_is_not_caught_by_fallback(self):
        """A load failure unrelated to auth (e.g. repo doesn't exist,
        network error) should propagate immediately, not trigger a token
        prompt that has nothing to do with the actual problem."""
        def load_fn(token):
            raise ValueError("repo not found")

        def fake_prompt(repo_id):
            pytest.fail("Should not prompt for a non-auth error")

        with pytest.raises(ValueError):
            load_with_token_fallback(load_fn, "org/nonexistent", None, fake_prompt)

    def test_retry_with_token_still_failing_raises_that_failure(self):
        """If the retry with a freshly-prompted token still fails (e.g.
        the user pasted an invalid token), that failure should surface
        plainly rather than looping forever."""
        def load_fn(token):
            raise HFAuthRequiredError("org/gated-model")

        def fake_prompt(repo_id):
            return "hf_invalidtoken"

        with pytest.raises(HFAuthRequiredError):
            load_with_token_fallback(load_fn, "org/gated-model", None, fake_prompt)

    def test_default_prompt_used_when_none_given(self, monkeypatch):
        """When no token_prompt_fn is supplied, the module's default CLI
        prompt should be used automatically."""
        import src.providers.hf_common as hf_common

        monkeypatch.setattr(hf_common, "default_token_prompt", lambda repo_id: "default-token")

        def load_fn(token):
            if token is None:
                raise HFAuthRequiredError("org/gated-model")

        result = load_with_token_fallback(load_fn, "org/gated-model", None, None)
        assert result == "default-token"
