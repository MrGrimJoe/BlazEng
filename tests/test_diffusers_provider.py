"""
Tests for DiffusersImageProvider — same testing approach and same
limitation as test_huggingface_provider.py: the pipeline itself is
mocked, since a real Stable Diffusion download needs several GB and
huggingface.co is blocked in this sandbox. These prove the provider's
own logic (lazy loading, device selection, gated-repo token retry) is
correct, not that a real download/generation works.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.providers.diffusers_provider import DiffusersConfigError, DiffusersImageProvider


class TestDiffusersImageProviderConfig:
    def test_requires_repo_id(self):
        with pytest.raises(DiffusersConfigError):
            DiffusersImageProvider(repo_id="")

    def test_construction_does_not_load_model(self):
        provider = DiffusersImageProvider(repo_id="some/sd-model")
        assert provider._pipe is None


class TestDiffusersImageProviderGenerate:
    def test_generate_loads_lazily_and_saves_image(self, tmp_path):
        provider = DiffusersImageProvider(repo_id="some/sd-model", device="cpu")
        output_path = tmp_path / "character.png"

        fake_image = MagicMock()
        fake_pipe = MagicMock()
        fake_pipe.to.return_value = fake_pipe
        fake_pipe.return_value = MagicMock(images=[fake_image])

        with patch(
            "src.providers.diffusers_provider._AutoPipelineForText2Image.from_pretrained",
            return_value=fake_pipe,
        ) as mock_from_pretrained:
            result = provider.generate_image("a detective", output_path)

        mock_from_pretrained.assert_called_once_with("some/sd-model", token=None)
        fake_pipe.to.assert_called_once_with("cpu")
        fake_image.save.assert_called_once_with(output_path)
        assert result == output_path

    def test_generate_only_loads_once_across_multiple_calls(self, tmp_path):
        provider = DiffusersImageProvider(repo_id="some/sd-model", device="cpu")
        fake_pipe = MagicMock()
        fake_pipe.to.return_value = fake_pipe
        fake_pipe.return_value = MagicMock(images=[MagicMock()])

        with patch(
            "src.providers.diffusers_provider._AutoPipelineForText2Image.from_pretrained",
            return_value=fake_pipe,
        ) as mock_from_pretrained:
            provider.generate_image("prompt one", tmp_path / "a.png")
            provider.generate_image("prompt two", tmp_path / "b.png")

        mock_from_pretrained.assert_called_once()

    def test_auto_device_selects_cpu_when_no_cuda(self, tmp_path):
        provider = DiffusersImageProvider(repo_id="some/sd-model", device="auto")
        fake_pipe = MagicMock()
        fake_pipe.to.return_value = fake_pipe
        fake_pipe.return_value = MagicMock(images=[MagicMock()])

        fake_torch = MagicMock()
        fake_torch.cuda.is_available.return_value = False

        with patch(
            "src.providers.diffusers_provider._AutoPipelineForText2Image.from_pretrained",
            return_value=fake_pipe,
        ), patch.dict("sys.modules", {"torch": fake_torch}):
            provider.generate_image("prompt", tmp_path / "a.png")

        fake_pipe.to.assert_called_once_with("cpu")

    def test_gated_repo_prompts_for_token_and_retries(self, tmp_path):
        provider = DiffusersImageProvider(repo_id="org/gated-sd-model", device="cpu")
        provider.token_prompt_fn = lambda repo_id: "hf_sdtoken"
        call_tokens = []

        fake_pipe = MagicMock()
        fake_pipe.to.return_value = fake_pipe
        fake_pipe.return_value = MagicMock(images=[MagicMock()])

        def fake_from_pretrained(repo_id, token):
            call_tokens.append(token)
            if token is None:
                raise Exception("Cannot access gated repo for url ...")
            return fake_pipe

        with patch(
            "src.providers.diffusers_provider._AutoPipelineForText2Image.from_pretrained",
            side_effect=fake_from_pretrained,
        ):
            provider.generate_image("prompt", tmp_path / "a.png")

        assert call_tokens == [None, "hf_sdtoken"]
        assert provider.hf_token == "hf_sdtoken"

    def test_ungated_repo_never_calls_token_prompt(self, tmp_path):
        provider = DiffusersImageProvider(repo_id="some/public-sd-model", device="cpu")
        prompt_calls = []
        provider.token_prompt_fn = lambda repo_id: prompt_calls.append(repo_id) or "unused"

        fake_pipe = MagicMock()
        fake_pipe.to.return_value = fake_pipe
        fake_pipe.return_value = MagicMock(images=[MagicMock()])

        with patch(
            "src.providers.diffusers_provider._AutoPipelineForText2Image.from_pretrained",
            return_value=fake_pipe,
        ):
            provider.generate_image("prompt", tmp_path / "a.png")

        assert prompt_calls == []

    def test_diffusers_not_installed_raises_config_error(self, tmp_path):
        provider = DiffusersImageProvider(repo_id="some/sd-model")
        with patch("src.providers.diffusers_provider._DIFFUSERS_AVAILABLE", False):
            with pytest.raises(DiffusersConfigError):
                provider.generate_image("prompt", tmp_path / "a.png")
