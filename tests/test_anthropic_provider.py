from unittest.mock import MagicMock

import pytest

from src.providers.anthropic_provider import (
    AnthropicConfigError,
    AnthropicTextProvider,
    AnthropicVisionProvider,
    _extract_text,
)


def _text_block(text):
    block = MagicMock()
    block.type = "text"
    block.text = text
    return block


class TestConfigValidation:
    def test_text_provider_rejects_empty_key(self):
        with pytest.raises(AnthropicConfigError):
            AnthropicTextProvider(api_key="")

    def test_text_provider_rejects_placeholder_key(self):
        with pytest.raises(AnthropicConfigError):
            AnthropicTextProvider(api_key="ANTHROPIC_API_KEY_HERE")

    def test_vision_provider_rejects_empty_key(self):
        with pytest.raises(AnthropicConfigError):
            AnthropicVisionProvider(api_key="")

    def test_text_provider_accepts_real_looking_key(self):
        provider = AnthropicTextProvider(api_key="sk-ant-reallooking123")
        assert provider.model == "claude-sonnet-4-5"
        assert provider.max_tokens == 2048


class TestExtractText:
    def test_single_text_block(self):
        message = MagicMock()
        message.content = [_text_block("hello world")]
        assert _extract_text(message) == "hello world"

    def test_multiple_text_blocks_concatenated(self):
        message = MagicMock()
        message.content = [_text_block("part one "), _text_block("part two")]
        assert _extract_text(message) == "part one part two"

    def test_non_text_blocks_skipped(self):
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        message = MagicMock()
        message.content = [tool_block, _text_block("the answer")]
        assert _extract_text(message) == "the answer"

    def test_empty_content_returns_empty_string(self):
        message = MagicMock()
        message.content = []
        assert _extract_text(message) == ""


class TestAnthropicTextGenerate:
    def test_generate_returns_extracted_text(self):
        provider = AnthropicTextProvider(api_key="sk-ant-key123")
        mock_message = MagicMock()
        mock_message.content = [_text_block("Generated plan")]
        provider.client = MagicMock()
        provider.client.messages.create.return_value = mock_message

        result = provider.generate("Write a story")
        assert result == "Generated plan"

    def test_generate_requires_max_tokens_in_call(self):
        # Anthropic's API requires max_tokens on every call — verify we
        # always pass it, since forgetting it is a real, documented
        # integration mistake.
        provider = AnthropicTextProvider(api_key="sk-ant-key123", max_tokens=512)
        mock_message = MagicMock()
        mock_message.content = [_text_block("ok")]
        provider.client = MagicMock()
        provider.client.messages.create.return_value = mock_message

        provider.generate("prompt")
        kwargs = provider.client.messages.create.call_args.kwargs
        assert kwargs["max_tokens"] == 512

    def test_generate_puts_system_instruction_in_system_param_not_messages(self):
        # Anthropic doesn't support a {"role": "system"} message — it must
        # go in the top-level `system` kwarg. A common integration mistake
        # per Anthropic's own docs.
        provider = AnthropicTextProvider(api_key="sk-ant-key123")
        mock_message = MagicMock()
        mock_message.content = [_text_block("ok")]
        provider.client = MagicMock()
        provider.client.messages.create.return_value = mock_message

        provider.generate("prompt", system_instruction="You are a director")
        kwargs = provider.client.messages.create.call_args.kwargs
        assert kwargs["system"] == "You are a director"
        assert all(m.get("role") != "system" for m in kwargs["messages"])

    def test_generate_propagates_sdk_exceptions(self):
        provider = AnthropicTextProvider(api_key="sk-ant-key123")
        provider.client = MagicMock()
        provider.client.messages.create.side_effect = RuntimeError("overloaded")
        with pytest.raises(RuntimeError):
            provider.generate("prompt")


class TestAnthropicVisionAnalyze:
    def test_analyze_raises_for_missing_file(self, tmp_path):
        provider = AnthropicVisionProvider(api_key="sk-ant-key123")
        with pytest.raises(FileNotFoundError):
            provider.analyze(tmp_path / "missing.png", "describe")

    def test_analyze_sends_base64_image_block(self, tmp_path):
        image_path = tmp_path / "frame.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\nfakepngdata")

        provider = AnthropicVisionProvider(api_key="sk-ant-key123")
        mock_message = MagicMock()
        mock_message.content = [_text_block("A person in a doorway")]
        provider.client = MagicMock()
        provider.client.messages.create.return_value = mock_message

        result = provider.analyze(image_path, "Describe this")
        assert result == "A person in a doorway"

        kwargs = provider.client.messages.create.call_args.kwargs
        content = kwargs["messages"][0]["content"]
        assert content[0]["type"] == "image"
        assert content[0]["source"]["type"] == "base64"
        assert content[0]["source"]["media_type"] == "image/png"
        assert content[1] == {"type": "text", "text": "Describe this"}
