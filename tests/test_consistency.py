"""Character consistency: reference-conditioned regeneration, drift check, frame validator."""

import shutil
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image

from src.core.asset_manager.asset_manager import AssetManager
from src.core.director.director import Shot
from src.core.scene_composer.scene_composer import SceneComposer
from src.core.validator.consistency import distinctive_hues, frame_shows_character, similarity
from src.core.validator.validator_manager import ValidatorManager
from src.providers.base import ImageProvider
from src.providers.dummy_provider import DummyImageProvider, DummyVisionProvider

HAVE_BLENDER = bool(shutil.which("blender") and shutil.which("xvfb-run"))


def solid(path: Path, color, size=(64, 128)) -> Path:
    Image.new("RGBA", size, color).save(path)
    return path


def two_tone(path: Path, top, bottom) -> Path:
    im = Image.new("RGBA", (64, 128), top)
    im.paste(Image.new("RGBA", (64, 64), bottom), (0, 64))
    im.save(path)
    return path


@pytest.fixture
def config(tmp_path):
    return {"storage_path": str(tmp_path / "storage")}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class TestSimilarity:
    def test_identical_images(self, tmp_path):
        a = two_tone(tmp_path / "a.png", (200, 30, 30, 255), (30, 30, 200, 255))
        assert similarity(a, a) == pytest.approx(1.0)

    def test_different_palettes_score_low(self, tmp_path):
        a = solid(tmp_path / "a.png", (220, 20, 20, 255))
        b = solid(tmp_path / "b.png", (20, 20, 220, 255))
        assert similarity(a, b) < 0.1

    def test_transparent_background_ignored(self, tmp_path):
        a = tmp_path / "a.png"
        im = Image.new("RGBA", (64, 128), (0, 0, 0, 0))
        im.paste(Image.new("RGBA", (20, 20), (220, 20, 20, 255)), (10, 10))
        im.save(a)
        b = solid(tmp_path / "b.png", (220, 20, 20, 255))
        assert similarity(a, b) > 0.9

    def test_fully_transparent_scores_zero(self, tmp_path):
        a = solid(tmp_path / "a.png", (0, 0, 0, 0))
        b = solid(tmp_path / "b.png", (220, 20, 20, 255))
        assert similarity(a, b) == 0.0


class TestFrameShowsCharacter:
    def test_present(self, tmp_path):
        ref = solid(tmp_path / "ref.png", (220, 30, 30, 255))
        frame = Image.new("RGB", (320, 180), (80, 80, 90))
        frame.paste(Image.new("RGB", (40, 80), (190, 25, 25)), (100, 50))
        frame.save(tmp_path / "f.png")
        ok, _ = frame_shows_character(tmp_path / "f.png", ref)
        assert ok

    def test_present_but_dimmed_by_lighting(self, tmp_path):
        ref = solid(tmp_path / "ref.png", (220, 30, 30, 255))
        frame = Image.new("RGB", (320, 180), (10, 10, 14))
        frame.paste(Image.new("RGB", (40, 80), (70, 8, 8)), (100, 50))
        frame.save(tmp_path / "f.png")
        assert frame_shows_character(tmp_path / "f.png", ref)[0]

    def test_missing(self, tmp_path):
        ref = solid(tmp_path / "ref.png", (220, 30, 30, 255))
        Image.new("RGB", (320, 180), (80, 80, 90)).save(tmp_path / "f.png")
        ok, why = frame_shows_character(tmp_path / "f.png", ref)
        assert not ok and "not found" in why

    def test_wrong_colour(self, tmp_path):
        ref = solid(tmp_path / "ref.png", (220, 30, 30, 255))
        frame = Image.new("RGB", (320, 180), (80, 80, 90))
        frame.paste(Image.new("RGB", (40, 80), (30, 30, 200)), (100, 50))
        frame.save(tmp_path / "f.png")
        assert not frame_shows_character(tmp_path / "f.png", ref)[0]

    def test_greyscale_character_has_nothing_to_check(self, tmp_path):
        ref = solid(tmp_path / "ref.png", (120, 120, 120, 255))
        assert distinctive_hues(ref) == []
        Image.new("RGB", (320, 180), (0, 0, 0)).save(tmp_path / "f.png")
        assert frame_shows_character(tmp_path / "f.png", ref)[0]


# ---------------------------------------------------------------------------
# provider contract
# ---------------------------------------------------------------------------

class TestDummyProviderReference:
    def test_reference_keeps_the_same_character(self, tmp_path):
        p = DummyImageProvider()
        ref = p.generate_image("a tall man in a red coat", tmp_path / "ref.png")
        out = p.generate_image("a tall man in a red coat, angry", tmp_path / "out.png", reference_image=ref)
        assert out.read_bytes() == ref.read_bytes()
        assert p.call_log[-1]["reference_image"] == str(ref)

    def test_without_reference_prompt_decides(self, tmp_path):
        p = DummyImageProvider()
        a = p.generate_image("alpha", tmp_path / "a.png")
        b = p.generate_image("omega", tmp_path / "b.png")
        assert a.read_bytes() != b.read_bytes()

    def test_missing_reference_file_is_ignored(self, tmp_path):
        p = DummyImageProvider()
        out = p.generate_image("alpha", tmp_path / "a.png", reference_image=tmp_path / "gone.png")
        assert out.exists()


def test_every_image_provider_accepts_reference_image():
    import inspect

    from src.providers import diffusers_provider, gemini_provider, openai_provider

    for cls in (
        ImageProvider, DummyImageProvider, gemini_provider.GeminiImageProvider,
        openai_provider.OpenAIImageProvider, diffusers_provider.DiffusersImageProvider,
    ):
        assert "reference_image" in inspect.signature(cls.generate_image).parameters, cls


# ---------------------------------------------------------------------------
# AssetManager
# ---------------------------------------------------------------------------

class TestAssetManagerConsistency:
    def test_first_version_has_no_reference(self, config):
        provider = DummyImageProvider()
        AssetManager(config, provider).get_asset("character", "Ann", "red hair")
        assert provider.call_log[0]["reference_image"] is None

    def test_new_version_is_generated_from_v1(self, config):
        provider = DummyImageProvider()
        am = AssetManager(config, provider)
        v1 = am.get_asset("character", "Ann", "red hair")
        v2 = am.get_asset("character", "Ann", "red hair, now wearing a hat")
        v3 = am.get_asset("character", "Ann", "red hair, wearing a coat")
        assert v2 != v1
        assert provider.call_log[1]["reference_image"] == str(v1)
        assert provider.call_log[2]["reference_image"] == str(v1)  # always the locked v1, no compounding
        assert am.locked_reference("character", "Ann") == v1
        assert v3.exists()

    def test_non_characters_are_not_reference_conditioned(self, config):
        provider = DummyImageProvider()
        am = AssetManager(config, provider)
        am.get_asset("object", "Lamp", "brass lamp")
        am.get_asset("object", "Lamp", "brass lamp, lit")
        assert provider.call_log[1]["reference_image"] is None

    def test_drift_retries_then_warns(self, config, tmp_path):
        red, blue = solid(tmp_path / "r.png", (220, 20, 20, 255)), solid(tmp_path / "b.png", (20, 20, 220, 255))
        calls = []

        class Drifting(ImageProvider):
            def generate_image(self, prompt, output_path, reference_image=None):
                calls.append(reference_image)
                shutil.copy(red if reference_image is None else blue, output_path)
                return output_path

        am = AssetManager(config, Drifting())
        am.get_asset("character", "Ann", "red")
        am.get_asset("character", "Ann", "red, new look")
        assert len(calls) == 1 + 3  # v1, then first try + 2 retries
        meta = AssetManager._read_meta(am.list_asset_versions("character", "Ann")[-1])
        assert meta["drift_warning"] is True and meta["similarity_to_reference"] < 0.5

    def test_drift_retry_can_recover(self, config, tmp_path):
        red, blue = solid(tmp_path / "r.png", (220, 20, 20, 255)), solid(tmp_path / "b.png", (20, 20, 220, 255))
        n = {"i": 0}

        class Recovering(ImageProvider):
            def generate_image(self, prompt, output_path, reference_image=None):
                n["i"] += 1
                shutil.copy(blue if n["i"] == 2 else red, output_path)  # only the first retry-able try drifts
                return output_path

        am = AssetManager(config, Recovering())
        am.get_asset("character", "Ann", "red")
        am.get_asset("character", "Ann", "red, new look")
        meta = AssetManager._read_meta(am.list_asset_versions("character", "Ann")[-1])
        assert "drift_warning" not in meta and meta["similarity_to_reference"] > 0.9

    def test_unmeasurable_output_does_not_block(self, config):
        provider = MagicMock(spec=ImageProvider)  # writes no file at all
        am = AssetManager(config, provider)
        am.get_asset("character", "Ann", "red")
        am.get_asset("character", "Ann", "red, new look")  # must not raise
        assert provider.generate_image.call_count == 2


# ---------------------------------------------------------------------------
# ValidatorManager integration
# ---------------------------------------------------------------------------

class TestConsistencyValidator:
    def _vm(self):
        return ValidatorManager(MagicMock(), DummyVisionProvider("PASS: ok"))

    def _shot(self):
        return Shot(shot_id="s1", scene_description="x", characters=["Ann"])

    def test_not_run_without_images(self, tmp_path):
        Image.new("RGB", (64, 64)).save(tmp_path / "f.png")
        report = self._vm().validate_frame(tmp_path / "f.png", self._shot(), None)
        assert len(report.results) == 4

    def test_fails_when_character_missing(self, tmp_path):
        ref = solid(tmp_path / "ref.png", (220, 30, 30, 255))
        Image.new("RGB", (320, 180), (80, 80, 90)).save(tmp_path / "f.png")
        report = self._vm().validate_frame(tmp_path / "f.png", self._shot(), None, character_images={"Ann": ref})
        assert not report.passed
        assert "ConsistencyValidator: Ann" in report.failure_feedback

    def test_passes_when_present(self, tmp_path):
        ref = solid(tmp_path / "ref.png", (220, 30, 30, 255))
        frame = Image.new("RGB", (320, 180), (80, 80, 90))
        frame.paste(Image.new("RGB", (40, 80), (200, 25, 25)), (100, 50))
        frame.save(tmp_path / "f.png")
        report = self._vm().validate_frame(tmp_path / "f.png", self._shot(), None, character_images={"Ann": ref})
        assert report.passed and len(report.results) == 5

    def test_unreadable_frame_fails_closed(self, tmp_path):
        ref = solid(tmp_path / "ref.png", (220, 30, 30, 255))
        (tmp_path / "f.png").write_text("not an image")
        report = self._vm().validate_frame(tmp_path / "f.png", self._shot(), None, character_images={"Ann": ref})
        assert not report.passed


# ---------------------------------------------------------------------------
# Real render: the validator must pass on true frames, under any lighting, and fail on a stranger
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not HAVE_BLENDER, reason="needs blender and xvfb-run on PATH")
class TestRealRenderConsistency:
    @pytest.fixture(autouse=True)
    def _linux(self, monkeypatch):
        from src.integrations import display

        monkeypatch.setattr(display, "needs_virtual_display", lambda: True)

    @pytest.mark.parametrize("lighting", ["bright daylight", "harsh noon sun", "dim", "dark night"])
    def test_real_frames_match_reference_art(self, tmp_path, lighting):
        from src.integrations.blender.renderer import BlenderRenderer

        cfg = {"storage_path": str(tmp_path / "s"), "render_width": 320,
               "render_height": 180, "render_fps": 6}
        img = DummyImageProvider()
        ann = img.generate_image("Ann, a woman in a green jacket", tmp_path / "ann.png")
        bob = img.generate_image("Bob, a man in an orange coat", tmp_path / "bob.png")
        shot = Shot(shot_id="shot_001", scene_description="talk", characters=["Ann", "Bob"],
                    lighting=lighting, duration_seconds=1.0)
        scene = SceneComposer(cfg).compose_shot(shot, {"Ann": ann, "Bob": bob})
        frame = BlenderRenderer(cfg).render_shot(scene, "shot_001", num_frames=1)[0]

        assert frame_shows_character(frame, ann)[0], lighting
        assert frame_shows_character(frame, bob)[0], lighting

        # A character who is not in the shot (magenta: no magenta anywhere in the set or lighting).
        stranger = solid(tmp_path / "zed.png", (230, 20, 200, 255))
        assert not frame_shows_character(frame, stranger)[0], lighting
