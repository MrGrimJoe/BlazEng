"""
Tests for GeminiTextProvider/VisionProvider/ImageProvider.

The google-genai SDK is installed in this environment, but live calls to
generativelanguage.googleapis.com are not reachable from CI/sandbox
environments in general — and shouldn't be depended on for unit tests
regardless. So these tests mock the `.client` attribute after
construction and verify:
  1. Config validation (missing/placeholder keys) happens without
     touching the network at all.
  2. Our request-building and response-parsing logic calls the SDK the
     way the real API expects, and correctly extracts results.

This does NOT prove the live API contract hasn't changed since the
docs were checked — only a real call could prove that. It proves our
side of the contract is internally consistent and matches the
documented shape.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.providers.gemini_provider import (
    GeminiConfigError,
    GeminiImageProvider,
    GeminiTextProvider,
    GeminiVisionProvider,
    _guess_mime_type,
)


class TestConfigValidation:
    def test_text_provider_rejects_empty_key(self):
        with pytest.raises(GeminiConfigError):
            GeminiTextProvider(api_key="")

    def test_text_provider_rejects_placeholder_key(self):
        with pytest.raises(GeminiConfigError):
            GeminiTextProvider(api_key="GEMINI_API_KEY_HERE")

    def test_vision_provider_rejects_empty_key(self):
        with pytest.raises(GeminiConfigError):
            GeminiVisionProvider(api_key="")

    def test_image_provider_rejects_empty_key(self):
        with pytest.raises(GeminiConfigError):
            GeminiImageProvider(api_key="")

    def test_text_provider_accepts_real_looking_key(self):
        # Should construct without raising (SDK client creation itself
        # doesn't make a network call — verified against SDK docs: the
        # Client constructor just stores credentials).
        provider = GeminiTextProvider(api_key="AIzaRealLookingKey123")
        assert provider.model == "gemini-flash-latest"

    def test_text_provider_uses_configured_model(self):
        provider = GeminiTextProvider(api_key="AIzaKey123", model="gemini-3.7-flash")
        assert provider.model == "gemini-3.7-flash"


class TestGeminiTextProviderGenerate:
    def test_generate_calls_sdk_with_prompt_and_returns_text(self):
        provider = GeminiTextProvider(api_key="AIzaKey123")
        mock_response = MagicMock()
        mock_response.text = "Generated story plan"
        provider.client = MagicMock()
        provider.client.models.generate_content.return_value = mock_response

        result = provider.generate("Write a detective story")

        assert result == "Generated story plan"
        provider.client.models.generate_content.assert_called_once()
        call_kwargs = provider.client.models.generate_content.call_args.kwargs
        assert call_kwargs["model"] == provider.model
        assert call_kwargs["contents"] == "Write a detective story"

    def test_generate_returns_empty_string_for_none_text(self):
        # SDK can return response.text == None (e.g. safety-filtered) —
        # our code should not crash, and should not return the string "None".
        provider = GeminiTextProvider(api_key="AIzaKey123")
        mock_response = MagicMock()
        mock_response.text = None
        provider.client = MagicMock()
        provider.client.models.generate_content.return_value = mock_response

        result = provider.generate("test prompt")
        assert result == ""

    def test_generate_propagates_sdk_exceptions(self):
        provider = GeminiTextProvider(api_key="AIzaKey123")
        provider.client = MagicMock()
        provider.client.models.generate_content.side_effect = RuntimeError("API error")

        with pytest.raises(RuntimeError):
            provider.generate("test prompt")


class TestGeminiVisionProviderAnalyze:
    def test_analyze_raises_for_missing_file(self, tmp_path):
        provider = GeminiVisionProvider(api_key="AIzaKey123")
        with pytest.raises(FileNotFoundError):
            provider.analyze(tmp_path / "does_not_exist.png", "describe this")

    def test_analyze_sends_image_bytes_and_prompt(self, tmp_path):
        image_path = tmp_path / "test.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\nfakepngdata")

        provider = GeminiVisionProvider(api_key="AIzaKey123")
        mock_response = MagicMock()
        mock_response.text = "A person standing in a doorway"
        provider.client = MagicMock()
        provider.client.models.generate_content.return_value = mock_response

        result = provider.analyze(image_path, "Describe this frame")

        assert result == "A person standing in a doorway"
        provider.client.models.generate_content.assert_called_once()


class TestGeminiImageProviderGenerateImage:
    def test_generate_image_writes_returned_bytes(self, tmp_path):
        output_path = tmp_path / "out" / "character.png"
        provider = GeminiImageProvider(api_key="AIzaKey123")

        fake_part = MagicMock()
        fake_part.inline_data.data = b"fake-png-bytes"
        mock_response = MagicMock()
        mock_response.parts = [fake_part]
        provider.client = MagicMock()
        provider.client.models.generate_content.return_value = mock_response

        result_path = provider.generate_image("a detective", output_path)

        assert result_path == output_path
        assert output_path.exists()
        assert output_path.read_bytes() == b"fake-png-bytes"

    def test_generate_image_raises_if_no_image_in_response(self, tmp_path):
        # This is a real, documented failure mode: the model can return
        # text-only output (e.g. if it refuses or the prompt was filtered).
        # We should raise a clear error, not silently write nothing.
        output_path = tmp_path / "character.png"
        provider = GeminiImageProvider(api_key="AIzaKey123")

        text_only_part = MagicMock()
        text_only_part.inline_data = None
        mock_response = MagicMock()
        mock_response.parts = [text_only_part]
        provider.client = MagicMock()
        provider.client.models.generate_content.return_value = mock_response

        with pytest.raises(GeminiConfigError, match="no \\(non-empty\\)"):
            provider.generate_image("a detective", output_path)

    def test_generate_image_raises_on_empty_inline_data_bytes(self, tmp_path):
        # Adversarial case: inline_data exists (isn't None) but its actual
        # byte payload is empty — a plausible partial-failure response.
        # Must not silently write a corrupt zero-byte "image" file.
        output_path = tmp_path / "character.png"
        provider = GeminiImageProvider(api_key="AIzaKey123")

        empty_part = MagicMock()
        empty_part.inline_data.data = b""
        mock_response = MagicMock()
        mock_response.parts = [empty_part]
        provider.client = MagicMock()
        provider.client.models.generate_content.return_value = mock_response

        with pytest.raises(GeminiConfigError, match="no \\(non-empty\\)"):
            provider.generate_image("a detective", output_path)
        assert not output_path.exists()

    def test_generate_image_creates_parent_directories(self, tmp_path):
        output_path = tmp_path / "deeply" / "nested" / "path" / "character.png"
        provider = GeminiImageProvider(api_key="AIzaKey123")

        fake_part = MagicMock()
        fake_part.inline_data.data = b"fake-bytes"
        mock_response = MagicMock()
        mock_response.parts = [fake_part]
        provider.client = MagicMock()
        provider.client.models.generate_content.return_value = mock_response

        provider.generate_image("a detective", output_path)
        assert output_path.exists()


class TestGuessMimeType:
    @pytest.mark.parametrize("suffix,expected", [
        (".png", "image/png"),
        (".jpg", "image/jpeg"),
        (".jpeg", "image/jpeg"),
        (".webp", "image/webp"),
        (".PNG", "image/png"),  # case-insensitive
    ])
    def test_known_extensions(self, suffix, expected):
        assert _guess_mime_type(Path(f"file{suffix}")) == expected

    def test_unknown_extension_defaults_to_png(self):
        assert _guess_mime_type(Path("file.bmp")) == "image/png"
