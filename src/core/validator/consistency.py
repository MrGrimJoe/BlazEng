"""
Deterministic character-consistency helpers (no model calls).

Two jobs:

* ``similarity(a, b)``: how alike two character images are (alpha-masked colour
  histogram intersection, 0..1). Used by AssetManager to detect a regenerated
  character drifting away from its locked reference image.
* ``frame_shows_character(frame, reference)``: does a rendered frame contain the
  character's distinctive colours? Hue-based, so lighting tints and dimming
  don't cause false failures, but a missing or recoloured character does.
"""

import colorsys
from pathlib import Path
from typing import List, Tuple

from PIL import Image

_BINS = 8  # per channel for the histogram signature
_HUE_BUCKETS = 24


def _pixels(im: Image.Image):
    """Pixel iterator that avoids the Pillow-14-deprecated getdata() when possible."""
    getter = getattr(im, "get_flattened_data", None) or im.getdata
    return getter()


def _opaque_pixels(path: Path, size: Tuple[int, int] = (64, 64)) -> List[Tuple[int, int, int]]:
    with Image.open(path) as im:
        im = im.convert("RGBA")
        im.thumbnail(size)
        return [(r, g, b) for r, g, b, a in _pixels(im) if a >= 128]


def _signature(pixels: List[Tuple[int, int, int]]) -> dict:
    sig: dict = {}
    for r, g, b in pixels:
        key = (r * _BINS // 256, g * _BINS // 256, b * _BINS // 256)
        sig[key] = sig.get(key, 0) + 1
    total = float(len(pixels)) or 1.0
    return {k: v / total for k, v in sig.items()}


def similarity(a: Path, b: Path) -> float:
    """Colour-histogram intersection of two images' opaque pixels: 1.0 = same palette."""
    sa, sb = _signature(_opaque_pixels(a)), _signature(_opaque_pixels(b))
    if not sa or not sb:
        return 0.0
    return sum(min(v, sb.get(k, 0.0)) for k, v in sa.items())


def distinctive_hues(reference: Path, min_share: float = 0.04) -> List[int]:
    """Hue buckets that make up a meaningful share of the character's saturated pixels."""
    counts: dict = {}
    saturated = 0
    for r, g, b in _opaque_pixels(reference):
        h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
        if s < 0.25 or v < 0.15:
            continue
        saturated += 1
        bucket = int(h * _HUE_BUCKETS) % _HUE_BUCKETS
        counts[bucket] = counts.get(bucket, 0) + 1
    if not saturated:
        return []
    return [b for b, c in counts.items() if c / saturated >= min_share]


def frame_shows_character(frame: Path, reference: Path, min_pixels: int = 25) -> Tuple[bool, str]:
    """Check that a frame contains the reference character's distinctive hues.

    Returns (ok, explanation). A character with no saturated colours (greyscale
    art) has nothing distinctive to check, so it passes.
    """
    hues = distinctive_hues(reference)
    if not hues:
        return True, "no distinctive colours to check"

    with Image.open(frame) as im:
        im = im.convert("RGB")
        im.thumbnail((320, 320))
        frame_pixels = list(_pixels(im))

    found = {h: 0 for h in hues}
    for r, g, b in frame_pixels:
        h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
        if s < 0.2 or v < 0.08:
            continue
        bucket = int(h * _HUE_BUCKETS) % _HUE_BUCKETS
        for target in found:
            d = min((bucket - target) % _HUE_BUCKETS, (target - bucket) % _HUE_BUCKETS)
            if d <= 1:
                found[target] += 1
    missing = [h for h, n in found.items() if n < min_pixels]
    # Need most of the character's distinctive hues present (tolerates one hidden by lighting).
    if len(missing) * 2 > len(hues):
        return False, f"{len(missing)}/{len(hues)} of the character's distinctive colours not found in frame"
    return True, f"{len(hues) - len(missing)}/{len(hues)} distinctive colours present"


def palette_from_image(path: Path) -> dict:
    """Rough body-part colours of a standing character image: skin, hair, shirt, trousers.

    Reads the median colour of the opaque pixels in the head, torso and leg bands of the
    picture. Exact for flat/simple art, an approximation for detailed illustrations;
    it exists so a 3D stand-in wears the same colours as the character's reference art.
    Values are 0..1 RGB tuples.
    """
    with Image.open(path) as im:
        im = im.convert("RGBA")
        im.thumbnail((96, 192))
        w, h = im.size
        px = im.load()

        def band(y0: float, y1: float):
            return [px[x, y][:3] for y in range(int(h * y0), max(int(h * y0) + 1, int(h * y1)))
                    for x in range(w) if px[x, y][3] >= 128]

        def median(pixels):
            if not pixels:
                return None
            return tuple(sorted(c[i] for c in pixels)[len(pixels) // 2] for i in range(3))

        head, torso, legs = band(0.04, 0.22), band(0.28, 0.58), band(0.64, 0.96)
    fallback = (150, 150, 150)
    skin = median(head) or fallback
    shirt = median(torso) or skin
    trousers = median(legs) or shirt
    top = sorted(head[: max(1, len(head) // 3)], key=lambda c: sum(c))
    hair = median(top[: max(1, len(top) // 3)]) or skin
    if sum(abs(a - b) for a, b in zip(hair, skin)) < 40:  # no visible hair in the art
        hair = tuple(int(c * 0.35) for c in skin)
    norm = lambda c: [round(v / 255.0, 4) for v in c]  # noqa: E731
    return {"skin": norm(skin), "hair": norm(hair), "shirt": norm(shirt), "trousers": norm(trousers)}
