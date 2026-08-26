from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.providers.openai_provider import (
    OpenAIConfigError,
    OpenAIImageProvider,
    OpenAITextProvider,
    OpenAIVisionProvider,
)


class TestConfigValidation:
    def test_text_provider_rejects_empty_key(self):
        with pytest.raises(OpenAIConfigError):
            OpenAITextProvider(api_key="")

    def test_text_provider_rejects_placeholder_key(self):
        with pytest.raises(OpenAIConfigError):
            OpenAITextProvider(api_key="OPENAI_API_KEY_HERE")

    def test_vision_provider_rejects_empty_key(self):
        with pytest.raises(OpenAIConfigError):
            OpenAIVisionProvider(api_key="")

    def test_image_provider_rejects_empty_key(self):
        with pytest.raises(OpenAIConfigError):
            OpenAIImageProvider(api_key="")

    def test_text_provider_accepts_real_looking_key(self):
        provider = OpenAITextProvider(api_key="sk-reallookingkey123")
        assert provider.model == "gpt-5.5"


class TestOpenAITextGenerate:
    def test_generate_returns_message_content(self):
        provider = OpenAITextProvider(api_key="sk-key123")
        mock_choice = MagicMock()
        mock_choice.message.content = "Generated plan"
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        provider.client = MagicMock()
        provider.client.chat.completions.create.return_value = mock_response

        result = provider.generate("Write a story")
        assert result == "Generated plan"

    def test_generate_includes_system_instruction_as_system_message(self):
        provider = OpenAITextProvider(api_key="sk-key123")
        mock_choice = MagicMock()
        mock_choice.message.content = "ok"
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        provider.client = MagicMock()
        provider.client.chat.completions.create.return_value = mock_response

        provider.generate("prompt", system_instruction="You are a director")
        messages = provider.client.chat.completions.create.call_args.kwargs["messages"]
        assert messages[0] == {"role": "system", "content": "You are a director"}
        assert messages[1] == {"role": "user", "content": "prompt"}

    def test_generate_returns_empty_string_for_none_content(self):
        provider = OpenAITextProvider(api_key="sk-key123")
        mock_choice = MagicMock()
        mock_choice.message.content = None
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        provider.client = MagicMock()
        provider.client.chat.completions.create.return_value = mock_response

        assert provider.generate("prompt") == ""

    def test_generate_propagates_sdk_exceptions(self):
        provider = OpenAITextProvider(api_key="sk-key123")
        provider.client = MagicMock()
        provider.client.chat.completions.create.side_effect = RuntimeError("rate limited")
        with pytest.raises(RuntimeError):
            provider.generate("prompt")


class TestOpenAIVisionAnalyze:
    def test_analyze_raises_for_missing_file(self, tmp_path):
        provider = OpenAIVisionProvider(api_key="sk-key123")
        with pytest.raises(FileNotFoundError):
            provider.analyze(tmp_path / "missing.png", "describe")

    def test_analyze_sends_base64_data_url(self, tmp_path):
        image_path = tmp_path / "frame.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\nfakepngdata")

        provider = OpenAIVisionProvider(api_key="sk-key123")
        mock_choice = MagicMock()
        mock_choice.message.content = "A person in a doorway"
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        provider.client = MagicMock()
        provider.client.chat.completions.create.return_value = mock_response

        result = provider.analyze(image_path, "Describe this")
        assert result == "A person in a doorway"

        messages = provider.client.chat.completions.create.call_args.kwargs["messages"]
        content = messages[0]["content"]
        assert content[0] == {"type": "text", "text": "Describe this"}
        assert content[1]["type"] == "image_url"
        assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


class TestOpenAIImageGenerate:
    def test_generate_image_decodes_and_writes_b64(self, tmp_path):
        import base64
        output_path = tmp_path / "out" / "character.png"
        provider = OpenAIImageProvider(api_key="sk-key123")

        fake_image = MagicMock()
        fake_image.b64_json = base64.b64encode(b"fake-png-bytes").decode()
        mock_response = MagicMock()
        mock_response.data = [fake_image]
        provider.client = MagicMock()
        provider.client.images.generate.return_value = mock_response

        result_path = provider.generate_image("a detective", output_path)
        assert result_path == output_path
        assert output_path.read_bytes() == b"fake-png-bytes"

    def test_generate_image_raises_if_no_data(self, tmp_path):
        output_path = tmp_path / "character.png"
        provider = OpenAIImageProvider(api_key="sk-key123")
        mock_response = MagicMock()
        mock_response.data = []
        provider.client = MagicMock()
        provider.client.images.generate.return_value = mock_response

        with pytest.raises(OpenAIConfigError, match="no image data"):
            provider.generate_image("a detective", output_path)
