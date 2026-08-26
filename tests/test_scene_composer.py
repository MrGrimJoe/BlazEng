import pytest

from src.core.director.director import Shot
from src.core.scene_composer.scene_composer import (
    SceneComposer,
    SceneComposerError,
    _sanitize_shot_id,
)


@pytest.fixture
def composer(tmp_path):
    return SceneComposer({"storage_path": str(tmp_path / "storage")})


@pytest.fixture
def dummy_asset(tmp_path):
    path = tmp_path / "character.png"
    path.write_bytes(b"fake png bytes")
    return path


class TestProjectInitialization:
    def test_creates_project_file(self, composer):
        assert (composer.project_dir / "project.godot").exists()

    def test_creates_default_scene(self, composer):
        assert (composer.scenes_dir / "_default.tscn").exists()

    def test_does_not_overwrite_existing_project_file(self, tmp_path):
        composer1 = SceneComposer({"storage_path": str(tmp_path / "storage")})
        project_file = composer1.project_dir / "project.godot"
        project_file.write_text("CUSTOM_MARKER")

        # Constructing a second composer against the same storage path
        # should not clobber an existing project.godot.
        SceneComposer({"storage_path": str(tmp_path / "storage")})
        assert project_file.read_text() == "CUSTOM_MARKER"


class TestComposeShot:
    def test_missing_character_asset_raises(self, composer):
        shot = Shot(shot_id="shot_001", scene_description="x", characters=["nobody"])
        with pytest.raises(SceneComposerError, match="nobody"):
            composer.compose_shot(shot, assets={})

    def test_composes_scene_with_no_characters(self, composer):
        shot = Shot(shot_id="shot_001", scene_description="empty room", characters=[])
        scene_path = composer.compose_shot(shot, assets={})
        assert scene_path.exists()
        content = scene_path.read_text()
        assert "no characters in this shot" in content

    def test_composes_scene_with_one_character(self, composer, dummy_asset):
        shot = Shot(shot_id="shot_001", scene_description="x", characters=["detective"])
        scene_path = composer.compose_shot(shot, {"detective": dummy_asset})
        content = scene_path.read_text()
        assert str(dummy_asset) in content
        assert "Image.new()" in content
        assert "ImageTexture.create_from_image" in content

    def test_does_not_use_ext_resource_for_dynamic_images(self, composer, dummy_asset):
        """Regression guard: ExtResource requires a Godot .import sidecar
        file that freshly-generated images don't have — verified against
        a real Godot binary that this fails to load. Scenes must load
        images at runtime instead (see module docstring)."""
        shot = Shot(shot_id="shot_001", scene_description="x", characters=["detective"])
        scene_path = composer.compose_shot(shot, {"detective": dummy_asset})
        content = scene_path.read_text()
        # Check for the actual Godot resource-declaration syntax, not a
        # bare substring — pytest's own tmp_path dirs are named after the
        # test function and can incidentally contain "ext_resource" (as
        # this test's directory does), which would false-positive a
        # plain substring check.
        assert "[ext_resource" not in content

    def test_scene_filename_matches_shot_id(self, composer, dummy_asset):
        shot = Shot(shot_id="shot_042", scene_description="x", characters=["a"])
        scene_path = composer.compose_shot(shot, {"a": dummy_asset})
        assert scene_path.name == "shot_042.tscn"

    def test_multiple_characters_get_distinct_positions(self, composer, tmp_path):
        asset_a = tmp_path / "a.png"
        asset_b = tmp_path / "b.png"
        asset_a.write_bytes(b"a")
        asset_b.write_bytes(b"b")

        shot = Shot(shot_id="shot_001", scene_description="x", characters=["a", "b"])
        scene_path = composer.compose_shot(shot, {"a": asset_a, "b": asset_b})
        content = scene_path.read_text()

        # Two distinct sprite blocks should exist, each with its own
        # position — a real bug here would be both characters landing
        # on the same coordinates.
        assert content.count("Sprite2D.new()") == 2
        assert content.count("add_child(sprite_") == 2


class TestCameraAngleScaling:
    @pytest.mark.parametrize("angle,expected_scale", [
        ("wide shot", 0.5),
        ("medium shot", 1.0),
        ("close-up", 1.8),
        ("overhead", 0.7),
        ("some totally unrecognized angle", 1.0),  # falls back to default
    ])
    def test_scale_applied_per_camera_angle(self, composer, dummy_asset, angle, expected_scale):
        shot = Shot(shot_id="shot_001", scene_description="x", characters=["a"], camera_angle=angle)
        scene_path = composer.compose_shot(shot, {"a": dummy_asset})
        content = scene_path.read_text()
        assert f"Vector2({expected_scale}, {expected_scale})" in content

    def test_camera_angle_matching_is_case_insensitive(self, composer, dummy_asset):
        shot = Shot(shot_id="shot_001", scene_description="x", characters=["a"], camera_angle="CLOSE-UP")
        scene_path = composer.compose_shot(shot, {"a": dummy_asset})
        assert "Vector2(1.8, 1.8)" in scene_path.read_text()


class TestLightingModulate:
    @pytest.mark.parametrize("lighting_text,expect_dim", [
        ("dim, shadowy warehouse", True),
        ("dark and cold", True),
        ("bright daylight", False),
        ("harsh overhead lighting", False),
        ("completely unremarkable lighting", None),  # default, neither dim nor bright
    ])
    def test_lighting_keywords_affect_modulate(self, composer, dummy_asset, lighting_text, expect_dim):
        shot = Shot(shot_id="shot_001", scene_description="x", characters=["a"], lighting=lighting_text)
        scene_path = composer.compose_shot(shot, {"a": dummy_asset})
        content = scene_path.read_text()

        # Extract the modulate line's first Color() component values crudely
        import re
        match = re.search(r"modulate = Color\(([\d.]+),", content)
        assert match is not None
        r_value = float(match.group(1))

        if expect_dim is True:
            assert r_value < 1.0, f"Expected dimmed modulate for '{lighting_text}', got r={r_value}"
        elif expect_dim is False:
            assert r_value > 1.0, f"Expected brightened modulate for '{lighting_text}', got r={r_value}"
        else:
            assert r_value == 1.0


class TestSanitizeShotId:
    def test_normal_id_unchanged(self):
        assert _sanitize_shot_id("shot_001") == "shot_001"

    def test_special_characters_replaced(self):
        assert _sanitize_shot_id("shot/001:weird") == "shot_001_weird"

    def test_empty_falls_back_to_shot(self):
        assert _sanitize_shot_id("???") == "_"  # regex replaces symbols with _
        assert _sanitize_shot_id("") == "shot"


class TestConfigurableResolution:
    def test_default_resolution_matches_godot_default(self, composer):
        # Verified against a real Godot 4.7.2 binary: this is its actual
        # built-in default when no [display] section overrides it.
        assert composer.viewport_width == 1152
        assert composer.viewport_height == 648

    def test_custom_resolution_applied_from_config(self, tmp_path):
        composer = SceneComposer({
            "storage_path": str(tmp_path / "storage"),
            "render_width": 800,
            "render_height": 600,
        })
        assert composer.viewport_width == 800
        assert composer.viewport_height == 600

    def test_custom_resolution_written_to_project_file(self, tmp_path):
        composer = SceneComposer({
            "storage_path": str(tmp_path / "storage"),
            "render_width": 800,
            "render_height": 600,
        })
        project_content = (composer.project_dir / "project.godot").read_text()
        assert "viewport_width=800" in project_content
        assert "viewport_height=600" in project_content

    def test_layout_scales_with_custom_viewport(self, tmp_path, dummy_asset):
        # A single character should be horizontally centered regardless of
        # viewport size — verifies layout math actually uses the
        # configured dimensions, not the old hardcoded constants.
        narrow_composer = SceneComposer({
            "storage_path": str(tmp_path / "narrow_storage"),
            "render_width": 400,
            "render_height": 300,
        })
        shot = Shot(shot_id="shot_001", scene_description="x", characters=["a"])
        scene_path = narrow_composer.compose_shot(shot, {"a": dummy_asset})
        content = scene_path.read_text()

        import re
        match = re.search(r"position = Vector2\(([\d.]+),", content)
        assert match is not None
        x = float(match.group(1))
        # Single character centered in a 400px-wide viewport should land
        # near x=200, not the old hardcoded 1152-based value (~576).
        assert 150 < x < 250, f"Expected centered position near 200 for a 400px viewport, got {x}"
