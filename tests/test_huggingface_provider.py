"""
Tests for HuggingFaceTextProvider/VisionProvider.

`transformers.pipeline` is mocked throughout — actually loading a real
model needs a multi-GB download and (for many models) a GPU, neither
available here, and huggingface.co itself is blocked by this sandbox's
network policy. These tests prove the provider's own logic (lazy
loading, gated-repo detection, token retry, response parsing) is
correct; they do not prove a real download/inference actually works —
see the module docstring for that limitation.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.providers.hf_common import HFAuthRequiredError
from src.providers.huggingface_provider import (
    HuggingFaceConfigError,
    HuggingFaceTextProvider,
    HuggingFaceVisionProvider,
)


class TestHuggingFaceTextProviderConfig:
    def test_requires_repo_id(self):
        with pytest.raises(HuggingFaceConfigError):
            HuggingFaceTextProvider(repo_id="")

    def test_construction_does_not_load_model(self):
        provider = HuggingFaceTextProvider(repo_id="some/model")
        assert provider._pipe is None


class TestHuggingFaceTextProviderGenerate:
    def test_generate_loads_lazily_and_strips_echoed_prompt(self):
        provider = HuggingFaceTextProvider(repo_id="some/model")

        fake_pipe = MagicMock(return_value=[{"generated_text": "Write a story.\nOnce upon a time..."}])
        with patch("src.providers.huggingface_provider._hf_pipeline", return_value=fake_pipe) as mock_pipeline:
            result = provider.generate("Write a story.")

        mock_pipeline.assert_called_once()
        assert mock_pipeline.call_args.args[0] == "text-generation"
        assert mock_pipeline.call_args.kwargs["model"] == "some/model"
        assert result == "Once upon a time..."
        assert provider._pipe is fake_pipe

    def test_generate_only_loads_once_across_multiple_calls(self):
        provider = HuggingFaceTextProvider(repo_id="some/model")
        fake_pipe = MagicMock(return_value=[{"generated_text": "prompt continuation"}])

        with patch("src.providers.huggingface_provider._hf_pipeline", return_value=fake_pipe) as mock_pipeline:
            provider.generate("prompt")
            provider.generate("prompt")

        mock_pipeline.assert_called_once()  # not reloaded on second call

    def test_gated_repo_prompts_for_token_and_retries(self):
        provider = HuggingFaceTextProvider(repo_id="org/gated-model")
        call_tokens = []

        def fake_pipeline(task, model, token, device_map):
            call_tokens.append(token)
            if token is None:
                raise Exception("Cannot access gated repo for url ...")
            return MagicMock(return_value=[{"generated_text": "ok"}])

        provider.token_prompt_fn = lambda repo_id: "hf_providedtoken"

        with patch("src.providers.huggingface_provider._hf_pipeline", side_effect=fake_pipeline):
            provider.generate("prompt")

        assert call_tokens == [None, "hf_providedtoken"]
        assert provider.hf_token == "hf_providedtoken"

    def test_ungated_repo_never_calls_token_prompt(self):
        provider = HuggingFaceTextProvider(repo_id="some/public-model")
        prompt_calls = []
        provider.token_prompt_fn = lambda repo_id: prompt_calls.append(repo_id) or "unused"

        fake_pipe = MagicMock(return_value=[{"generated_text": "ok"}])
        with patch("src.providers.huggingface_provider._hf_pipeline", return_value=fake_pipe):
            provider.generate("prompt")

        assert prompt_calls == []

    def test_transformers_not_installed_raises_config_error(self):
        provider = HuggingFaceTextProvider(repo_id="some/model")
        with patch("src.providers.huggingface_provider._TRANSFORMERS_AVAILABLE", False):
            with pytest.raises(HuggingFaceConfigError):
                provider.generate("prompt")


class TestHuggingFaceVisionProvider:
    def test_requires_repo_id(self):
        with pytest.raises(HuggingFaceConfigError):
            HuggingFaceVisionProvider(repo_id="")

    def test_analyze_raises_for_missing_file(self, tmp_path):
        provider = HuggingFaceVisionProvider(repo_id="some/model")
        with pytest.raises(FileNotFoundError):
            provider.analyze(tmp_path / "missing.png", "describe")

    def test_analyze_returns_top_answer(self, tmp_path):
        image_path = tmp_path / "frame.png"
        image_path.write_bytes(b"fake png bytes")

        provider = HuggingFaceVisionProvider(repo_id="some/vqa-model")
        fake_pipe = MagicMock(return_value=[
            {"answer": "a detective in a doorway", "score": 0.9},
            {"answer": "a person", "score": 0.4},
        ])
        with patch("src.providers.huggingface_provider._hf_pipeline", return_value=fake_pipe):
            result = provider.analyze(image_path, "What is this?")

        assert result == "a detective in a doorway"

    def test_gated_vision_repo_prompts_for_token(self, tmp_path):
        image_path = tmp_path / "frame.png"
        image_path.write_bytes(b"fake png bytes")

        provider = HuggingFaceVisionProvider(repo_id="org/gated-vqa")
        provider.token_prompt_fn = lambda repo_id: "hf_token456"
        call_tokens = []

        def fake_pipeline(task, model, token, device_map):
            call_tokens.append(token)
            if token is None:
                raise Exception("Access to model is restricted. You must be authenticated.")
            return MagicMock(return_value=[{"answer": "ok", "score": 1.0}])

        with patch("src.providers.huggingface_provider._hf_pipeline", side_effect=fake_pipeline):
            provider.analyze(image_path, "describe")

        assert call_tokens == [None, "hf_token456"]
