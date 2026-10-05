"""
Speech (text-to-speech) providers.

  none     no audio; shots with dialogue render silent (a warning is logged)
  dummy    offline stand-in: syllable-like tone bursts whose length follows the
           text. Not speech, but it exercises timing/muxing with no tools or keys
  espeak   real offline speech through the ``espeak-ng`` command line tool
  openai   OpenAI text-to-speech (needs openai_api_key)

All write a WAV file. The pipeline normalises it afterwards (see
src/integrations/audio.py), so providers need not agree on sample rate.
"""

import hashlib
import logging
import math
import shutil
import struct
import subprocess
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.integrations.display import subprocess_kwargs

from .base import SpeechProvider

logger = logging.getLogger(__name__)


class SpeechError(Exception):
    """Raised when speech cannot be synthesised."""


class DummySpeechProvider(SpeechProvider):
    """Deterministic placeholder 'speech': one short tone burst per syllable-ish chunk."""

    RATE = 22050
    _VOICES = ["low", "mid", "high"]

    def __init__(self):
        self.call_log: List[Dict[str, Any]] = []

    def voices(self) -> List[str]:
        return list(self._VOICES)

    def synthesize(self, text: str, output_path: Path, voice: Optional[str] = None) -> Path:
        self.call_log.append({"text": text, "voice": voice, "output_path": str(output_path)})
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        base = {"low": 120.0, "mid": 180.0, "high": 260.0}.get(voice or "mid", 180.0)

        syllables = max(1, sum(max(1, len(w) // 3) for w in text.split()))
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        frames = bytearray()
        for i in range(syllables):
            freq = base * (1.0 + (digest[i % len(digest)] % 40) / 200.0)
            burst, gap = int(self.RATE * 0.11), int(self.RATE * 0.04)
            for n in range(burst):
                env = math.sin(math.pi * n / burst)  # fade in/out: no clicks
                frames += struct.pack("<h", int(9000 * env * math.sin(2 * math.pi * freq * n / self.RATE)))
            frames += b"\x00\x00" * gap
        with wave.open(str(output_path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.RATE)
            w.writeframes(bytes(frames))
        return output_path


class EspeakSpeechProvider(SpeechProvider):
    """Real offline speech via espeak-ng (apt install espeak-ng / brew install espeak-ng)."""

    _VOICES = ["en-us", "en-gb", "en-us+f3", "en-gb+m3", "en-us+m5", "en-gb+f4"]

    def __init__(self, binary: str = "espeak-ng", speed_wpm: int = 160):
        self.binary = binary
        self.speed_wpm = speed_wpm
        if shutil.which(binary) is None:
            raise SpeechError(f"{binary!r} not found — install espeak-ng, or pick another speech_provider")

    def voices(self) -> List[str]:
        return list(self._VOICES)

    def synthesize(self, text: str, output_path: Path, voice: Optional[str] = None) -> Path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [self.binary, "-v", voice or self._VOICES[0], "-s", str(self.speed_wpm), "-w", str(output_path), "--", text]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, **subprocess_kwargs())
        if result.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
            raise SpeechError(f"espeak-ng failed: {result.stderr[-300:] or 'no audio written'}")
        return output_path


class OpenAISpeechProvider(SpeechProvider):
    """OpenAI text-to-speech."""

    _VOICES = ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]

    def __init__(self, api_key: str, model: str = "gpt-4o-mini-tts"):
        if not api_key or api_key.endswith("_HERE"):
            raise SpeechError("No OpenAI API key configured for speech")
        try:
            import openai
        except ImportError as e:  # pragma: no cover
            raise SpeechError("openai package not installed: pip install openai") from e
        self.client = openai.OpenAI(api_key=api_key)
        self.model = model

    def voices(self) -> List[str]:
        return list(self._VOICES)

    def synthesize(self, text: str, output_path: Path, voice: Optional[str] = None) -> Path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            response = self.client.audio.speech.create(
                model=self.model, voice=voice or self._VOICES[0], input=text, response_format="wav"
            )
        except Exception as e:
            logger.error(f"OpenAI speech failed: {e}")
            raise
        output_path.write_bytes(response.content)
        return output_path


class PiperSpeechProvider(SpeechProvider):
    """Local neural speech through Piper, using voice models you put in a folder.

    Drop ``<name>.onnx`` (plus its ``<name>.onnx.json``) into the voices folder and
    refer to it by name. Models come from https://huggingface.co/rhasspy/piper-voices or
    are ones you trained/exported yourself. A voice may be a name, a path to an .onnx
    file, and for multi-speaker models ``name:3`` picks speaker 3.
    """

    def __init__(self, voices_dir: Path, binary: str = "piper"):
        self.voices_dir = Path(voices_dir)
        self.binary = binary
        if shutil.which(binary) is None:
            raise SpeechError(f"{binary!r} not found — pip install piper-tts, or pick another speech_provider")

    def voices(self) -> List[str]:
        if not self.voices_dir.is_dir():
            return []
        return sorted(p.stem for p in self.voices_dir.glob("*.onnx"))

    def _resolve(self, voice: Optional[str]):
        speaker = None
        spec = voice
        if spec and ":" in spec and not Path(spec).exists() and spec.rsplit(":", 1)[1].isdigit():
            spec, speaker = spec.rsplit(":", 1)
        if spec:
            for candidate in (Path(spec), self.voices_dir / f"{spec}.onnx", self.voices_dir / spec):
                if candidate.is_file() and candidate.suffix == ".onnx":
                    return candidate, speaker
            raise SpeechError(f"Piper voice {voice!r} not found (looked in {self.voices_dir}; have: {self.voices() or 'none'})")
        available = self.voices()
        if not available:
            raise SpeechError(f"No Piper voices found in {self.voices_dir} — put <name>.onnx (+ .onnx.json) files there")
        return self.voices_dir / f"{available[0]}.onnx", speaker

    def synthesize(self, text: str, output_path: Path, voice: Optional[str] = None) -> Path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        model, speaker = self._resolve(voice)
        cmd = [self.binary, "-m", str(model), "-f", str(output_path)]
        if speaker is not None:
            cmd += ["-s", speaker]
        result = subprocess.run(cmd, input=text, capture_output=True, text=True, timeout=300, **subprocess_kwargs())
        if result.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
            raise SpeechError(f"piper failed: {result.stderr[-300:] or 'no audio written'}")
        return output_path


class CommandSpeechProvider(SpeechProvider):
    """Any text-to-speech program, including one that speaks in a voice you supply.

    ``speech_command`` is an argument list (no shell). Placeholders: ``{text}``, ``{text_file}``
    (a UTF-8 file holding the line), ``{voice}`` and ``{out}`` (where to write the audio; WAV
    preferred, anything ffmpeg can read works). Example, a voice-cloning tool given a reference
    recording of a voice you have the right to use::

        speech_command: ["my-tts", "--ref", "{voice}", "--text-file", "{text_file}", "--out", "{out}"]
        voices: {Ann: /home/me/voices/ann_sample.wav}
    """

    def __init__(self, command: List[str], voices: Optional[List[str]] = None, timeout: int = 600):
        if not command or not isinstance(command, list):
            raise SpeechError("speech_command must be a non-empty list of arguments")
        if shutil.which(command[0]) is None and not Path(command[0]).exists():
            raise SpeechError(f"speech_command program {command[0]!r} not found")
        self.command = [str(c) for c in command]
        self._voices = list(voices or [])
        self.timeout = timeout

    def voices(self) -> List[str]:
        return list(self._voices)

    def synthesize(self, text: str, output_path: Path, voice: Optional[str] = None) -> Path:
        import tempfile

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            text_file = Path(tmp) / "line.txt"
            text_file.write_text(text, encoding="utf-8")
            values = {"text": text, "text_file": str(text_file), "voice": voice or "", "out": str(output_path)}
            try:
                cmd = [part.format(**values) for part in self.command]
            except (KeyError, IndexError, ValueError) as e:
                raise SpeechError(f"bad placeholder in speech_command: {e}") from e
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout, **subprocess_kwargs())
        if result.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
            raise SpeechError(f"speech_command failed ({result.returncode}): {result.stderr[-300:] or 'no audio written'}")
        return output_path


SPEECH_PROVIDERS = {"auto", "none", "dummy", "espeak", "openai", "piper", "command"}


def get_speech_provider(config: Dict[str, Any]) -> Optional[SpeechProvider]:
    """Resolve ``speech_provider``. Returns None for 'none' (or 'auto' with nothing available)."""
    name = str(config.get("speech_provider", "auto")).lower()
    if name not in SPEECH_PROVIDERS:
        raise ValueError(f"Unknown speech_provider {name!r} (expected one of {sorted(SPEECH_PROVIDERS)})")
    provider = _build_speech_provider(name, config)
    if provider is not None:
        provider.assignments = {
            str(k).strip().lower(): str(v) for k, v in (config.get("voices") or {}).items()
        }
    return provider


def piper_voices_dir(config: Dict[str, Any]) -> Path:
    explicit = config.get("piper_voices_dir")
    if explicit:
        return Path(explicit)
    return Path(config.get("storage_path", "./storage")).parent / "voices"


def _build_speech_provider(name: str, config: Dict[str, Any]) -> Optional[SpeechProvider]:
    if name == "none":
        return None
    if name == "dummy":
        return DummySpeechProvider()
    if name == "espeak":
        return EspeakSpeechProvider(binary=config.get("espeak_path", "espeak-ng"))
    if name == "openai":
        return OpenAISpeechProvider(config.get("openai_api_key", ""), config.get("openai_speech_model", "gpt-4o-mini-tts"))
    if name == "piper":
        return PiperSpeechProvider(piper_voices_dir(config), binary=config.get("piper_path", "piper"))
    if name == "command":
        return CommandSpeechProvider(config.get("speech_command") or [], voices=list((config.get("voices") or {}).values()))
    # auto: real offline speech when available, otherwise silent (never beeps in a real film)
    if shutil.which(config.get("espeak_path", "espeak-ng")):
        return EspeakSpeechProvider(binary=config.get("espeak_path", "espeak-ng"))
    return None
