"""Dialogue and speech: plan parsing, TTS providers, audio joining, orchestration, muxing, timeline."""

import json
import logging
import shutil
import subprocess
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.core.director.director import Director, ProductionPlan, Shot, _coerce_dialogue
from src.core.orchestrator.orchestrator import PipelineOrchestrator
from src.core.world_state.world_state import WorldStateManager
from src.integrations import audio
from src.integrations.ffmpeg.video_assembler import VideoAssembler
from src.integrations.otio.timeline_export import export_timeline
from src.pipeline import timeline_shots
from src.providers.dummy_provider import DummyShotPlanTextProvider, DummyTextProvider
from src.providers.provider_factory import validate_provider_config
from src.providers.speech_providers import (
    DummySpeechProvider, EspeakSpeechProvider, OpenAISpeechProvider, SpeechError, get_speech_provider,
)

HAVE_FFMPEG = shutil.which("ffmpeg") is not None
HAVE_ESPEAK = shutil.which("espeak-ng") is not None
requires_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")


def stream_types(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    return sorted(out)


def duration(path):
    return float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip())


# ---------------------------------------------------------------------------
# dialogue in the plan
# ---------------------------------------------------------------------------

class TestCoerceDialogue:
    def test_dicts(self):
        assert _coerce_dialogue([{"character": "Ann", "line": "Hi."}]) == [{"character": "Ann", "line": "Hi."}]

    def test_alternate_keys(self):
        assert _coerce_dialogue([{"speaker": "Ann", "text": "Hi."}]) == [{"character": "Ann", "line": "Hi."}]

    def test_name_colon_strings(self):
        assert _coerce_dialogue(["Ann: Hello there", "Bob: Hi: friend"]) == [
            {"character": "Ann", "line": "Hello there"}, {"character": "Bob", "line": "Hi: friend"}]

    def test_single_item_not_in_list(self):
        assert _coerce_dialogue({"character": "Ann", "line": "Hi."}) == [{"character": "Ann", "line": "Hi."}]
        assert _coerce_dialogue("Ann: Hi.") == [{"character": "Ann", "line": "Hi."}]

    @pytest.mark.parametrize("bad", [None, "", [], 5, [{}], [{"character": "Ann"}], [{"line": "x"}], ["no colon"], [None, 3]])
    def test_junk_becomes_empty(self, bad):
        assert _coerce_dialogue(bad) == []

    def test_partial_junk_keeps_good_lines(self):
        assert _coerce_dialogue([{"x": 1}, "Ann: ok", 7]) == [{"character": "Ann", "line": "ok"}]


class TestShotDialogue:
    def test_from_dict_and_metadata_roundtrip(self):
        s = Shot.from_dict({"shot_id": "s1", "scene_description": "x",
                            "dialogue": [{"character": "Ann", "line": "Hi."}]}, 1)
        assert s.dialogue == [{"character": "Ann", "line": "Hi."}]
        assert Shot.from_dict({**s.to_metadata(), "shot_id": "s1"}, 1).dialogue == s.dialogue

    def test_default_is_silent(self):
        assert Shot.from_dict({"scene_description": "x"}, 1).dialogue == []


@pytest.fixture
def world(tmp_path):
    w = WorldStateManager({"storage_path": str(tmp_path / "s")})
    yield w
    w.close()


class TestDirector:
    def test_speak_tasks_only_for_dialogue_shots_and_before_render(self, world):
        d = Director(DummyTextProvider(), world)
        plan = ProductionPlan("p", [
            Shot("a", "x", ["Ann"], dialogue=[{"character": "Ann", "line": "Hi."}]),
            Shot("b", "y", ["Ann"]),
        ])
        types = [(t.task_type, t.shot_id) for t in d.create_task_schedule(plan)]
        assert ("speak", "a") in types and ("speak", "b") not in types
        assert types.index(("speak", "a")) < min(i for i, t in enumerate(types) if t[0] == "render")

    def test_no_dialogue_means_no_new_tasks(self, world):
        d = Director(DummyShotPlanTextProvider(), world)
        plan = d.generate_production_plan("story")
        assert not [t for t in d.create_task_schedule(plan) if t.task_type == "speak"]

    def test_llm_dialogue_is_parsed_and_stored(self, world):
        raw = json.dumps({"shots": [{"shot_id": "s1", "scene_description": "x", "characters": ["Ann"],
                                     "dialogue": ["Ann: Look at this."]}],
                          "world_state_seed": {"Ann": {"appearance": "red"}}})
        plan = Director(DummyTextProvider(canned_response=raw), world).generate_production_plan("p")
        assert plan.shots[0].dialogue == [{"character": "Ann", "line": "Look at this."}]
        assert world.get_shot("s1")["dialogue"] == [{"character": "Ann", "line": "Look at this."}]

    def test_repair_keeps_dialogue(self, world):
        world.add_shot("s1", Shot("s1", "x", ["Ann"], dialogue=[{"character": "Ann", "line": "Hi."}]).to_metadata())
        repaired = Director(DummyTextProvider(canned_response='{"scene_description": "better"}'), world).repair_shot("s1", "bad")
        assert repaired.dialogue == [{"character": "Ann", "line": "Hi."}]


# ---------------------------------------------------------------------------
# providers
# ---------------------------------------------------------------------------

class TestDummySpeech:
    def test_longer_text_is_longer_audio(self, tmp_path):
        p = DummySpeechProvider()
        short = audio.wav_duration(p.synthesize("Hi.", tmp_path / "a.wav"))
        long = audio.wav_duration(p.synthesize("This is a much longer sentence than the other one.", tmp_path / "b.wav"))
        assert long > short * 2

    def test_deterministic(self, tmp_path):
        p = DummySpeechProvider()
        assert p.synthesize("Same", tmp_path / "a.wav").read_bytes() == p.synthesize("Same", tmp_path / "b.wav").read_bytes()

    def test_voices_differ(self, tmp_path):
        p = DummySpeechProvider()
        assert p.synthesize("Same", tmp_path / "a.wav", "low").read_bytes() != p.synthesize("Same", tmp_path / "b.wav", "high").read_bytes()

    def test_voice_for_is_stable_and_case_insensitive(self):
        p = DummySpeechProvider()
        assert p.voice_for("Ann") == p.voice_for(" ann ") and p.voice_for("Ann") in p.voices()


class TestEspeak:
    def test_missing_binary(self):
        with pytest.raises(SpeechError, match="not found"):
            EspeakSpeechProvider(binary="definitely-not-espeak")

    @pytest.mark.skipif(not HAVE_ESPEAK, reason="espeak-ng not installed")
    def test_real_speech(self, tmp_path):
        p = EspeakSpeechProvider()
        f = p.synthesize("Hello there, detective.", tmp_path / "a.wav", p.voice_for("Ann"))
        assert audio.wav_duration(f) > 0.5

    @pytest.mark.skipif(not HAVE_ESPEAK, reason="espeak-ng not installed")
    def test_text_starting_with_dash_is_spoken_not_parsed_as_flag(self, tmp_path):
        assert audio.wav_duration(EspeakSpeechProvider().synthesize("-v hello", tmp_path / "a.wav")) > 0.2

    def test_failure_is_reported(self, tmp_path):
        p = EspeakSpeechProvider.__new__(EspeakSpeechProvider)
        p.binary, p.speed_wpm = "espeak-ng", 160
        with patch("subprocess.run", return_value=MagicMock(returncode=1, stderr="boom")):
            with pytest.raises(SpeechError, match="boom"):
                p.synthesize("x", tmp_path / "a.wav")


class TestOpenAISpeech:
    def test_needs_key(self):
        with pytest.raises(SpeechError):
            OpenAISpeechProvider("OPENAI_API_KEY_HERE")

    def test_writes_returned_audio(self, tmp_path):
        with patch("openai.OpenAI") as client:
            client.return_value.audio.speech.create.return_value = MagicMock(content=b"RIFFdata")
            p = OpenAISpeechProvider("sk-test")
            f = p.synthesize("Hello", tmp_path / "a.wav", voice="nova")
        assert f.read_bytes() == b"RIFFdata"
        kwargs = client.return_value.audio.speech.create.call_args.kwargs
        assert kwargs["voice"] == "nova" and kwargs["input"] == "Hello" and kwargs["response_format"] == "wav"


class TestFactory:
    def test_none(self):
        assert get_speech_provider({"speech_provider": "none"}) is None

    def test_dummy(self):
        assert isinstance(get_speech_provider({"speech_provider": "dummy"}), DummySpeechProvider)

    def test_unknown(self):
        with pytest.raises(ValueError, match="Unknown speech_provider"):
            get_speech_provider({"speech_provider": "klingon"})

    def test_auto_uses_espeak_when_installed(self):
        with patch("shutil.which", return_value="/usr/bin/espeak-ng"):
            assert isinstance(get_speech_provider({}), EspeakSpeechProvider)

    def test_auto_is_silent_not_beeps_when_nothing_installed(self):
        with patch("shutil.which", return_value=None):
            assert get_speech_provider({}) is None

    def test_validation_flags_bad_speech_config(self):
        base = {"text_provider": "dummy", "vision_provider": "dummy", "image_provider": "dummy"}
        assert validate_provider_config({**base, "speech_provider": "dummy"})[0]
        ok, msg = validate_provider_config({**base, "speech_provider": "klingon"})
        assert not ok and "speech_provider" in msg
        ok, msg = validate_provider_config({**base, "speech_provider": "openai"})
        assert not ok and "openai_api_key" in msg


# ---------------------------------------------------------------------------
# audio helpers
# ---------------------------------------------------------------------------

@requires_ffmpeg
class TestAudioHelpers:
    def test_normalise_converts_to_common_format(self, tmp_path):
        raw = DummySpeechProvider().synthesize("Hello world", tmp_path / "raw.wav")  # 22.05 kHz
        out = audio.normalise(raw, tmp_path / "n.wav", {})
        with wave.open(str(out)) as w:
            assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, audio.SAMPLE_RATE)

    def test_normalise_reports_ffmpeg_failure(self, tmp_path):
        (tmp_path / "bad.wav").write_text("not audio")
        with pytest.raises(audio.AudioError, match="could not convert"):
            audio.normalise(tmp_path / "bad.wav", tmp_path / "o.wav", {})

    def test_join_adds_gap_between_lines_only(self, tmp_path):
        p = DummySpeechProvider()
        a = audio.normalise(p.synthesize("one two three", tmp_path / "a.wav"), tmp_path / "an.wav", {})
        b = audio.normalise(p.synthesize("four five six", tmp_path / "b.wav"), tmp_path / "bn.wav", {})
        joined = audio.join_lines([a, b], tmp_path / "j.wav", gap_seconds=0.5)
        assert audio.wav_duration(joined) == pytest.approx(audio.wav_duration(a) + audio.wav_duration(b) + 0.5, abs=0.01)
        single = audio.join_lines([a], tmp_path / "s.wav", gap_seconds=0.5)
        assert audio.wav_duration(single) == pytest.approx(audio.wav_duration(a), abs=0.01)

    def test_join_rejects_unnormalised(self, tmp_path):
        raw = DummySpeechProvider().synthesize("hello", tmp_path / "raw.wav")
        with pytest.raises(audio.AudioError, match="not normalised"):
            audio.join_lines([raw], tmp_path / "j.wav")

    def test_join_nothing(self, tmp_path):
        with pytest.raises(audio.AudioError):
            audio.join_lines([], tmp_path / "j.wav")


# ---------------------------------------------------------------------------
# orchestrator
# ---------------------------------------------------------------------------

def make_orchestrator(tmp_path, world, speech, dialogue, duration_seconds=1.0):
    cfg = {"storage_path": str(tmp_path / "s")}
    world.add_shot("s1", Shot("s1", "x", ["Ann"], duration_seconds=duration_seconds, dialogue=dialogue).to_metadata())
    orch = PipelineOrchestrator(cfg, world, MagicMock(), MagicMock(), MagicMock(), MagicMock(), renderer=MagicMock(),
                                speech_provider=speech)
    task = MagicMock(task_type="speak", shot_id="s1")
    return orch, task


LINES = [{"character": "Ann", "line": "This is a fairly long line of dialogue for Ann to say out loud."},
         {"character": "Bob", "line": "And Bob replies."}]


@requires_ffmpeg
class TestOrchestratorSpeak:
    def test_synthesises_joins_and_lengthens_shot(self, tmp_path, world):
        speech = DummySpeechProvider()
        orch, task = make_orchestrator(tmp_path, world, speech, LINES, duration_seconds=1.0)
        orch._run_speak(task)
        wav = orch.shot_audio["s1"]
        assert audio.wav_duration(wav) > 2
        assert world.get_shot("s1")["duration_seconds"] == pytest.approx(audio.wav_duration(wav) + 0.4, abs=0.02)
        # one voice per speaker, stable per name
        assert [c["voice"] for c in speech.call_log] == [speech.voice_for("Ann"), speech.voice_for("Bob")]

    def test_does_not_shorten_a_long_shot(self, tmp_path, world):
        orch, task = make_orchestrator(tmp_path, world, DummySpeechProvider(), [LINES[1]], duration_seconds=30.0)
        orch._run_speak(task)
        assert world.get_shot("s1")["duration_seconds"] == 30.0

    def test_status_survives_the_duration_update(self, tmp_path, world):
        orch, task = make_orchestrator(tmp_path, world, DummySpeechProvider(), LINES)
        world.set_shot_status("s1", "composed")
        orch._run_speak(task)
        assert world.get_shot("s1")["status"] == "composed"

    def test_no_provider_leaves_shot_silent_with_warning(self, tmp_path, world, caplog):
        orch, task = make_orchestrator(tmp_path, world, None, LINES)
        with caplog.at_level(logging.WARNING):
            orch._run_speak(task)
        assert orch.shot_audio == {} and "silent" in caplog.text
        assert world.get_shot("s1")["duration_seconds"] == 1.0

    def test_no_dialogue_is_a_noop(self, tmp_path, world):
        speech = DummySpeechProvider()
        orch, task = make_orchestrator(tmp_path, world, speech, [])
        orch._run_speak(task)
        assert speech.call_log == [] and orch.shot_audio == {}

    def test_synthesis_failure_is_recorded_not_raised_from_run_pipeline(self, tmp_path, world):
        speech = MagicMock()
        speech.voice_for.return_value = None
        speech.synthesize.side_effect = SpeechError("tts down")
        orch, task = make_orchestrator(tmp_path, world, speech, LINES)
        task.payload = {}
        orch.load_task_schedule([task])
        assert orch.run_pipeline() is False
        assert orch.failures[0].task_type == "speak" and "tts down" in str(orch.failures[0].error)

    def test_render_uses_the_lengthened_duration(self, tmp_path, world):
        orch, speak = make_orchestrator(tmp_path, world, DummySpeechProvider(), LINES, duration_seconds=1.0)
        orch._run_speak(speak)
        orch._scene_paths["s1"] = Path("scene.tscn")
        orch.godot_renderer.render_shot.return_value = []
        orch._run_render(MagicMock(task_type="render", shot_id="s1"))
        assert orch.godot_renderer.render_shot.call_args.kwargs["duration_seconds"] > 2.5


# ---------------------------------------------------------------------------
# assembly
# ---------------------------------------------------------------------------

def frames_for(tmp_path, shot_id, count, color="red"):
    d = tmp_path / "frames" / shot_id
    d.mkdir(parents=True)
    out = []
    for i in range(count):
        f = d / f"frame{i:08d}.png"
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=64x48:d=1", "-frames:v", "1", str(f)],
                       capture_output=True, check=True, timeout=30)
        out.append(f)
    return out


def dialogue_wav(tmp_path, name, text):
    raw = DummySpeechProvider().synthesize(text, tmp_path / f"{name}_raw.wav")
    return audio.normalise(raw, tmp_path / f"{name}.wav", {})


@requires_ffmpeg
class TestAssemblyWithAudio:
    def asm(self, tmp_path):
        return VideoAssembler({"storage_path": str(tmp_path / "st"), "render_fps": 10})

    def test_no_audio_means_no_audio_stream(self, tmp_path):
        out = self.asm(tmp_path).assemble({"a": frames_for(tmp_path, "a", 10), "b": frames_for(tmp_path, "b", 10, "blue")})
        assert stream_types(out) == ["video"]

    def test_mixed_shots_get_audio_everywhere_and_stay_in_sync(self, tmp_path):
        wav = dialogue_wav(tmp_path, "a", "A short line of speech")
        out = self.asm(tmp_path).assemble(
            {"a": frames_for(tmp_path, "a", 20), "b": frames_for(tmp_path, "b", 20, "blue")}, shot_audio={"a": wav})
        assert stream_types(out) == ["audio", "video"]
        assert duration(out) == pytest.approx(4.0, abs=0.15)

    def test_audio_is_actually_in_its_shot(self, tmp_path):
        wav = dialogue_wav(tmp_path, "b", "A short line of speech")
        out = self.asm(tmp_path).assemble(
            {"a": frames_for(tmp_path, "a", 20), "b": frames_for(tmp_path, "b", 20, "blue")}, shot_audio={"b": wav})

        def loudness(start, end):
            r = subprocess.run(["ffmpeg", "-ss", str(start), "-to", str(end), "-i", str(out), "-af", "volumedetect",
                                "-f", "null", "-"], capture_output=True, text=True)
            line = [l for l in r.stderr.splitlines() if "max_volume" in l][0]
            return float(line.split("max_volume:")[1].split("dB")[0])

        assert loudness(0.1, 1.9) < -60       # shot a: silence
        assert loudness(2.1, 3.0) > -30       # shot b: speech

    def test_audio_longer_than_picture_is_trimmed(self, tmp_path):
        wav = dialogue_wav(tmp_path, "a", "word " * 40)
        out = self.asm(tmp_path).assemble({"a": frames_for(tmp_path, "a", 10)}, shot_audio={"a": wav})
        assert duration(out) == pytest.approx(1.0, abs=0.15)


# ---------------------------------------------------------------------------
# timeline
# ---------------------------------------------------------------------------

class TestTimelineAudio:
    def shots(self):
        return [{"shot_id": "a", "duration_seconds": 2.0, "dialogue": [{"character": "Ann", "line": "Hi."}]},
                {"shot_id": "b", "duration_seconds": 3.0}]

    def test_audio_track_with_gap_for_silent_shot(self, tmp_path):
        import opentimelineio as otio

        segs = {"a": tmp_path / "a.mp4", "b": tmp_path / "b.mp4"}
        out = export_timeline(self.shots(), segs, tmp_path / "t.otio", fps=24, shot_audio={"a": tmp_path / "a.wav"})
        t = otio.adapters.read_from_file(str(out))
        kinds = {tr.kind: tr for tr in t.tracks}
        assert [type(c).__name__ for c in kinds["Audio"]] == ["Clip", "Gap"]
        assert kinds["Audio"].duration() == kinds["Video"].duration()
        assert [dict(d) for d in kinds["Audio"][0].metadata["blazeng"]["dialogue"]] == [{"character": "Ann", "line": "Hi."}]
        assert kinds["Video"][0].metadata["blazeng"]["dialogue"][0]["line"] == "Hi."

    def test_no_audio_no_audio_track(self, tmp_path):
        import opentimelineio as otio

        segs = {"a": tmp_path / "a.mp4", "b": tmp_path / "b.mp4"}
        t = otio.adapters.read_from_file(str(export_timeline(self.shots(), segs, tmp_path / "t.otio")))
        assert [tr.kind for tr in t.tracks] == ["Video"]

    def test_skipped_shot_keeps_tracks_aligned(self, tmp_path):
        import opentimelineio as otio

        segs = {"b": tmp_path / "b.mp4"}  # shot a failed to render
        t = otio.adapters.read_from_file(str(export_timeline(
            self.shots(), segs, tmp_path / "t.otio", shot_audio={"b": tmp_path / "b.wav"})))
        assert all(len(tr) == 1 for tr in t.tracks)


def test_timeline_shots_uses_rendered_frame_count_and_world_state(world):
    plan = ProductionPlan("p", [Shot("s1", "stale", duration_seconds=4.0)])
    world.add_shot("s1", Shot("s1", "fresh", dialogue=[{"character": "Ann", "line": "Hi."}], duration_seconds=7.0).to_metadata())
    [d] = timeline_shots(plan, {"s1": [Path(f"f{i}.png") for i in range(30)]}, 10, world)
    assert d["duration_seconds"] == 3.0 and d["scene_description"] == "fresh" and d["dialogue"][0]["line"] == "Hi."
    [d] = timeline_shots(plan, {}, 10, world)          # nothing rendered: fall back to the stored duration
    assert d["duration_seconds"] == 7.0
    [d] = timeline_shots(plan, {}, 10, None)           # no world state: fall back to the plan
    assert d["duration_seconds"] == 4.0
