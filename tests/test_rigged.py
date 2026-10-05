"""Rigged 3D characters: scene description, lip-sync data, and real Blender renders."""

import json
import shutil
import subprocess
import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image, ImageChops

from src.core.director.director import Director, ProductionPlan, Shot
from src.core.orchestrator.orchestrator import PipelineOrchestrator
from src.core.scene_composer.scene_composer import SceneComposer
from src.core.validator.consistency import frame_shows_character, palette_from_image
from src.core.world_state.world_state import WorldStateManager
from src.integrations import audio
from src.providers.dummy_provider import DummyImageProvider, DummyTextProvider
from src.providers.speech_providers import DummySpeechProvider, EspeakSpeechProvider

HAVE_BLENDER = bool(shutil.which("blender") and shutil.which("xvfb-run"))
HAVE_FFMPEG = shutil.which("ffmpeg") is not None
needs_blender = pytest.mark.skipif(not HAVE_BLENDER, reason="needs blender and xvfb-run on PATH")


def solid_figure(path: Path, skin, shirt, trousers, w=64, h=128) -> Path:
    """A standing figure in flat colours, laid out like the dummy provider's silhouettes."""
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    im.paste(Image.new("RGBA", (24, 24), skin + (255,)), (20, 4))
    im.paste(Image.new("RGBA", (36, 44), shirt + (255,)), (14, 34))
    im.paste(Image.new("RGBA", (24, 44), trousers + (255,)), (20, 80))
    im.save(path)
    return path


# ---------------------------------------------------------------------------
# palette + envelope
# ---------------------------------------------------------------------------

class TestPalette:
    def test_recovers_body_part_colours(self, tmp_path):
        p = palette_from_image(solid_figure(tmp_path / "a.png", (230, 180, 140), (30, 140, 40), (40, 40, 160)))
        near = lambda got, want: all(abs(g * 255 - w) < 3 for g, w in zip(got, want))  # noqa: E731
        assert near(p["skin"], (230, 180, 140)) and near(p["shirt"], (30, 140, 40)) and near(p["trousers"], (40, 40, 160))

    def test_hair_is_darker_than_skin_when_art_has_none(self, tmp_path):
        p = palette_from_image(solid_figure(tmp_path / "a.png", (230, 180, 140), (30, 140, 40), (40, 40, 160)))
        assert sum(p["hair"]) < sum(p["skin"]) * 0.6

    def test_dummy_provider_art_round_trips(self, tmp_path):
        art = DummyImageProvider().generate_image("some character", tmp_path / "d.png")
        p = palette_from_image(art)
        assert set(p) == {"skin", "hair", "shirt", "trousers"} and p["shirt"] != p["trousers"]

    def test_empty_image_falls_back_instead_of_crashing(self, tmp_path):
        Image.new("RGBA", (32, 64), (0, 0, 0, 0)).save(tmp_path / "e.png")
        assert set(palette_from_image(tmp_path / "e.png")) == {"skin", "hair", "shirt", "trousers"}


@pytest.fixture
def speech_wav(tmp_path):
    raw = DummySpeechProvider().synthesize("Hello there, this is a line of dialogue.", tmp_path / "raw.wav")
    return audio.normalise(raw, tmp_path / "n.wav", {})


@pytest.mark.skipif(not HAVE_FFMPEG, reason="needs ffmpeg")
class TestEnvelope:
    def test_one_value_per_frame_in_range(self, speech_wav):
        env = audio.envelope(speech_wav, 24)
        assert len(env) == int(audio.wav_duration(speech_wav) * 24)
        assert all(0.0 <= v <= 1.0 for v in env) and max(env) > 0.8

    def test_silence_stays_closed(self, tmp_path):
        import wave

        p = tmp_path / "s.wav"
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(audio.SAMPLE_RATE)  # noqa: E702
            w.writeframes(b"\x00\x00" * audio.SAMPLE_RATE)
        assert set(audio.envelope(p, 24)) == {0.0}

    def test_span_and_padding(self, speech_wav):
        full = audio.envelope(speech_wav, 24)
        part = audio.envelope(speech_wav, 24, start=0.25, end=0.75)
        assert len(part) == 12 and part == pytest.approx(full[6:18], abs=0.35)
        assert len(audio.envelope(speech_wav, 24, frames=200)) == 200
        assert len(audio.envelope(speech_wav, 24, frames=3)) == 3

    def test_line_offsets_match_join(self, tmp_path, speech_wav):
        joined = audio.join_lines([speech_wav, speech_wav], tmp_path / "j.wav", gap_seconds=0.3)
        (a0, a1), (b0, b1) = audio.line_offsets([speech_wav, speech_wav], 0.3)
        assert a0 == 0 and b0 == pytest.approx(a1 + 0.3) and b1 == pytest.approx(audio.wav_duration(joined), abs=0.01)


# ---------------------------------------------------------------------------
# scene description
# ---------------------------------------------------------------------------

@pytest.fixture
def cfg(tmp_path):
    return {"storage_path": str(tmp_path / "s"), "render_fps": 10, "render_width": 320, "render_height": 180}


@pytest.fixture
def arts(tmp_path):
    return {"Ann": solid_figure(tmp_path / "ann.png", (230, 180, 140), (30, 140, 40), (40, 40, 160)),
            "Bob": solid_figure(tmp_path / "bob.png", (200, 150, 110), (200, 90, 20), (60, 60, 60))}


def scene_json(composer, shot, arts, speech=None):
    return json.loads(SceneComposer.scene_json_path(composer.compose_shot(shot, arts, speech=speech)).read_text())


class TestRiggedSceneDescription:
    def shot(self, **kw):
        return Shot("s1", "two people", ["Ann", "Bob"], duration_seconds=2.0, **kw)

    def test_default_is_unchanged_image_planes(self, cfg, arts):
        data = scene_json(SceneComposer(cfg), self.shot(), arts)
        assert "character_style" not in data and "palette" not in data["characters"][0]

    @pytest.mark.skipif(not HAVE_FFMPEG, reason="needs ffmpeg")
    def test_rigged_description(self, cfg, arts, speech_wav):
        speech = {"wav": speech_wav, "lines": [{"character": "Ann", "start": 0.0, "end": 1.0}]}
        data = scene_json(SceneComposer({**cfg, "blender_characters": "rigged"}), self.shot(), arts, speech)
        assert data["version"] == 1 and data["character_style"] == "rigged" and data["frames"] == 20 and data["fps"] == 10
        ann, bob = data["characters"]
        assert ann["motion"] == "talk" and bob["motion"] == "idle"
        assert ann["model"] == {"type": "procedural"} and len(ann["palette"]["shirt"]) == 3
        assert ann["palette"]["shirt"][1] > ann["palette"]["shirt"][0]          # Ann's green shirt
        assert set(data["mouth"]) == {"Ann"} and len(data["mouth"]["Ann"]) == 20  # Bob never speaks
        assert max(data["mouth"]["Ann"][:10]) > 0.5 and set(data["mouth"]["Ann"][11:]) == {0.0}  # line ends at 1.0s
        assert data["dialogue"] == [{"character": "Ann", "start": 0.0, "end": 1.0}]

    def test_no_speech_means_no_mouth_curves(self, cfg, arts):
        data = scene_json(SceneComposer({**cfg, "blender_characters": "rigged"}), self.shot(), arts)
        assert "mouth" not in data and [c["motion"] for c in data["characters"]] == ["idle", "idle"]

    @pytest.mark.parametrize("action", ["They walk towards each other", "Ann approaches", "He enters the room"])
    def test_walking_actions_make_everyone_walk_towards_the_middle(self, cfg, arts, action):
        data = scene_json(SceneComposer({**cfg, "blender_characters": "rigged"}), self.shot(action=action), arts)
        for c in data["characters"]:
            assert c["motion"] == "walk" and abs(c["walk_to"]) < abs(c["x"])

    def test_user_model_is_used_for_that_character_only(self, cfg, arts, tmp_path):
        model = tmp_path / "ann.glb"
        model.write_bytes(b"glTF")
        data = scene_json(SceneComposer({**cfg, "blender_characters": "rigged", "character_models": {"ann": str(model)}}),
                          self.shot(), arts)
        assert data["characters"][0]["model"] == {"type": "file", "path": str(model.resolve())}
        assert data["characters"][1]["model"] == {"type": "procedural"}

    def test_speaker_names_match_case_insensitively(self, cfg, arts, speech_wav):
        speech = {"wav": speech_wav, "lines": [{"character": "ann", "start": 0.0, "end": 1.0},
                                                {"character": "Nobody", "start": 1.0, "end": 1.5}]}
        data = scene_json(SceneComposer({**cfg, "blender_characters": "rigged"}), self.shot(), arts, speech)
        assert set(data["mouth"]) == {"Ann"}


# ---------------------------------------------------------------------------
# orchestration order
# ---------------------------------------------------------------------------

def test_speech_is_produced_before_scenes_are_composed():
    d = Director(DummyTextProvider(), None)
    plan = ProductionPlan("p", [Shot("a", "x", ["Ann"], dialogue=[{"character": "Ann", "line": "Hi."}])])
    order = [t.task_type for t in d.create_task_schedule(plan)]
    assert order.index("speak") < order.index("compose_scene") < order.index("render")


@pytest.mark.skipif(not HAVE_FFMPEG, reason="needs ffmpeg")
def test_orchestrator_hands_speech_timing_to_the_composer(tmp_path):
    w = WorldStateManager({"storage_path": str(tmp_path / "s")})
    w.add_shot("s1", Shot("s1", "x", ["Ann", "Bob"], duration_seconds=1.0, dialogue=[
        {"character": "Ann", "line": "First line of speech."}, {"character": "Bob", "line": "A reply."}]).to_metadata())
    composer = MagicMock()
    composer.compose_shot.return_value = tmp_path / "s1.tscn"
    orch = PipelineOrchestrator({"storage_path": str(tmp_path / "s")}, w, MagicMock(), composer, MagicMock(), MagicMock(),
                                renderer=MagicMock(), speech_provider=DummySpeechProvider())
    orch._asset_paths = {"Ann": tmp_path / "a.png", "Bob": tmp_path / "b.png"}
    orch._run_speak(MagicMock(task_type="speak", shot_id="s1"))
    orch._run_compose_scene(MagicMock(task_type="compose_scene", shot_id="s1"))
    speech = composer.compose_shot.call_args.kwargs["speech"]
    assert [l["character"] for l in speech["lines"]] == ["Ann", "Bob"]
    assert speech["lines"][1]["start"] > speech["lines"][0]["end"] and Path(speech["wav"]).exists()
    w.close()


# ---------------------------------------------------------------------------
# real Blender
# ---------------------------------------------------------------------------

@pytest.fixture
def linux_display(monkeypatch):
    from src.integrations import display

    monkeypatch.setattr(display, "needs_virtual_display", lambda: True)


def render(cfg, shot, arts, speech=None, engine="workbench"):
    from src.integrations.blender.renderer import BlenderRenderer

    config = {**cfg, "blender_characters": "rigged", "blender_engine": engine}
    scene = SceneComposer(config).compose_shot(shot, arts, speech=speech)
    frames = BlenderRenderer(config).render_shot(scene, shot.shot_id, duration_seconds=shot.duration_seconds)
    report = json.loads((frames[0].parent / "rig_report.json").read_text())
    return frames, report


def region_diff(a: Path, b: Path, box) -> int:
    with Image.open(a) as x, Image.open(b) as y:
        d = ImageChops.difference(x.convert("RGB").crop(box), y.convert("RGB").crop(box))
    return sum(1 for px in (getattr(d, 'get_flattened_data', None) or d.getdata)() if max(px) > 24)


@needs_blender
@pytest.mark.usefixtures("linux_display")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="needs ffmpeg")
class TestRealRiggedRender:
    @pytest.fixture
    def talk(self, cfg, arts, tmp_path):
        raw = EspeakSpeechProvider().synthesize("Hello there, detective. Look at this clue.", tmp_path / "raw.wav") \
            if shutil.which("espeak-ng") else DummySpeechProvider().synthesize("Hello there, detective. Look at this", tmp_path / "raw.wav")
        wav = audio.normalise(raw, tmp_path / "n.wav", {})
        dur = audio.wav_duration(wav)
        shot = Shot("shot_001", "two people talk", ["Ann", "Bob"], camera_angle="wide shot", lighting="bright daylight",
                    duration_seconds=round(dur + 0.4, 2))
        speech = {"wav": wav, "lines": [{"character": "Ann", "start": 0.0, "end": dur}]}
        frames, report = render(cfg, shot, arts, speech)
        return frames, report, shot

    def test_rig_has_a_full_skeleton_and_body(self, talk):
        _, report, _ = talk
        ann = report["characters"]["Ann"]
        assert ann["model"] == "procedural" and ann["motion"] == "talk"
        assert {"hips", "spine", "head", "jaw", "upper_arm.L", "lower_leg.R"} <= set(ann["bones"])
        assert len(ann["meshes"]) >= 15

    def test_frame_count_matches_duration(self, talk):
        frames, report, shot = talk
        assert len(frames) == report["frames"] == round(shot.duration_seconds * 10)

    def test_jaw_follows_the_speech_loudness_for_the_speaker_only(self, talk, cfg):
        _, report, _ = talk
        jaw = report["characters"]["Ann"]["jaw"]
        assert max(jaw) > 0.3 and min(jaw) == 0.0           # opens and closes
        assert set(report["characters"]["Bob"]["jaw"]) == {0.0}
        loud = max(range(len(jaw)), key=jaw.__getitem__)
        assert jaw[loud] == pytest.approx(0.55, abs=0.12)

    def test_speech_changes_only_the_speaker(self, talk, cfg, arts, tmp_path):
        """Same shot rendered with and without the dialogue: only Ann's half of the picture may differ."""
        frames, report, shot = talk
        jaw = report["characters"]["Ann"]["jaw"]
        i = max(range(len(jaw)), key=jaw.__getitem__)
        talking = tmp_path / "talking.png"
        shutil.copy(frames[i], talking)
        silent_frames, _ = render(cfg, shot, arts, speech=None)
        w, h = Image.open(talking).size
        assert region_diff(talking, silent_frames[i], (w // 2, 0, w, h)) == 0      # Bob: pixel-identical
        assert region_diff(talking, silent_frames[i], (0, 0, w // 2, h)) > 30      # Ann: mouth open, gesturing

    def test_same_body_and_colours_in_every_shot(self, talk, arts):
        frames, _, _ = talk
        for f in (frames[0], frames[len(frames) // 2], frames[-1]):
            assert frame_shows_character(f, arts["Ann"])[0] and frame_shows_character(f, arts["Bob"])[0]

    @pytest.mark.parametrize("lighting", ["bright daylight", "dim", "dark night"])
    def test_characters_keep_their_colours_under_any_lighting_in_eevee(self, cfg, arts, lighting):
        shot = Shot("shot_001", "x", ["Ann", "Bob"], lighting=lighting, duration_seconds=0.5)
        frames, _ = render(cfg, shot, arts, engine="eevee")
        assert frame_shows_character(frames[-1], arts["Ann"])[0], lighting
        assert frame_shows_character(frames[-1], arts["Bob"])[0], lighting
        magenta = Path(arts["Ann"]).with_name("zed.png")
        Image.new("RGBA", (64, 128), (230, 20, 200, 255)).save(magenta)
        assert not frame_shows_character(frames[-1], magenta)[0]

    def test_walking_moves_toward_the_middle_and_legs_swing(self, cfg, arts):
        shot = Shot("shot_001", "x", ["Ann", "Bob"], action="They walk towards each other", duration_seconds=1.5)
        frames, report = render(cfg, shot, arts)
        ann, bob = report["characters"]["Ann"], report["characters"]["Bob"]
        assert ann["motion"] == bob["motion"] == "walk"
        assert ann["root_x"][-1] > ann["root_x"][0] + 0.5 and bob["root_x"][-1] < bob["root_x"][0] - 0.5
        # Each one faces the way it is going (not walking backwards): Ann travels +x, Bob -x.
        assert all(f[0] > 0.7 for f in ann["facing"]), ann["facing"]
        assert all(f[0] < -0.7 for f in bob["facing"]), bob["facing"]
        for c in (ann, bob):  # still standing up while walking (a 3-unit figure: head near 2.9)
            assert all(2.6 < z < 3.2 for z in c["head_height"]), c["head_height"]
        w, h = Image.open(frames[0]).size
        assert region_diff(frames[0], frames[len(frames) // 4], (0, h // 2, w, h)) > 100  # legs/feet region changes

    def test_idle_characters_stay_put_but_are_alive(self, cfg, arts):
        shot = Shot("shot_001", "x", ["Ann"], duration_seconds=2.0)
        frames, report = render(cfg, shot, arts)
        assert len(set(report["characters"]["Ann"]["root_x"])) == 1
        assert all(2.6 < z < 3.2 for z in report["characters"]["Ann"]["head_height"])
        assert all(f[1] < -0.8 for f in report["characters"]["Ann"]["facing"])   # idle: looking at the camera
        assert region_diff(frames[0], frames[len(frames) // 2], (0, 0, *Image.open(frames[0]).size)) > 0

    def test_old_image_plane_scenes_still_render_and_have_no_rig_report(self, cfg, arts):
        from src.integrations.blender.renderer import BlenderRenderer

        shot = Shot("shot_001", "x", ["Ann"], duration_seconds=0.5)
        scene = SceneComposer(cfg).compose_shot(shot, arts)
        frames = BlenderRenderer({**cfg, "blender_engine": "workbench"}).render_shot(scene, "shot_001", duration_seconds=0.5)
        assert frames and not (frames[0].parent / "rig_report.json").exists()


# ---------------------------------------------------------------------------
# bring your own model (.glb)
# ---------------------------------------------------------------------------

_MAKE_GLB = textwrap.dedent('''
    import bpy, sys
    out = sys.argv[sys.argv.index("--") + 1]
    bpy.ops.wm.read_factory_settings(use_empty=True)
    mat = bpy.data.materials.new("red"); mat.use_nodes = True
    mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.9, 0.05, 0.05, 1)
    mat.diffuse_color = (0.9, 0.05, 0.05, 1)
    bpy.ops.mesh.primitive_cylinder_add(radius=0.3, depth=1.0, location=(0, 0, 0.5))
    body = bpy.context.active_object; body.name = "Body"; body.data.materials.append(mat)
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.35, location=(0, 0, 1.35))
    head = bpy.context.active_object; head.name = "Head"; head.data.materials.append(mat)
    head.shape_key_add(name="Basis")
    jaw = head.shape_key_add(name="jawOpen")
    for v in jaw.data:
        if v.co.z < 0: v.co.z -= 0.25
    bpy.ops.object.armature_add(location=(0, 0, 0))
    arm = bpy.context.active_object; arm.name = "Armature"
    body.parent = arm; head.parent = arm
    arm.animation_data_create()
    act = bpy.data.actions.new("Idle"); arm.animation_data.action = act
    arm.pose.bones[0].location = (0, 0, 0); arm.pose.bones[0].keyframe_insert("location", frame=1)
    arm.pose.bones[0].location = (0, 0, 0.1); arm.pose.bones[0].keyframe_insert("location", frame=12)
    bpy.ops.export_scene.gltf(filepath=out, export_format="GLB", export_animations=True, export_morph=True)
''')


@pytest.fixture(scope="module")
def glb(tmp_path_factory):
    d = tmp_path_factory.mktemp("glb")
    script, out = d / "make.py", d / "hero.glb"
    script.write_text(_MAKE_GLB)
    r = subprocess.run(["blender", "--background", "--python", str(script), "--", str(out)],
                       capture_output=True, text=True, timeout=180)
    if not out.exists():
        pytest.skip(f"could not build a test .glb: {r.stdout[-300:]} {r.stderr[-300:]}")
    return out


@needs_blender
@pytest.mark.usefixtures("linux_display")
@pytest.mark.skipif(not HAVE_FFMPEG, reason="needs ffmpeg")
class TestBringYourOwnModel:
    def test_glb_character_is_used_with_its_mouth_and_animation(self, cfg, arts, glb, speech_wav):
        shot = Shot("shot_001", "x", ["Ann", "Bob"], duration_seconds=round(audio.wav_duration(speech_wav) + 0.3, 2))
        speech = {"wav": speech_wav, "lines": [{"character": "Ann", "start": 0.0, "end": audio.wav_duration(speech_wav)}]}
        cfg = {**cfg, "character_models": {"Ann": str(glb)}}
        frames, report = render(cfg, shot, arts, speech, engine="eevee")
        ann, bob = report["characters"]["Ann"], report["characters"]["Bob"]
        assert ann["model"] == "file" and bob["model"] == "procedural"
        assert ann["mouth_shape_keys"] == ["jawOpen"] and ann["keyed_mouth_values"] == report["frames"]
        assert max(ann["jaw"]) > 0.5
        assert "idle" in ann["action"].lower()
        assert all(f[1] < -0.9 for f in ann["facing"])                   # imported model also faces the camera
        assert ann["scale"] == pytest.approx(3.0 / 1.7, rel=0.15)         # scaled to the standard height
        assert frame_shows_character(frames[0], _red(arts["Ann"].parent))   # the model's own red is on screen

    def test_a_model_that_is_not_a_model_fails_cleanly(self, cfg, arts, tmp_path):
        from src.integrations.blender.renderer import BlenderRenderError

        bad = tmp_path / "bad.glb"
        bad.write_bytes(b"not a glb")
        shot = Shot("shot_001", "x", ["Ann"], duration_seconds=0.5)
        with pytest.raises(BlenderRenderError):
            render({**cfg, "character_models": {"Ann": str(bad)}}, shot, arts)


def _red(folder: Path) -> Path:
    p = folder / "red.png"
    Image.new("RGBA", (64, 128), (230, 12, 12, 255)).save(p)
    return p


# ---------------------------------------------------------------------------
# CLI + config
# ---------------------------------------------------------------------------

class TestCliFlags:
    def ns(self, **kw):
        import argparse

        base = dict(storage=None, renderer=None, engine=None, fps=None, resolution=None, dummy=False,
                    validate=False, speech=None, voice=[], characters=None, model=[])
        return argparse.Namespace(**{**base, **kw})

    def test_characters_flag(self):
        from src import cli

        assert cli.apply_overrides({}, self.ns(characters="rigged"))["blender_characters"] == "rigged"
        assert "blender_characters" not in cli.apply_overrides({}, self.ns())

    def test_model_flag_registers_model_and_switches_to_rigged(self, tmp_path):
        from src import cli

        glb = tmp_path / "ann.glb"
        glb.write_bytes(b"x")
        cfg = cli.apply_overrides({"character_models": {"Zed": "z.glb"}}, self.ns(model=[f"Ann={glb}"]))
        assert cfg["character_models"] == {"Zed": "z.glb", "Ann": str(glb)} and cfg["blender_characters"] == "rigged"

    @pytest.mark.parametrize("bad", ["Ann", "=x.glb", "Ann="])
    def test_bad_model_flag(self, bad):
        import argparse

        from src import cli

        with pytest.raises(argparse.ArgumentTypeError, match="NAME=FILE"):
            cli.apply_overrides({}, self.ns(model=[bad]))

    def test_missing_model_file_is_reported_up_front(self, tmp_path):
        import argparse

        from src import cli

        with pytest.raises(argparse.ArgumentTypeError, match="not found"):
            cli.apply_overrides({}, self.ns(model=[f"Ann={tmp_path / 'nope.glb'}"]))

    def test_composer_refuses_a_configured_model_that_does_not_exist(self, cfg, arts, tmp_path):
        from src.core.scene_composer.scene_composer import SceneComposerError

        composer = SceneComposer({**cfg, "blender_characters": "rigged",
                                  "character_models": {"Ann": str(tmp_path / "gone.glb")}})
        with pytest.raises(SceneComposerError, match="not found"):
            composer.compose_shot(Shot("s1", "x", ["Ann"]), arts)


@needs_blender
@pytest.mark.usefixtures("linux_display")
@pytest.mark.skipif(not (HAVE_FFMPEG and shutil.which("espeak-ng")), reason="needs ffmpeg and espeak-ng")
def test_full_cli_run_with_rigged_talking_characters(tmp_path, capsys):
    """Prompt -> plan with dialogue -> espeak speech -> rigged Blender render -> MP4 with audio + OTIO audio track."""
    import yaml

    from src import cli

    cfg = tmp_path / "c.yaml"
    cfg.write_text(yaml.safe_dump({"storage_path": str(tmp_path / "st"), "render_fps": 8,
                                   "render_width": 320, "render_height": 180, "blender_engine": "workbench"}))
    code = cli.main(["run", "A detective finds a clue.", "-c", str(cfg), "--dummy", "--speech", "espeak",
                     "--renderer", "blender", "--characters", "rigged", "--json"])
    import opentimelineio as otio

    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["failures"] == [] and out["audio_shots"]
    kinds = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", out["video"]],
                           capture_output=True, text=True).stdout.split()
    assert sorted(kinds) == ["audio", "video"]
    reports = list((tmp_path / "st" / "renders").glob("*/rig_report.json"))
    assert reports and all(max(json.loads(r.read_text())["characters"]["protagonist"]["jaw"]) > 0.3 for r in reports)
    timeline = otio.adapters.read_from_file(out["timeline"])  # keep a reference: OTIO children die with the timeline
    assert {t.kind for t in timeline.tracks} == {"Video", "Audio"}


@needs_blender
@pytest.mark.usefixtures("linux_display")
def test_imported_model_walks_forwards_too(cfg, arts, glb):
    shot = Shot("shot_001", "x", ["Ann", "Bob"], action="They walk towards each other", duration_seconds=1.0)
    _, report = render({**cfg, "character_models": {"Ann": str(glb)}}, shot, arts)
    ann = report["characters"]["Ann"]
    assert ann["model"] == "file" and ann["root_x"][-1] > ann["root_x"][0]
    assert all(f[0] > 0.9 for f in ann["facing"]), ann["facing"]
