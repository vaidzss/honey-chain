"""Varroa mite counting from a sticky-board photograph.

The beekeeper slides a white board under the hive, leaves it 24-72 hours, and
photographs it. Fallen mites are what get counted; the count per day is the
standard field measure of infestation.

## Why classical computer vision and not a trained detector

We have no annotated Indian sticky-board images. Training a YOLO detector needs
thousands of them, and a model trained on a European dataset -- different board,
different debris, different camera -- would carry a domain gap we could not
measure.

What we *can* do without any training data is exploit the physics of the object:
a varroa mite is a dark, near-elliptical body about 1.1 x 1.6 mm with a
characteristic aspect ratio, on a deliberately white background. Adaptive
thresholding plus connected components with size and shape filters is a
well-understood approach for exactly this, and every rejection is explainable.

**This is a baseline that ships now and gets replaced.** Once the platform has
collected a few thousand beekeeper photographs with counts confirmed at
inspection -- which the inspection flow already captures -- a YOLO detector
trained on Indian boards will beat it. The interface here is what that model
plugs into.

## What it deliberately does not do

It never returns a bare number. It returns a count, a confidence, the reasons
candidates were rejected, and -- when the image is poor -- a refusal with advice
on retaking the photo. A wrong mite count leads to unnecessary miticide, which
costs money and breeds resistance.
"""

from __future__ import annotations

import io
import logging
import math
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageOps
from scipy import ndimage

log = logging.getLogger("aurabee.varroa")

VERSION = "varroa-cv-0.1.0"

# A varroa mite is roughly 1.1 x 1.6 mm. Board dimensions are asked of the user
# so pixels can be converted to millimetres; without a scale the counter refuses
# rather than guessing, because "how many dark specks" is not a mite count.
MITE_MIN_MM2 = 0.6
MITE_MAX_MM2 = 3.2
MITE_MIN_ASPECT = 1.05     # near-circular to oval
MITE_MAX_ASPECT = 2.30
MITE_MIN_SOLIDITY = 0.55   # mites are convex blobs; debris is ragged

MIN_RESOLUTION_PX_PER_MM = 4.0   # below this a mite is a handful of pixels


@dataclass
class Candidate:
    area_mm2: float
    aspect: float
    solidity: float
    centroid: tuple[float, float]
    accepted: bool
    reason: str = ""


@dataclass
class VarroaResult:
    count: int
    confidence: float
    px_per_mm: float
    candidates_examined: int
    rejected: dict[str, int] = field(default_factory=dict)
    advice_en: str = ""
    advice_hi: str = ""
    usable: bool = True
    version: str = VERSION

    def to_dict(self) -> dict:
        return {
            "count": self.count,
            "confidence": round(self.confidence, 3),
            "px_per_mm": round(self.px_per_mm, 2),
            "candidates_examined": self.candidates_examined,
            "rejected": self.rejected,
            "usable": self.usable,
            "advice_en": self.advice_en,
            "advice_hi": self.advice_hi,
            "model_version": self.version,
            "method": "classical CV (adaptive threshold + shape filtering)",
        }


# Enough pixels across a mite to measure its shape rather than merely notice it.
# A 1.1 mm mite at 4 px/mm is four pixels wide, which is not a shape.
TARGET_PX_PER_MM = 10.0
ABSOLUTE_MAX_PX = 3200          # keeps one photo under a second on a laptop


def _to_gray(data: bytes, board_width_mm: float) -> np.ndarray:
    """Downscale only as far as the measurement allows.

    A fixed max_side is wrong here: resizing a 4000 px photo of a 43 cm board to
    1400 px leaves 3.3 px/mm, so every mite becomes three pixels and the shape
    filters have nothing to work with. The resize target is derived from the
    physical width instead, so the pixels that carry the signal survive.
    """
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img).convert("L")

    wanted = min(ABSOLUTE_MAX_PX, int(board_width_mm * TARGET_PX_PER_MM))
    if img.width > wanted:
        scale = wanted / img.width
        img = img.resize((wanted, max(1, int(img.height * scale))), Image.LANCZOS)
    return np.asarray(img, dtype=np.float32) / 255.0


def _local_threshold(gray: np.ndarray, block_mm: float, px_per_mm: float) -> np.ndarray:
    """Adaptive threshold: a phone photo of a board in a field is unevenly lit,
    and a global threshold turns the shaded half of the board into one giant
    blob."""
    sigma = max(4.0, block_mm * px_per_mm / 2.0)
    background = ndimage.gaussian_filter(gray, sigma=sigma)
    # mites are darker than their local background
    return (background - gray) > 0.055


def count_mites(image_bytes: bytes, board_width_mm: float,
                board_height_mm: float | None = None) -> VarroaResult:
    """Count mites on a sticky board.

    `board_width_mm` is required: without a known physical scale there is no
    way to distinguish a mite from a speck of wax, and returning a number
    anyway would be worse than refusing.
    """
    gray = _to_gray(image_bytes, board_width_mm)
    h, w = gray.shape
    px_per_mm = w / float(board_width_mm)

    if px_per_mm < MIN_RESOLUTION_PX_PER_MM:
        return VarroaResult(
            count=0, confidence=0.0, px_per_mm=px_per_mm, candidates_examined=0,
            usable=False,
            advice_en=("The photo is too far away or too small to count mites. "
                       "Fill the frame with the board and take it again."),
            advice_hi=("फोटो बहुत दूर से या बहुत छोटी है। बोर्ड को पूरी स्क्रीन में "
                       "लेकर दोबारा फोटो लें।"))

    # Sharpness, via the variance of the Laplacian. A badly out-of-focus photo
    # can still have plenty of global contrast, sail past a standard-deviation
    # check, and then find nothing -- reporting "no mites found" with high
    # confidence on an unreadable image. That is the worst possible failure
    # here: the beekeeper concludes the colony is clean and does not treat.
    lap = ndimage.laplace(gray)
    sharpness = float(lap.var())
    if sharpness < 1.5e-4:
        return VarroaResult(
            count=0, confidence=0.0, px_per_mm=px_per_mm, candidates_examined=0,
            usable=False,
            advice_en=("The photo is out of focus. Hold the phone steady, about "
                       "30 cm above the board, and take it again."),
            advice_hi=("फोटो फोकस में नहीं है। फोन को स्थिर रखकर, बोर्ड से लगभग "
                       "30 सेमी ऊपर से दोबारा फोटो लें।"))

    # a very flat image is usually blown-out or a photo of nothing
    if float(gray.std()) < 0.035:
        return VarroaResult(
            count=0, confidence=0.0, px_per_mm=px_per_mm, candidates_examined=0,
            usable=False,
            advice_en=("The photo is blurred or over-exposed. Move into shade "
                       "and take it again."),
            advice_hi="फोटो धुंधली या बहुत तेज रोशनी में है। छाया में जाकर दोबारा लें।")

    mask = _local_threshold(gray, block_mm=6.0, px_per_mm=px_per_mm)
    mask = ndimage.binary_opening(mask, structure=np.ones((2, 2)))
    labels, n = ndimage.label(mask)
    if n == 0:
        return VarroaResult(count=0, confidence=0.85, px_per_mm=px_per_mm,
                            candidates_examined=0,
                            advice_en="No mites found on this board.",
                            advice_hi="इस बोर्ड पर कोई माइट नहीं मिला।")

    mm2_per_px = 1.0 / (px_per_mm ** 2)
    objects = ndimage.find_objects(labels)
    accepted, rejected = 0, {"too_small": 0, "too_large": 0,
                             "wrong_shape": 0, "too_ragged": 0}

    for i, sl in enumerate(objects, start=1):
        if sl is None:
            continue
        blob = labels[sl] == i
        area_px = int(blob.sum())
        area_mm2 = area_px * mm2_per_px

        if area_mm2 < MITE_MIN_MM2:
            rejected["too_small"] += 1
            continue
        if area_mm2 > MITE_MAX_MM2:
            rejected["too_large"] += 1        # wax flakes, bee parts, debris
            continue

        bh, bw = blob.shape
        aspect = max(bh, bw) / max(1.0, min(bh, bw))
        if not (MITE_MIN_ASPECT <= aspect <= MITE_MAX_ASPECT):
            rejected["wrong_shape"] += 1      # hairs, fibres, legs
            continue

        # solidity: filled area over its bounding box. A mite nearly fills an
        # ellipse; a fragment of comb does not.
        solidity = area_px / float(bh * bw)
        if solidity < MITE_MIN_SOLIDITY:
            rejected["too_ragged"] += 1
            continue

        accepted += 1

    examined = n
    # Confidence falls when most of what we saw was rejected: a board that is
    # mostly debris is one where the filters are doing the deciding, not the
    # evidence.
    accept_ratio = accepted / max(1, examined)
    resolution_factor = min(1.0, px_per_mm / 10.0)
    confidence = float(np.clip(0.45 + 0.4 * resolution_factor
                               + 0.25 * min(1.0, accept_ratio * 4), 0, 0.95))

    if accepted == 0:
        en = "No mites found on this board."
        hi = "इस बोर्ड पर कोई माइट नहीं मिला।"
    elif accepted < 10:
        en = (f"About {accepted} mites. Low infestation -- keep monitoring, no "
              f"treatment needed yet.")
        hi = f"लगभग {accepted} माइट। कम संक्रमण — निगरानी रखें, अभी इलाज की जरूरत नहीं।"
    elif accepted < 40:
        en = (f"About {accepted} mites. Approaching the treatment threshold; "
              f"count again in a week.")
        hi = f"लगभग {accepted} माइट। इलाज की सीमा के करीब; एक हफ्ते बाद फिर गिनें।"
    else:
        en = f"About {accepted} mites. High infestation -- treat this colony."
        hi = f"लगभग {accepted} माइट। ज्यादा संक्रमण — इस कॉलोनी का इलाज करें।"

    return VarroaResult(
        count=accepted, confidence=confidence, px_per_mm=px_per_mm,
        candidates_examined=examined,
        rejected={k: v for k, v in rejected.items() if v},
        advice_en=en, advice_hi=hi)
