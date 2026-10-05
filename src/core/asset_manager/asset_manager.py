"""
AssetManager — generates, caches, and versions visual assets.

Assets are keyed by (asset_type, name). Each generation gets a new
version directory rather than overwriting, so a character's earlier
appearance stays available for comparison or rollback after a repair.
Cache hits are based on the *prompt text* matching the most recent
version's recorded prompt — if the description changes, that's treated
as an intentional new version, not a cache miss to silently ignore.
"""

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.providers.base import ImageProvider

logger = logging.getLogger(__name__)

_SAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9_-]+")


class AssetManagerError(Exception):
    """Raised for invalid asset operations."""


def _sanitize(name: str) -> str:
    """Make a string safe to use as a directory component.

    Raises if nothing alphanumeric survives — a name like "???" would
    otherwise sanitize to a bare "_", which is a valid directory name but
    would silently collide with every other all-symbol name passed in.
    """
    cleaned = _SAFE_NAME_RE.sub("_", name.strip())
    if not re.search(r"[a-zA-Z0-9]", cleaned):
        raise AssetManagerError(
            f"Asset name has no usable characters after sanitizing: {name!r}"
        )
    return cleaned


class AssetManager:
    """Generates and caches character/object/environment art."""

    def __init__(self, config: Dict[str, Any], image_provider: ImageProvider):
        self.config = config
        self.image_provider = image_provider
        self.storage_path = Path(config.get("storage_path", "./storage")) / "assets"
        self.min_similarity = float(config.get("consistency_min_similarity", 0.5))
        self.consistency_retries = int(config.get("consistency_retries", 2))
        self.storage_path.mkdir(parents=True, exist_ok=True)

    def get_asset(
        self,
        asset_type: str,
        name: str,
        description: str,
        force_regenerate: bool = False,
    ) -> Path:
        """Return the path to an asset image, generating it if needed.

        - If a version already exists with the same description and
          force_regenerate is False, that cached image is returned.
        - If the description differs from the latest version's, or
          force_regenerate is True, a new version is generated.
        """
        asset_dir = self.storage_path / _sanitize(asset_type) / _sanitize(name)
        asset_dir.mkdir(parents=True, exist_ok=True)

        latest = self._latest_version(asset_dir)
        if latest is not None and not force_regenerate:
            cached_meta = self._read_meta(latest)
            if cached_meta.get("description") == description:
                cached_image = self._find_image(latest)
                if cached_image is not None:
                    logger.info(f"Asset cache hit: {asset_type}/{name} ({latest.name})")
                    return cached_image
                logger.warning(
                    f"Version dir {latest} has metadata but no image file — regenerating"
                )

        next_version = self._next_version_number(asset_dir)
        version_dir = asset_dir / f"v{next_version}"
        version_dir.mkdir(parents=True, exist_ok=True)

        image_path = version_dir / f"{_sanitize(name)}_v{next_version}.png"
        logger.info(f"Generating asset: {asset_type}/{name} v{next_version}")

        # Character consistency: a new version of an existing character is
        # generated *from* its first (locked) image, so a changed description or
        # a repair re-draws the same person rather than a stranger.
        reference = self._locked_reference(asset_dir) if asset_type == "character" else None
        meta: Dict[str, Any] = {
            "asset_type": asset_type,
            "name": name,
            "description": description,
            "version": next_version,
        }
        if reference is None:
            self.image_provider.generate_image(description, image_path)
        else:
            meta["reference"] = str(reference)
            score = self._generate_close_to(description, image_path, reference)
            if score is not None:
                meta["similarity_to_reference"] = round(score, 3)
                if score < self.min_similarity:
                    meta["drift_warning"] = True
                    logger.warning(
                        f"Character '{name}' v{next_version} drifted from its reference "
                        f"(similarity {score:.2f} < {self.min_similarity:.2f})"
                    )

        self._write_meta(version_dir, meta)
        return image_path

    def locked_reference(self, asset_type: str, name: str) -> Optional[Path]:
        """The image every later version of this asset is generated from (its v1)."""
        return self._locked_reference(self.storage_path / _sanitize(asset_type) / _sanitize(name))

    def _locked_reference(self, asset_dir: Path) -> Optional[Path]:
        if not asset_dir.exists():
            return None
        versions = sorted(
            (d for d in asset_dir.iterdir() if d.is_dir() and d.name.startswith("v")),
            key=self._version_number,
        )
        for v in versions:
            image = self._find_image(v)
            if image is not None:
                return image
        return None

    def _generate_close_to(self, description: str, image_path: Path, reference: Path) -> Optional[float]:
        """Generate with the reference, retrying while the result drifts. Returns similarity, or None if unmeasurable."""
        from src.core.validator.consistency import similarity

        score: Optional[float] = None
        for attempt in range(1 + self.consistency_retries):
            self.image_provider.generate_image(description, image_path, reference_image=reference)
            try:
                score = similarity(reference, image_path)
            except Exception as e:  # noqa: BLE001 - unreadable image: cannot measure, do not block
                logger.debug(f"Could not measure similarity: {e}")
                return None
            if score >= self.min_similarity:
                break
            logger.info(f"Drift check failed (attempt {attempt + 1}): similarity {score:.2f}")
        return score

    def list_asset_versions(self, asset_type: str, name: str) -> List[Path]:
        asset_dir = self.storage_path / _sanitize(asset_type) / _sanitize(name)
        if not asset_dir.exists():
            return []
        return sorted(
            (d for d in asset_dir.iterdir() if d.is_dir() and d.name.startswith("v")),
            key=self._version_number,
        )

    def delete_asset_version(self, asset_type: str, name: str, version: int) -> bool:
        asset_dir = self.storage_path / _sanitize(asset_type) / _sanitize(name)
        version_dir = asset_dir / f"v{version}"
        if not version_dir.exists():
            return False
        import shutil
        shutil.rmtree(version_dir)
        logger.info(f"Deleted asset version: {asset_type}/{name} v{version}")
        return True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _version_number(version_dir: Path) -> int:
        try:
            return int(version_dir.name.lstrip("v"))
        except ValueError:
            return 0

    def _latest_version(self, asset_dir: Path) -> Optional[Path]:
        versions = [
            d for d in asset_dir.iterdir() if d.is_dir() and d.name.startswith("v")
        ] if asset_dir.exists() else []
        if not versions:
            return None
        return max(versions, key=self._version_number)

    def _next_version_number(self, asset_dir: Path) -> int:
        latest = self._latest_version(asset_dir)
        if latest is None:
            return 1
        return self._version_number(latest) + 1

    @staticmethod
    def _find_image(version_dir: Path) -> Optional[Path]:
        for ext in (".png", ".jpg", ".jpeg", ".webp"):
            matches = list(version_dir.glob(f"*{ext}"))
            if matches:
                return matches[0]
        return None

    @staticmethod
    def _read_meta(version_dir: Path) -> Dict[str, Any]:
        meta_path = version_dir / "meta.json"
        if not meta_path.exists():
            return {}
        try:
            return json.loads(meta_path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

    @staticmethod
    def _write_meta(version_dir: Path, meta: Dict[str, Any]) -> None:
        (version_dir / "meta.json").write_text(json.dumps(meta, indent=2))
