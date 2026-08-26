import pytest

from src.core.asset_manager.asset_manager import (
    AssetManager,
    AssetManagerError,
    _sanitize,
)
from src.providers.dummy_provider import DummyImageProvider


@pytest.fixture
def asset_manager(tmp_path):
    provider = DummyImageProvider()
    mgr = AssetManager({"storage_path": str(tmp_path / "storage")}, provider)
    return mgr, provider


class TestSanitize:
    def test_normal_name_unchanged(self):
        assert _sanitize("detective") == "detective"

    def test_spaces_replaced(self):
        assert _sanitize("brass key") == "brass_key"

    def test_special_chars_replaced(self):
        assert _sanitize("john/doe:1") == "john_doe_1"

    def test_empty_after_sanitize_raises(self):
        with pytest.raises(AssetManagerError):
            _sanitize("???")


class TestGetAsset:
    def test_generates_new_asset(self, asset_manager):
        mgr, provider = asset_manager
        path = mgr.get_asset("character", "detective", "tall, brown fedora")

        assert path.exists()
        assert len(provider.call_log) == 1
        assert provider.call_log[0]["prompt"] == "tall, brown fedora"

    def test_same_description_hits_cache(self, asset_manager):
        mgr, provider = asset_manager
        path1 = mgr.get_asset("character", "detective", "tall, brown fedora")
        path2 = mgr.get_asset("character", "detective", "tall, brown fedora")

        assert path1 == path2
        assert len(provider.call_log) == 1  # only generated once

    def test_different_description_creates_new_version(self, asset_manager):
        mgr, provider = asset_manager
        path1 = mgr.get_asset("character", "detective", "tall, brown fedora")
        path2 = mgr.get_asset("character", "detective", "tall, injured, torn coat")

        assert path1 != path2
        assert len(provider.call_log) == 2
        versions = mgr.list_asset_versions("character", "detective")
        assert len(versions) == 2

    def test_force_regenerate_creates_new_version_even_if_same_description(self, asset_manager):
        mgr, provider = asset_manager
        mgr.get_asset("character", "detective", "tall, brown fedora")
        mgr.get_asset("character", "detective", "tall, brown fedora", force_regenerate=True)

        assert len(provider.call_log) == 2
        versions = mgr.list_asset_versions("character", "detective")
        assert len(versions) == 2

    def test_different_asset_types_are_independent(self, asset_manager):
        mgr, provider = asset_manager
        char_path = mgr.get_asset("character", "detective", "a person")
        obj_path = mgr.get_asset("object", "detective", "a statue")  # same name!

        assert char_path != obj_path
        assert len(provider.call_log) == 2

    def test_version_numbers_increment_sequentially(self, asset_manager):
        mgr, _ = asset_manager
        mgr.get_asset("character", "detective", "v1 look")
        mgr.get_asset("character", "detective", "v2 look")
        mgr.get_asset("character", "detective", "v3 look")

        versions = mgr.list_asset_versions("character", "detective")
        version_numbers = [int(v.name.lstrip("v")) for v in versions]
        assert version_numbers == [1, 2, 3]


class TestDeleteAssetVersion:
    def test_delete_existing_version(self, asset_manager):
        mgr, _ = asset_manager
        mgr.get_asset("character", "detective", "a look")
        assert mgr.delete_asset_version("character", "detective", 1) is True
        assert mgr.list_asset_versions("character", "detective") == []

    def test_delete_nonexistent_version_returns_false(self, asset_manager):
        mgr, _ = asset_manager
        assert mgr.delete_asset_version("character", "nobody", 1) is False


class TestListAssetVersions:
    def test_empty_for_unknown_asset(self, asset_manager):
        mgr, _ = asset_manager
        assert mgr.list_asset_versions("character", "nobody") == []


class TestCacheRecovery:
    def test_missing_image_file_triggers_regeneration(self, asset_manager):
        """If meta.json says a version matches but the actual image file
        was deleted (e.g. manual cleanup, disk issue), get_asset should
        detect the missing file and regenerate rather than returning a
        path that doesn't exist."""
        mgr, provider = asset_manager
        path = mgr.get_asset("character", "detective", "a look")
        path.unlink()  # simulate the image file disappearing

        new_path = mgr.get_asset("character", "detective", "a look")
        assert new_path.exists()
        assert len(provider.call_log) == 2  # regenerated

    def test_missing_meta_json_treated_as_cache_miss(self, asset_manager):
        mgr, provider = asset_manager
        path = mgr.get_asset("character", "detective", "a look")
        (path.parent / "meta.json").unlink()

        mgr.get_asset("character", "detective", "a look")
        assert len(provider.call_log) == 2  # no valid meta -> regenerated

    def test_corrupted_meta_json_treated_as_cache_miss(self, asset_manager):
        mgr, provider = asset_manager
        path = mgr.get_asset("character", "detective", "a look")
        (path.parent / "meta.json").write_text("{not valid json")

        mgr.get_asset("character", "detective", "a look")
        assert len(provider.call_log) == 2  # unreadable meta -> regenerated

    def test_version_number_parsing_ignores_malformed_dirs(self, asset_manager, tmp_path):
        """A stray non-version directory (e.g. 'vfoo') under an asset dir
        shouldn't crash version numbering — it should just be ignored."""
        mgr, provider = asset_manager
        mgr.get_asset("character", "detective", "a look")

        asset_dir = mgr.storage_path / "character" / "detective"
        (asset_dir / "vfoo").mkdir()  # malformed version dir

        # Should not raise, and should still compute the next real version
        second_path = mgr.get_asset("character", "detective", "a different look")
        assert second_path.exists()
        versions = mgr.list_asset_versions("character", "detective")
        version_numbers = sorted(
            int(v.name.lstrip("v")) for v in versions if v.name.lstrip("v").isdigit()
        )
        assert version_numbers == [1, 2]
