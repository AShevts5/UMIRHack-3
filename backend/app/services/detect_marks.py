"""Detection of *graphic* signature and seal marks.

rules.md 2.3 and README are explicit: a printed caption such as «Подпись» or
«М.П.» is **not** a signature/seal.  This module therefore never treats OCR text
as evidence for a mark — it only uses text to *localise the zone*, and then
analyses the pixels of that zone:

``detect_marks(image, ocr_result, document_type)``
    Canonical entry point (the signature required by the technical task).

Two independent evidence paths are combined so that the result does not depend on
one fragile signal:

``colour``
    Signature ink and the seal mock-up are drawn in blue; printed body text is
    black.  A chroma mask (blue minus red/green) isolates real ink even when the
    scan is noisy.  This is genuine pixel analysis, not a caption lookup.
``geometry``
    For monochrome scans the same zones are analysed in greyscale: handwriting
    strokes have a high perimeter/area ("roughness") ratio, while printed glyphs,
    the horizontal signature rule and fill lines are smooth.  Seals are found as
    large ring-shaped components (with an optional Hough-circle confirmation).

Positive and negative examples of all three document types are covered by
``tests/test_signatures.py``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import cv2
import numpy as np

from ..config import settings
from ..utils.textnorm import norm_text
from .document_config import DocumentTypeSpec, MarkSpec
from .loader import DocumentImage
from .ocr import OcrResult, TextLine, Word

# ---------------------------------------------------------------------------
# Tuning constants (measured on the open v1.0 set, see README «Проверка качества»)
# ---------------------------------------------------------------------------
BLUE_CHROMA_DIFF = 28  # B - R threshold for "this pixel is blue ink"
BLUE_GREEN_DIFF = 8  # B - G threshold
BLUE_MIN_VALUE = 60
MIN_MARK_PIXELS = 90  # a mark smaller than this is OCR speckle, not ink
MIN_MARK_WIDTH = 45
MIN_MARK_HEIGHT = 5
MAX_MARK_HEIGHT_RATIO = 0.22  # a "signature" taller than this share of the page is a graphic
MAX_MARK_WIDTH_RATIO = 0.45
MIN_SEAL_DIAMETER_RATIO = 0.045
MAX_SEAL_DIAMETER_RATIO = 0.30
SEAL_MIN_FILL = 0.012  # ring pixels / bounding box area

_HANDWRITING_HINTS = ("подпис", "одпись", "подпи", "подгис", "signature")
_SEAL_HINTS = ("печат", "м.п", "м п", "штамп", "seal")


@dataclass
class Zone:
    """A pixel rectangle (x, y, w, h) with the words that anchored it."""

    x: int
    y: int
    w: int
    h: int
    anchors: list[str] = field(default_factory=list)

    @property
    def x2(self) -> int:
        return self.x + self.w

    @property
    def y2(self) -> int:
        return self.y + self.h

    @property
    def area(self) -> int:
        return max(self.w, 0) * max(self.h, 0)

    def as_box(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.w, self.h)

    def intersects(self, other: "Zone", pad: int = 0) -> bool:
        return not (
            self.x2 + pad <= other.x
            or other.x2 + pad <= self.x
            or self.y2 + pad <= other.y
            or other.y2 + pad <= self.y
        )

    def expanded(self, pad_x: int, pad_y: int, bounds: tuple[int, int]) -> "Zone":
        width, height = bounds
        x = max(0, self.x - pad_x)
        y = max(0, self.y - pad_y)
        x2 = min(width, self.x2 + pad_x)
        y2 = min(height, self.y2 + pad_y)
        return Zone(x=x, y=y, w=max(0, x2 - x), h=max(0, y2 - y), anchors=list(self.anchors))


# ---------------------------------------------------------------------------
# Image masks
# ---------------------------------------------------------------------------


def blue_ink_mask(rgb: np.ndarray) -> np.ndarray:
    """Boolean mask of blue-dominant pixels (ink of the signature/seal mock-up)."""
    data = rgb.astype(np.int16)
    red, green, blue = data[..., 0], data[..., 1], data[..., 2]
    mask = (
        ((blue - red) > BLUE_CHROMA_DIFF)
        & ((blue - green) > BLUE_GREEN_DIFF)
        & (blue > BLUE_MIN_VALUE)
    )
    return mask


def dark_ink_mask(grey: np.ndarray, threshold: int = 150) -> np.ndarray:
    """Boolean mask of dark pixels — used by the monochrome fallback path."""
    return grey < threshold


# ---------------------------------------------------------------------------
# Anchors
# ---------------------------------------------------------------------------


def _word_text_pairs(ocr: OcrResult) -> list[Word]:
    return [word for word in ocr.words if word.text.strip()]


def find_signature_words(ocr: OcrResult) -> list[Word]:
    """Words that print the signature caption (used only for localisation)."""
    hits: list[Word] = []
    for word in _word_text_pairs(ocr):
        low = norm_text(word.text)
        if any(hint in low for hint in _HANDWRITING_HINTS) and len(low) >= 5:
            hits.append(word)
    return hits


def find_seal_words(ocr: OcrResult) -> list[Word]:
    hits: list[Word] = []
    for word in _word_text_pairs(ocr):
        low = norm_text(word.text).strip(" .:;,")
        if low in {"м.п", "мп", "м п", "место печати", "печать"}:
            hits.append(word)
            continue
        if any(hint in low for hint in _SEAL_HINTS) and len(low) >= 4:
            hits.append(word)
    return hits


def _lines_with(ocr: OcrResult, hints: tuple[str, ...]) -> list[TextLine]:
    """Text lines containing any of ``hints`` (diagnostics/self-test helper)."""
    found = []
    for line in ocr.lines:
        low = norm_text(line.text)
        if any(hint in low for hint in hints):
            found.append(line)
    return found


#: Printed seal captions that OCR sometimes merges into the signature caption line.
_SEAL_PRINT_RE = re.compile(r"^(м\.?\s?п\.?|печать|печ|макет|штамп|ученая|учебная)$")


def _is_seal_print(word: Word) -> bool:
    return bool(_SEAL_PRINT_RE.match(norm_text(word.text).strip(". :;,")))


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------


def find_horizontal_rules(
    grey: np.ndarray,
    min_length_ratio: float = 0.06,
    max_thickness: int = 6,
) -> list[Zone]:
    """Locate long thin horizontal segments (signature rule, fill-in blanks)."""
    height, width = grey.shape
    min_length = max(40, int(width * min_length_ratio))
    dark = (grey < 190).astype(np.uint8)
    opened = cv2.morphologyEx(
        dark, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (min_length, 1))
    )
    count, _, stats, _ = cv2.connectedComponentsWithStats(opened, connectivity=8)
    rules: list[Zone] = []
    for index in range(1, count):
        x, y, w, h, _ = stats[index]
        if w >= min_length and h <= max_thickness:
            rules.append(Zone(x=int(x), y=int(y), w=int(w), h=int(h), anchors=["rule"]))
    return rules


def _roughness(mask: np.ndarray) -> float:
    """Perimeter² / area of the largest component — high for handwriting."""
    if mask.sum() == 0:
        return 0.0
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    if count <= 1:
        return 0.0
    best = 0.0
    for index in range(1, count):
        area = float(stats[index, cv2.CC_STAT_AREA])
        if area < 40:
            continue
        component = (mask & 0).astype(np.uint8)
        component = np.zeros_like(mask, dtype=np.uint8)
        component[stats[index, cv2.CC_STAT_TOP] : stats[index, cv2.CC_STAT_TOP] + stats[index, cv2.CC_STAT_HEIGHT],
                  stats[index, cv2.CC_STAT_LEFT] : stats[index, cv2.CC_STAT_LEFT] + stats[index, cv2.CC_STAT_WIDTH]] = (
            mask[
                stats[index, cv2.CC_STAT_TOP] : stats[index, cv2.CC_STAT_TOP] + stats[index, cv2.CC_STAT_HEIGHT],
                stats[index, cv2.CC_STAT_LEFT] : stats[index, cv2.CC_STAT_LEFT] + stats[index, cv2.CC_STAT_WIDTH],
            ]
        ).astype(np.uint8)
        contours, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        if not contours:
            continue
        perimeter = float(sum(cv2.arcLength(contour, True) for contour in contours))
        best = max(best, (perimeter * perimeter) / max(area, 1.0))
    return best


def _component_stats(mask: np.ndarray, min_area: int = 12) -> list[dict]:
    if mask.sum() == 0:
        return []
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    items = []
    for index in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[index])
        if area < min_area:
            continue
        items.append({"x": x, "y": y, "w": w, "h": h, "area": area})
    return items


def _merge_boxes(boxes: list[dict], gap_x: int, gap_y: int) -> dict | None:
    """Union of nearby components: a signature is many separate strokes."""
    if not boxes:
        return None
    boxes = sorted(boxes, key=lambda item: (item["y"], item["x"]))
    group = dict(boxes[0])
    changed = True
    while changed:
        changed = False
        for item in boxes:
            if item is group:
                continue
            if (
                item["x"] <= group["x"] + group["w"] + gap_x
                and group["x"] <= item["x"] + item["w"] + gap_x
                and item["y"] <= group["y"] + group["h"] + gap_y
                and group["y"] <= item["y"] + item["h"] + gap_y
            ):
                x1 = min(group["x"], item["x"])
                y1 = min(group["y"], item["y"])
                x2 = max(group["x"] + group["w"], item["x"] + item["w"])
                y2 = max(group["y"] + group["h"], item["y"] + item["h"])
                if (x1, y1, x2, y2) != (
                    group["x"],
                    group["y"],
                    group["x"] + group["w"],
                    group["y"] + group["h"],
                ):
                    group.update({"x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1})
                    group["area"] = group.get("area", 0) + item.get("area", 0)
                    changed = True
    return group


# ---------------------------------------------------------------------------
# Zone construction
# ---------------------------------------------------------------------------


def signature_zone(
    image: DocumentImage,
    ocr: OcrResult,
    spec: DocumentTypeSpec,
    seal_zone: Zone | None = None,
) -> Zone | None:
    """Zone where a signature is expected.

    Anchoring rules (no absolute page coordinates are used):

    * start **after the last word of the printed caption**, so the caption glyphs can
      never be mistaken for handwriting;
    * if a horizontal signature rule is printed next to the caption, the rule's span
      is the most reliable indicator of where a signature may be placed;
    * the zone never extends into the seal's bounding box.
    """
    height, width = image.height, image.width
    words = [word for word in find_signature_words(ocr) if word.cy >= height * 0.45]
    if not words:
        return _signature_zone_from_rule(image, seal_zone)
    label = words[0]
    line = ocr.line_at(label.cy)
    raw_words = line.words if line is not None else [label]
    # OCR often merges the seal print ("ПЕЧАТЬ", "МАКЕТ") into the caption line.
    # Those words are not part of the caption: they must not push the signature zone.
    caption_words = [word for word in raw_words if not _is_seal_print(word)]
    if not caption_words:
        caption_words = [label]
    # Keep only words reasonably close to the caption word, so a distant print block
    # on the same OCR line cannot widen the caption.
    limit_x = label.left + max(label.width, 40) * 3.2
    caption_words = [word for word in caption_words if word.left <= limit_x] or [label]
    caption_bottom = max(word.bottom for word in caption_words)
    caption_height = max(
        max(word.height for word in caption_words),
        line.height if line is not None else label.height,
        12,
    )
    caption_right = min(max(word.right for word in caption_words), width)

    top = max(0, int(caption_bottom - caption_height * 2.0))
    bottom = min(height, int(caption_bottom + caption_height * 1.1))

    left = max(0, caption_right + 6)
    right = width
    if seal_zone is not None:
        # The seal mock-up must never be counted as a signature.
        right = max(left + 40, min(right, seal_zone.x - 6))
    zone = Zone(x=left, y=top, w=max(0, right - left), h=max(0, bottom - top), anchors=[label.text])

    # Prefer the printed signature rule as the horizontal extent of the zone.
    rules = find_horizontal_rules(image.grey)
    for rule in rules:
        # A short piece is a rule partly hidden under the signature itself; its
        # extent would cut the signature off, so only a whole rule is trusted.
        if rule.w < max(80, int(width * 0.12)):
            continue
        if not (rule.y2 >= top - 6 and rule.y <= bottom + 6):
            continue
        if rule.x2 <= caption_right:
            continue
        if seal_zone is not None and rule.x >= seal_zone.x2 - 8:
            # The rule lies inside/right of the seal area: not a signature rule.
            continue
        zone_left = max(zone.x, rule.x - 4)
        zone_top = min(zone.y, rule.y - int(caption_height * 0.8))
        zone_right = min(right, rule.x2 + 4)
        zone_bottom = max(bottom, rule.y2 + int(caption_height * 0.5))
        zone = Zone(
            x=zone_left,
            y=zone_top,
            w=max(0, zone_right - zone_left),
            h=max(0, zone_bottom - zone_top),
            anchors=zone.anchors + ["rule"],
        )
        break
    return zone


def _signature_zone_from_rule(image: DocumentImage, seal_zone: Zone | None) -> Zone | None:
    """Fallback when OCR lost the caption (blurred photo): the printed signature
    rule in the lower part of the page still marks where a signature goes."""
    height, width = image.height, image.width
    candidates = [
        rule
        for rule in find_horizontal_rules(image.grey)
        if rule.y >= height * 0.55
        and rule.y <= height * 0.92
        and 0.10 * width <= rule.w <= 0.45 * width
        and (seal_zone is None or rule.x2 <= seal_zone.x + 8)
    ]
    if not candidates:
        # No caption, no rule: the lower band left of the seal is the only place a
        # signature can be; the ink detectors below still demand a real stroke.
        right = seal_zone.x - 6 if seal_zone is not None else int(width * 0.62)
        top = int(height * 0.55)
        return Zone(x=int(width * 0.03), y=top, w=max(0, right - int(width * 0.03)),
                    h=int(height * 0.37), anchors=["band"])
    rule = min(candidates, key=lambda item: item.y)
    pad_y = max(12, int(height * 0.03))
    return Zone(
        x=max(0, rule.x - 4),
        y=max(0, rule.y - pad_y),
        w=rule.w + 8,
        h=pad_y + int(height * 0.012),
        anchors=["rule"],
    )


def seal_zone(image: DocumentImage, ocr: OcrResult, spec: DocumentTypeSpec) -> Zone | None:
    """Zone of the seal mock-up: right side, centred on the lowest seal caption.

    Only captions in the lower half of the page are considered, so the footer note
    «… печать являются учебными макетами» cannot move the zone upwards.
    """
    height, width = image.height, image.width
    points = [
        word for word in find_seal_words(ocr) if word.cy >= height * 0.5
    ]
    if not points:
        return None
    # «М.П.» is the dedicated place-of-seal caption: prefer it when present.
    marked = [word for word in points if norm_text(word.text).strip(" .:;,") in {"м.п", "мп", "м п"}]
    if marked:
        anchor = max(marked, key=lambda word: word.cy)
        depth = int(height * 0.115)
        top = max(0, int(anchor.top - depth))
        bottom = min(height, int(anchor.bottom + height * 0.02))
        left = max(0, int(anchor.left - anchor.width * 2.2))
        if anchor.width < 3:  # pragma: no cover - OCR returned a degenerate box
            left = int(width * 0.72)
        return Zone(x=left, y=top, w=max(0, width - left), h=max(0, bottom - top), anchors=[anchor.text])

    lowest = max(points, key=lambda word: word.cy)
    depth = int(height * 0.24)
    top = max(0, int(lowest.top - depth))
    bottom = min(height, int(lowest.bottom + height * 0.035))
    left = max(int(width * 0.40), int(min(word.left for word in points) - width * 0.28))
    return Zone(x=left, y=top, w=max(0, width - left), h=max(0, bottom - top), anchors=[w.text for w in points])


# ---------------------------------------------------------------------------
# Signature evidence
# ---------------------------------------------------------------------------


def _is_printed_glyph(item: dict, median_height: float) -> bool:
    """True for components whose shape matches a printed character.

    The caption itself is excluded by geometry (the zone starts after it), but a
    caption printed by another layout variant, a header/footer note or the seal
    text can still fall inside the zone.  A single glyph is small in both
    directions; handwriting is wide (or has wide letter spacing) even when short.
    """
    width_limit = max(MIN_MARK_WIDTH, median_height * 3.0)
    height_limit = max(24.0, median_height * 1.8)
    return item["w"] < width_limit and item["h"] < height_limit


def _signature_from_mask(
    mask: np.ndarray,
    image: DocumentImage,
    zone: Zone,
    method: str,
    min_components: int = 1,
) -> dict | None:
    """Shared plausibility logic for a signature candidate inside ``zone``."""
    if mask.sum() == 0:
        return None
    components = _component_stats(mask, min_area=8)
    heights = [line_box["h"] for line_box in components] or [20]
    heights.sort()
    median_height = float(heights[len(heights) // 2])
    kept = [item for item in components if not _is_printed_glyph(item, median_height)]
    merged = _merge_boxes(kept, gap_x=max(14, zone.h), gap_y=max(10, zone.h // 2))
    if merged is None or len(kept) < min_components:
        return None
    pixels = int(
        mask[merged["y"] : merged["y"] + merged["h"], merged["x"] : merged["x"] + merged["w"]].sum()
    )
    width_ratio = merged["w"] / max(image.width, 1)
    height_ratio = merged["h"] / max(image.height, 1)
    plausible = (
        pixels >= MIN_MARK_PIXELS
        and merged["w"] >= MIN_MARK_WIDTH
        and merged["h"] >= MIN_MARK_HEIGHT
        and width_ratio <= MAX_MARK_WIDTH_RATIO
        and height_ratio <= MAX_MARK_HEIGHT_RATIO
    )
    return {
        "method": method,
        "pixels": pixels,
        "box": (merged["x"], merged["y"], merged["w"], merged["h"]),
        "strength": min(1.0, pixels / 600.0),
        "component_count": len(components),
        "kept_components": len(kept),
        "plausible": plausible,
    }


def _signature_from_blue(image: DocumentImage, zone: Zone, seal_zone_box: Zone | None) -> dict | None:
    blue = blue_ink_mask(image.rgb)
    mask = np.zeros_like(blue, dtype=bool)
    mask[zone.y : zone.y2, zone.x : zone.x2] = blue[zone.y : zone.y2, zone.x : zone.x2]
    if seal_zone_box is not None:
        mask[seal_zone_box.y : seal_zone_box.y2, seal_zone_box.x : seal_zone_box.x2] = False
    return _signature_from_mask(mask, image, zone, "colour")


def _signature_from_greyscale(
    image: DocumentImage,
    ocr: OcrResult,
    zone: Zone,
    seal_box: Zone | None,
    rules: list[Zone],
) -> dict | None:
    """Fallback for monochrome scans: look for irregular (handwritten) strokes."""
    grey = image.grey
    zone_mask = np.zeros(grey.shape, dtype=bool)
    zone_mask[zone.y : zone.y2, zone.x : zone.x2] = True
    if seal_box is not None:
        zone_mask[seal_box.y : seal_box.y2, seal_box.x : seal_box.x2] = False
    dark = dark_ink_mask(grey) & zone_mask
    # Drop long thin rules (smooth and would mask the ink lying on top of them).
    for rule in rules:
        if rule.intersects(zone):
            dark[rule.y - 1 : rule.y2 + 2, rule.x : rule.x2] = False
    components = _component_stats(dark, min_area=10)
    heights = sorted(item["h"] for item in components) or [20]
    median_height = float(heights[len(heights) // 2])
    kept = []
    for item in components:
        if _is_printed_glyph(item, median_height):
            continue
        patch = np.zeros_like(dark, dtype=bool)
        patch[item["y"] : item["y"] + item["h"], item["x"] : item["x"] + item["w"]] = dark[
            item["y"] : item["y"] + item["h"], item["x"] : item["x"] + item["w"]
        ]
        rough = _roughness(patch)
        fill = item["area"] / max(item["w"] * item["h"], 1)
        # Printed words are dense and smooth; handwriting is sparse and jagged.
        if fill > 0.34 and rough < 60.0:
            continue
        kept.append(item)
    if not kept:
        return None
    evidence = _signature_from_mask(dark, image, zone, "greyscale")
    if evidence is None:
        return None
    box = evidence["box"]
    patch = np.zeros_like(dark, dtype=bool)
    patch[box[1] : box[1] + box[3], box[0] : box[0] + box[2]] = dark[
        box[1] : box[1] + box[3], box[0] : box[0] + box[2]
    ]
    evidence["roughness"] = round(_roughness(patch), 2)
    return evidence


# ---------------------------------------------------------------------------
# Seal evidence
# ---------------------------------------------------------------------------


def _circle_confirmation(mask: np.ndarray) -> int:
    if mask.sum() == 0 or min(mask.shape) < 24:
        return 0
    patch = (mask.astype(np.uint8)) * 255
    patch = cv2.morphologyEx(patch, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    circles = cv2.HoughCircles(
        patch,
        cv2.HOUGH_GRADIENT,
        dp=1.5,
        minDist=max(20, min(patch.shape) * 0.4),
        param1=90,
        param2=22,
        minRadius=int(min(patch.shape) * 0.12),
        maxRadius=int(min(patch.shape) * 0.65),
    )
    if circles is None:
        return 0
    return int(len(circles[0]))


def _seal_from_mask(mask: np.ndarray, image: DocumentImage, method: str) -> dict | None:
    components = _component_stats(mask, min_area=40)
    if not components:
        return None
    min_diameter = max(20, int(min(image.width, image.height) * MIN_SEAL_DIAMETER_RATIO))
    max_diameter = int(min(image.width, image.height) * MAX_SEAL_DIAMETER_RATIO)
    best = None
    for item in components:
        diameter = max(item["w"], item["h"])
        if diameter < min_diameter or diameter > max_diameter:
            continue
        fill = item["area"] / max(item["w"] * item["h"], 1)
        aspect = item["w"] / max(item["h"], 1)
        if fill < SEAL_MIN_FILL or not (0.55 <= aspect <= 1.8):
            continue
        score = min(1.0, item["area"] / 900.0) * (1.0 if fill < 0.4 else 0.5)
        candidate = {
            "method": method,
            "box": (item["x"], item["y"], item["w"], item["h"]),
            "area": item["area"],
            "fill": round(fill, 4),
            "strength": score,
            "plausible": True,
        }
        if best is None or candidate["area"] > best["area"]:
            best = candidate
    if best is None:
        return None
    patch = np.zeros(mask.shape, dtype=bool)
    x, y, w, h = best["box"]
    patch[y : y + h, x : x + w] = mask[y : y + h, x : x + w]
    best["circles"] = _circle_confirmation(patch)
    return best


def _seal_from_blue(image: DocumentImage, zone: Zone) -> dict | None:
    blue = blue_ink_mask(image.rgb)
    mask = np.zeros(blue.shape, dtype=bool)
    mask[zone.y : zone.y2, zone.x : zone.x2] = blue[zone.y : zone.y2, zone.x : zone.x2]
    return _seal_from_mask(mask, image, "colour")


def _seal_from_greyscale(image: DocumentImage, zone: Zone) -> dict | None:
    grey = image.grey
    mask = np.zeros(grey.shape, dtype=bool)
    mask[zone.y : zone.y2, zone.x : zone.x2] = dark_ink_mask(grey)[zone.y : zone.y2, zone.x : zone.x2]
    candidate = _seal_from_mask(mask, image, "greyscale")
    if candidate is None:
        return None
    # Printed words inside the region would not survive a ring test; keep them out
    # by requiring either a ring-like fill ratio or a Hough-circle confirmation.
    if candidate["fill"] > 0.62 and candidate["circles"] < 1:
        return None
    return candidate


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass
class MarkVerdict:
    key: str
    present: bool
    confidence: float
    box: tuple[int, int, int, int] | None = None
    method: str = ""
    evidence: dict = field(default_factory=dict)


def detect_marks(
    image: DocumentImage,
    ocr_boxes: OcrResult,
    document_type: str | DocumentTypeSpec,
    spec: DocumentTypeSpec | None = None,
) -> dict[str, MarkVerdict]:
    """Detect graphic signature/seal marks for a document type.

    Parameters
    ----------
    image:
        Loaded page (RGB + greyscale).  Colour is used when available; the
        greyscale fallback keeps working on monochrome scans.
    ocr_boxes:
        OCR output — used *only* to localise zones by printed captions.
    document_type:
        Type code (``study_certificate`` …) or a :class:`DocumentTypeSpec`.
    spec:
        Optional pre-loaded spec (avoids a config lookup).
    """
    if spec is None:
        if isinstance(document_type, DocumentTypeSpec):
            spec = document_type
        else:
            from .document_config import get_registry

            spec = get_registry().require(str(document_type))

    rules = find_horizontal_rules(image.grey)
    seal_zone_box = seal_zone(image, ocr_boxes, spec) if spec.has_seal else None
    sig_zone = signature_zone(image, ocr_boxes, spec, seal_zone=seal_zone_box)

    verdicts: dict[str, MarkVerdict] = {}

    # ---------------- signature ----------------
    signature_evidence: dict = {}
    if sig_zone is not None:
        for collector in (
            lambda: _signature_from_blue(image, sig_zone, seal_zone_box),
            lambda: (
                # A caption-less band also holds printed text: only coloured ink counts there.
                None
                if sig_zone.anchors == ["band"]
                else _signature_from_greyscale(image, ocr_boxes, sig_zone, seal_zone_box, rules)
            ),
        ):
            try:
                evidence = collector()
            except Exception:  # noqa: BLE001 - a failed heuristic must not break the pipeline
                evidence = None
            if evidence and evidence.get("plausible"):
                signature_evidence = evidence
                break
    signature_present = bool(signature_evidence)
    signature_conf = round(float(signature_evidence.get("strength", 0.0)), 3) if signature_present else 0.0
    sig_box = signature_evidence.get("box") if signature_evidence else None
    if signature_present and sig_box and seal_zone_box is not None:
        # The seal is a different mark: require a minimum separation.
        if not (
            sig_box[0] + sig_box[2] <= seal_zone_box.x + 4 or sig_box[0] >= seal_zone_box.x2 - 4
        ):
            overlap_width = min(sig_box[0] + sig_box[2], seal_zone_box.x2) - max(sig_box[0], seal_zone_box.x)
            if overlap_width > 0.5 * sig_box[2]:
                signature_present = False
                signature_conf = 0.0
                sig_box = None

    # ---------------- seal ----------------
    seal_evidence: dict = {}
    if spec.has_seal and seal_zone_box is not None:
        for collector in (
            lambda: _seal_from_blue(image, seal_zone_box),
            lambda: _seal_from_greyscale(image, seal_zone_box),
        ):
            try:
                evidence = collector()
            except Exception:  # noqa: BLE001
                evidence = None
            if evidence and evidence.get("plausible"):
                seal_evidence = evidence
                break
    seal_present = bool(seal_evidence)
    seal_conf = 0.0
    if seal_present:
        base = float(seal_evidence.get("strength", 0.5))
        seal_conf = round(min(1.0, base + 0.15 * seal_evidence.get("circles", 0)), 3)

    for mark in spec.marks:
        if mark.key == "signature_present":
            verdicts[mark.key] = MarkVerdict(
                key=mark.key,
                present=signature_present,
                confidence=signature_conf,
                box=sig_box,
                method=signature_evidence.get("method", ""),
                evidence=signature_evidence,
            )
        elif mark.key == "seal_present":
            verdicts[mark.key] = MarkVerdict(
                key=mark.key,
                present=seal_present,
                confidence=seal_conf,
                box=seal_evidence.get("box"),
                method=seal_evidence.get("method", ""),
                evidence=seal_evidence,
            )
    return verdicts


def detect_marks_dict(
    image: DocumentImage,
    ocr_boxes: OcrResult,
    document_type: str | DocumentTypeSpec,
    spec: DocumentTypeSpec | None = None,
) -> dict[str, bool]:
    """Convenience wrapper returning only the boolean flags."""
    verdicts = detect_marks(image, ocr_boxes, document_type, spec=spec)
    return {key: verdict.present for key, verdict in verdicts.items()}
