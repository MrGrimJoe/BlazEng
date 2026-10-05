"""Voice selection: per-character assignment, Piper voice folders, arbitrary TTS commands, CLI."""

import argparse
import shutil
import stat
import sys
import textwrap
from pathlib import Path

import pytest

from src import cli
from src.integrations import audio
from src.providers.speech_providers import (
    CommandSpeechProvider, DummySpeechProvider, PiperSpeechProvider, SpeechError, get_speech_provider,
    piper_voices_dir,
)

HAVE_ESPEAK = shutil.which("espeak-ng") is not None
POSIX = sys.platform != "win32"


class TestAssignments:
    def test_explicit_assignment_beats_automatic_pick(self):
        p = get_speech_provider({"speech_provider": "dummy", "voices": {"Ann": "high", "bob": "low"}})
        assert p.voice_for("Ann") == "high" and p.voice_for(" ANN ") == "high"
        assert p.voice_for("Bob") == "low"

    def test_unlisted_characters_still_get_a_stable_voice(self):
        p = get_speech_provider({"speech_provider": "dummy", "voices": {"Ann": "high"}})
        assert p.voice_for("Zed") in p.voices() and p.voice_for("Zed") == p.voice_for("Zed")

    def test_assignments_do_not_leak_between_providers(self):
        a = get_speech_provider({"speech_provider": "dummy", "voices": {"Ann": "high"}})
        b = get_speech_provider({"speech_provider": "dummy"})
        assert a.voice_for("Ann") == "high" and b.assignments == {}

    def test_assignment_works_for_provider_without_a_pool(self, tmp_path):
        p = get_speech_provider({"speech_provider": "dummy", "voices": {"Ann": "mid"}})
        p.voices = lambda: []
        assert p.voice_for("Ann") == "mid" and p.voice_for("Bob") is None


def make_fake_piper(tmp_path) -> Path:
    """A stand-in for the piper CLI: same flags, writes a WAV whose length follows the text."""
    script = tmp_path / "piper"
    script.write_text(textwrap.dedent(f"""\
        #!{sys.executable}
        import sys, wave, json
        a = sys.argv[1:]
        model, out = a[a.index("-m") + 1], a[a.index("-f") + 1]
        speaker = a[a.index("-s") + 1] if "-s" in a else "none"
        text = sys.stdin.read()
        with wave.open(out, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
            w.writeframes(b"\\x00\\x10" * (2205 * max(1, len(text.split()))))
        open(out + ".meta", "w").write(json.dumps({{"model": model, "speaker": speaker, "text": text}}))
        """))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


@pytest.mark.skipif(not POSIX, reason="uses a shell-style script as a fake piper")
class TestPiper:
    @pytest.fixture
    def setup(self, tmp_path):
        vdir = tmp_path / "voices"
        vdir.mkdir()
        for n in ("amy", "ryan"):
            (vdir / f"{n}.onnx").write_bytes(b"x")
            (vdir / f"{n}.onnx.json").write_text("{}")
        return PiperSpeechProvider(vdir, binary=str(make_fake_piper(tmp_path))), vdir

    def meta(self, wav):
        import json
        return json.loads(Path(str(wav) + ".meta").read_text())

    def test_lists_voices_from_folder(self, setup):
        p, _ = setup
        assert p.voices() == ["amy", "ryan"]

    def test_named_voice_selects_that_model(self, setup, tmp_path):
        p, vdir = setup
        m = self.meta(p.synthesize("Hello there friend", tmp_path / "o.wav", "ryan"))
        assert m["model"] == str(vdir / "ryan.onnx") and m["text"] == "Hello there friend"

    def test_speaker_suffix_for_multispeaker_models(self, setup, tmp_path):
        p, vdir = setup
        m = self.meta(p.synthesize("Hi", tmp_path / "o.wav", "amy:3"))
        assert m["speaker"] == "3" and m["model"] == str(vdir / "amy.onnx")

    def test_path_to_a_model_anywhere_works(self, setup, tmp_path):
        p, _ = setup
        mine = tmp_path / "elsewhere" / "my_voice.onnx"
        mine.parent.mkdir()
        mine.write_bytes(b"x")
        assert self.meta(p.synthesize("Hi", tmp_path / "o.wav", str(mine)))["model"] == str(mine)

    def test_unknown_voice_explains_what_exists(self, setup, tmp_path):
        p, _ = setup
        with pytest.raises(SpeechError, match=r"not found.*amy"):
            p.synthesize("Hi", tmp_path / "o.wav", "nobody")

    def test_no_voice_given_uses_first_available(self, setup, tmp_path):
        p, vdir = setup
        assert self.meta(p.synthesize("Hi", tmp_path / "o.wav"))["model"] == str(vdir / "amy.onnx")

    def test_empty_folder_gives_actionable_error(self, tmp_path):
        (tmp_path / "v").mkdir()
        p = PiperSpeechProvider(tmp_path / "v", binary=str(make_fake_piper(tmp_path)))
        with pytest.raises(SpeechError, match=r"No Piper voices found"):
            p.synthesize("Hi", tmp_path / "o.wav")

    def test_missing_binary(self, tmp_path):
        with pytest.raises(SpeechError, match="not found"):
            PiperSpeechProvider(tmp_path, binary="no-such-piper")

    def test_factory_uses_configured_dir(self, setup, tmp_path):
        _, vdir = setup
        p = get_speech_provider({"speech_provider": "piper", "piper_voices_dir": str(vdir),
                                 "piper_path": str(tmp_path / "piper"), "voices": {"Ann": "amy"}})
        assert p.voice_for("Ann") == "amy"

    def test_default_dir_sits_next_to_storage(self):
        assert piper_voices_dir({"storage_path": "/data/blazeng/storage"}) == Path("/data/blazeng/voices")


@pytest.mark.skipif(not HAVE_ESPEAK, reason="needs espeak-ng")
class TestCommandProvider:
    def test_real_tts_through_an_arbitrary_command(self, tmp_path):
        p = CommandSpeechProvider(["espeak-ng", "-v", "{voice}", "-f", "{text_file}", "-w", "{out}"], voices=["en-us"])
        wav = p.synthesize("Hello from a custom command", tmp_path / "o.wav", "en-us")
        assert audio.wav_duration(wav) > 0.5

    def test_text_placeholder_and_unicode(self, tmp_path):
        p = CommandSpeechProvider(["espeak-ng", "-w", "{out}", "--", "{text}"])
        assert audio.wav_duration(p.synthesize("héllo wörld", tmp_path / "o.wav")) > 0.3

    def test_failure_reported(self, tmp_path):
        p = CommandSpeechProvider([sys.executable, "-c", "import sys; sys.exit(3)"])
        with pytest.raises(SpeechError, match="failed"):
            p.synthesize("x", tmp_path / "o.wav")

    def test_command_that_writes_nothing_is_an_error(self, tmp_path):
        p = CommandSpeechProvider([sys.executable, "-c", "pass"])
        with pytest.raises(SpeechError, match="no audio"):
            p.synthesize("x", tmp_path / "o.wav")

    def test_bad_placeholder(self, tmp_path):
        p = CommandSpeechProvider([sys.executable, "-c", "pass", "{nope}"])
        with pytest.raises(SpeechError, match="placeholder"):
            p.synthesize("x", tmp_path / "o.wav")

    def test_braces_in_spoken_text_are_not_treated_as_placeholders(self, tmp_path):
        p = CommandSpeechProvider(["espeak-ng", "-f", "{text_file}", "-w", "{out}"])
        assert audio.wav_duration(p.synthesize("the set {a, b} is small", tmp_path / "o.wav")) > 0.3

    def test_rejects_empty_or_missing_command(self):
        with pytest.raises(SpeechError):
            CommandSpeechProvider([])
        with pytest.raises(SpeechError, match="not found"):
            CommandSpeechProvider(["definitely-not-a-program"])

    def test_voice_value_is_passed_through_as_is(self, tmp_path):
        script = tmp_path / "t.py"
        script.write_text("import sys, shutil\nshutil.copy(sys.argv[1], sys.argv[2])\nopen(sys.argv[2] + '.voice', 'w').write(sys.argv[3])\n")
        src = tmp_path / "src.wav"
        DummySpeechProvider().synthesize("hello", src)
        p = CommandSpeechProvider([sys.executable, str(script), str(src), "{out}", "{voice}"])
        out = p.synthesize("x", tmp_path / "o.wav", "/path/to/my_sample.wav")
        assert Path(str(out) + ".voice").read_text() == "/path/to/my_sample.wav"


class TestFactoryAndConfig:
    def test_command_provider_needs_a_command(self):
        with pytest.raises(SpeechError):
            get_speech_provider({"speech_provider": "command"})

    def test_command_provider_lists_assigned_voices(self):
        p = get_speech_provider({"speech_provider": "command", "speech_command": [sys.executable],
                                 "voices": {"Ann": "/v/ann.wav"}})
        assert p.voices() == ["/v/ann.wav"] and p.voice_for("Ann") == "/v/ann.wav"


class TestCli:
    def args(self, voice):
        return argparse.Namespace(storage=None, renderer=None, engine=None, fps=None, resolution=None,
                                  dummy=False, validate=False, speech=None, voice=voice)

    def test_voice_flags_merge_into_config(self):
        cfg = cli.apply_overrides({"voices": {"Zed": "x"}}, self.args(["Ann=amy", "Bob = ryan:2"]))
        assert cfg["voices"] == {"Zed": "x", "Ann": "amy", "Bob": "ryan:2"}

    @pytest.mark.parametrize("bad", ["Ann", "=amy", "Ann=", " = "])
    def test_bad_voice_flag(self, bad):
        with pytest.raises(argparse.ArgumentTypeError, match="NAME=VOICE"):
            cli.apply_overrides({}, self.args([bad]))

    def test_voices_command_lists_pool_and_assignments(self, tmp_path, capsys):
        import yaml
        cfg = tmp_path / "c.yaml"
        cfg.write_text(yaml.safe_dump({"storage_path": str(tmp_path / "s"), "speech_provider": "dummy",
                                       "voices": {"Ann": "high"}}))
        assert cli.main(["voices", "-c", str(cfg)]) == 0
        out = capsys.readouterr().out
        assert "DummySpeechProvider" in out and "low, mid, high" in out and "ann -> high" in out

    def test_voices_command_with_no_provider(self, tmp_path, capsys):
        import yaml
        cfg = tmp_path / "c.yaml"
        cfg.write_text(yaml.safe_dump({"storage_path": str(tmp_path / "s"), "speech_provider": "none"}))
        assert cli.main(["voices", "-c", str(cfg)]) == 0
        assert "No speech provider" in capsys.readouterr().out

    def test_voices_command_reports_config_errors(self, tmp_path, capsys):
        import yaml
        cfg = tmp_path / "c.yaml"
        cfg.write_text(yaml.safe_dump({"storage_path": str(tmp_path / "s"), "speech_provider": "piper",
                                       "piper_path": "no-such-piper"}))
        assert cli.main(["voices", "-c", str(cfg)]) == cli.EXIT_FAILED
        assert "not found" in capsys.readouterr().err

    def test_doctor_reports_piper_voice_count(self, tmp_path):
        v = tmp_path / "voices"
        v.mkdir()
        (v / "amy.onnx").write_bytes(b"x")
        rows = cli.run_checks({"storage_path": str(tmp_path / "s"), "speech_provider": "piper",
                               "piper_voices_dir": str(v), "piper_path": "no-such-piper"})
        speech = [r for r in rows if r[1] == "speech"][0]
        assert speech[0] == cli.WARN and "1 voice(s)" in speech[2] and "NOT found" in speech[2]
