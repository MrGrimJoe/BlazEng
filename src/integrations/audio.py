"""
Audio helpers: normalise TTS output and join a shot's lines into one WAV.

Providers return WAVs with different sample rates/channel counts, so every line
is first converted to one common format (44.1 kHz mono 16-bit) with ffmpeg,
then lines are joined with a short silence between speakers using the stdlib
``wave`` module.
"""

import subprocess
import wave
from pathlib import Path
from typing import Any, Dict, List

from .display import subprocess_kwargs

SAMPLE_RATE = 44100


class AudioError(Exception):
    """Raised when audio cannot be converted or joined."""


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def normalise(src: Path, dst: Path, config: Dict[str, Any]) -> Path:
    ffmpeg = config.get("ffmpeg_path", "ffmpeg")
    cmd = [ffmpeg, "-y", "-i", str(src), "-ar", str(SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_s16le", str(dst)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, **subprocess_kwargs())
    except FileNotFoundError as e:
        raise AudioError(f"ffmpeg not found: {ffmpeg!r}") from e
    if result.returncode != 0:
        raise AudioError(f"ffmpeg could not convert {Path(src).name}: {result.stderr[-300:]}")
    return dst


def join_lines(lines: List[Path], dst: Path, gap_seconds: float = 0.3) -> Path:
    """Concatenate already-normalised WAVs, with ``gap_seconds`` of silence between them."""
    if not lines:
        raise AudioError("No audio lines to join")
    silence = b"\x00\x00" * int(SAMPLE_RATE * gap_seconds)
    with wave.open(str(dst), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        for i, line in enumerate(lines):
            with wave.open(str(line), "rb") as w:
                if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (1, 2, SAMPLE_RATE):
                    raise AudioError(f"{line} is not normalised audio")
                if i:
                    out.writeframes(silence)
                out.writeframes(w.readframes(w.getnframes()))
    return dst


def line_offsets(lines: List[Path], gap_seconds: float = 0.3) -> List[tuple]:
    """(start, end) seconds of each line inside the file join_lines() builds from them."""
    out, t = [], 0.0
    for i, line in enumerate(lines):
        if i:
            t += gap_seconds
        d = wav_duration(line)
        out.append((round(t, 4), round(t + d, 4)))
        t += d
    return out


def envelope(path: Path, fps: int, start: float = 0.0, end: float = None, frames: int = None) -> List[float]:
    """Mouth-openness per video frame (0..1) from a normalised WAV's loudness.

    One value per frame of video: the RMS of that frame's slice of audio, scaled so the
    loudest part of the span is 1.0, with a noise floor so silence stays at 0 and a
    little smoothing so the mouth doesn't flicker. ``start``/``end`` pick a span of the
    file (seconds); the result is padded/trimmed to ``frames`` values when given, with
    index 0 = the start of the span.
    """
    import array

    with wave.open(str(path), "rb") as w:
        rate = w.getframerate()
        raw = w.readframes(w.getnframes())
    samples = array.array("h")
    samples.frombytes(raw[: len(raw) - (len(raw) % 2)])
    total = len(samples)
    a = max(0, int(start * rate))
    b = total if end is None else min(total, int(end * rate))
    per = rate / float(fps)
    n = int((b - a) / per) if b > a else 0
    rms = []
    for i in range(n):
        chunk = samples[a + int(i * per): a + int((i + 1) * per)]
        rms.append((sum(x * x for x in chunk) / len(chunk)) ** 0.5 if chunk else 0.0)
    peak = max(rms) if rms else 0.0
    if peak < 300:  # effectively silent
        vals = [0.0] * len(rms)
    else:
        vals = [min(1.0, max(0.0, (r / peak - 0.12) / 0.88)) for r in rms]
        sm = []
        for i, v in enumerate(vals):
            prev = vals[i - 1] if i else v
            sm.append(0.65 * v + 0.35 * prev)
        top = max(sm) or 1.0
        vals = [round(v / top, 3) for v in sm]  # re-normalise: the loudest moment is fully open
    if frames is not None:
        vals = (vals + [0.0] * frames)[:frames]
    return vals
