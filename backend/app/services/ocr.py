"""Local OCR: Tesseract (rus+eng) over the preprocessed page, with word boxes.

Design notes
------------
* OCR runs **locally** through the ``tesseract`` binary; no image ever leaves the
  machine and no remote OCR/LLM service is called.
* Preprocessing only rescales and (optionally) binarizes.  Aggressive filtering is
  avoided because it destroys the thin signature strokes analysed later.
* The original greyscale image is kept untouched for marker detection: words with
  boxes come from the enhanced copy, geometry-sensitive analysis uses the raw one.
* The Tesseract process is started once per document; results are cached per file
  hash by the caller (`pipeline`), never per API request.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
import pytesseract
from pytesseract import Output

from ..config import OcrSettings, find_tesseract_binary, settings, _default_tessdata
from .loader import DocumentImage

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Word:
    text: str
    left: int
    top: int
    width: int
    height: int
    conf: float
    line_id: int = -1
    block_id: int = -1
    par_id: int = -1

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    @property
    def cx(self) -> float:
        return self.left + self.width / 2.0

    @property
    def cy(self) -> float:
        return self.top + self.height / 2.0

    @property
    def box(self) -> tuple[int, int, int, int]:
        return (self.left, self.top, self.width, self.height)


@dataclass
class TextLine:
    """A logical text line rebuilt from word boxes (layout independent)."""

    words: list[Word] = field(default_factory=list)
    block_id: int = -1

    @property
    def text(self) -> str:
        return " ".join(word.text for word in self.words)

    @property
    def left(self) -> int:
        return min(word.left for word in self.words)

    @property
    def right(self) -> int:
        return max(word.right for word in self.words)

    @property
    def top(self) -> int:
        return min(word.top for word in self.words)

    @property
    def bottom(self) -> int:
        return max(word.bottom for word in self.words)

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def cy(self) -> float:
        return (self.top + self.bottom) / 2.0

    @property
    def box(self) -> tuple[int, int, int, int]:
        return (self.left, self.top, self.right - self.left, self.bottom - self.top)

    def text_after(self, x: int) -> str:
        """Concatenate words starting at/after pixel column ``x``."""
        return " ".join(word.text for word in self.words if word.left >= x - 2)


@dataclass
class OcrResult:
    text: str
    lines: list[TextLine]
    words: list[Word]
    scale: float
    mean_conf: float
    engine: str = "tesseract"

    def line_at(self, y: int) -> TextLine | None:
        best, best_dy = None, None
        for line in self.lines:
            if line.top - 6 <= y <= line.bottom + 6:
                dy = abs(line.cy - y)
                if best_dy is None or dy < best_dy:
                    best, best_dy = line, dy
        return best

    def lines_matching(self, pattern: str, flags: int = re.IGNORECASE) -> list[TextLine]:
        regex = re.compile(pattern, flags)
        return [line for line in self.lines if regex.search(line.text)]

    def first_match(self, pattern: str, flags: int = re.IGNORECASE) -> tuple[TextLine, re.Match] | None:
        regex = re.compile(pattern, flags)
        for line in self.lines:
            match = regex.search(line.text)
            if match:
                return line, match
        return None


class OcrUnavailableError(RuntimeError):
    """Raised when the local Tesseract binary/language data cannot be used."""


# ---------------------------------------------------------------------------
# Preprocessing
# ---------------------------------------------------------------------------


def _upscale_factor(shape: tuple[int, int], target_long_edge: int, low: float, high: float) -> float:
    long_edge = max(shape[0], shape[1])
    if long_edge <= 0:
        return 1.0
    factor = target_long_edge / float(long_edge)
    return float(min(max(factor, low), high))


def preprocess_for_ocr(grey: np.ndarray, ocfg: OcrSettings, quality: str = "clean") -> tuple[np.ndarray, float]:
    """Return (enhanced greyscale image, scale factor applied to the original).

    Clean scans are binarized (measured best on the open set).  Photos keep their
    grey levels: after relighting, Otsu breaks thin strokes of small or blurred
    glyphs, while the LSTM model reads anti-aliased grey text well.  Upscaled
    low-resolution photos are enlarged further and sharpened.
    """
    if quality == "lowres":
        scale = _upscale_factor(grey.shape, int(ocfg.target_long_edge * 1.6), ocfg.upscale_min, ocfg.upscale_max)
        image = cv2.resize(grey, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        soft = cv2.GaussianBlur(image, (0, 0), 3)
        image = cv2.addWeighted(image, 1.8, soft, -0.8, 0)
        image = cv2.copyMakeBorder(image, 24, 24, 24, 24, cv2.BORDER_CONSTANT, value=255)
        return image, scale
    scale = _upscale_factor(grey.shape, ocfg.target_long_edge, ocfg.upscale_min, ocfg.upscale_max)
    if abs(scale - 1.0) > 1e-3:
        interpolation = cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA
        image = cv2.resize(grey, None, fx=scale, fy=scale, interpolation=interpolation)
    else:
        image = grey
    if ocfg.use_binarization and quality == "clean":
        # Otsu keeps anti-aliased text edges; a light median blur removes scanner noise.
        blurred = cv2.medianBlur(image, 3)
        _, image = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # Add a white border: Tesseract reliably misses glyphs glued to the page edge.
    image = cv2.copyMakeBorder(image, 24, 24, 24, 24, cv2.BORDER_CONSTANT, value=255)
    return image, scale


# ---------------------------------------------------------------------------
# Tesseract invocation
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def ocr_environment() -> dict[str, str]:
    """Resolve Tesseract binary/tessdata once per process."""
    import os

    binary = find_tesseract_binary()
    if binary is None:
        raise OcrUnavailableError(
            "Не найден локальный Tesseract. Установите его (README, раздел «Установка OCR»)."
        )
    # One Tesseract thread per call: OpenMP threads of parallel calls (page + value
    # crops, API + batch) oversubscribe the CPU and can stall a call past its timeout.
    os.environ.setdefault("OMP_THREAD_LIMIT", "1")
    env = dict(os.environ)
    tessdata = settings.tessdata_prefix or _default_tessdata()
    if tessdata is not None:
        env["TESSDATA_PREFIX"] = str(tessdata)
        pytesseract.pytesseract.tesseract_cmd = str(binary)
    else:
        pytesseract.pytesseract.tesseract_cmd = str(binary)
    return env


@lru_cache(maxsize=1)
def available_languages() -> tuple[str, ...]:
    try:
        ocr_environment()
        return tuple(pytesseract.get_languages(config=""))
    except Exception:  # noqa: BLE001 - OCR availability probe must never raise
        return ()


def resolve_languages(requested: str) -> str:
    """Drop language codes whose traineddata is missing instead of failing."""
    installed = set(available_languages())
    if not installed:
        return requested
    wanted = [code.strip() for code in requested.split("+") if code.strip()]
    usable = [code for code in wanted if code in installed]
    if not usable:
        if "eng" in installed:
            return "eng"
        return sorted(installed)[0]
    return "+".join(usable)


def _build_words(data: dict, scale: float, min_conf: float) -> list[Word]:
    words: list[Word] = []
    border = 24
    count = len(data.get("text", []))
    for index in range(count):
        raw = (data["text"][index] or "").strip()
        if not raw:
            continue
        try:
            conf = float(data["conf"][index])
        except (TypeError, ValueError):
            conf = -1.0
        if conf < min_conf:
            continue
        # Page edges and table borders come out as lone bars; they are never text.
        if re.fullmatch(r"[|¦!\[\]{}()‘’'`,.]{1,2}", raw) and raw not in (".",):
            continue
        left = int(round(int(data["left"][index]) / scale)) - int(round(border / scale))
        top = int(round(int(data["top"][index]) / scale)) - int(round(border / scale))
        width = int(round(int(data["width"][index]) / scale))
        height = int(round(int(data["height"][index]) / scale))
        words.append(
            Word(
                text=raw,
                left=left,
                top=top,
                width=width,
                height=height,
                conf=conf,
                line_id=int(data["line_num"][index]),
                block_id=int(data["block_num"][index]),
                par_id=int(data["par_num"][index]),
            )
        )
    return words


def _group_into_lines(words: list[Word]) -> list[TextLine]:
    """Group words into text lines using pixel geometry, not Tesseract blocks.

    Tesseract block segmentation varies with layout; geometry-based grouping is
    stable for the field layouts used by the case and keeps label/value pairs on a
    single line even when the page has floating seal graphics.
    """
    if not words:
        return []
    heights = sorted(word.height for word in words)
    median_height = heights[len(heights) // 2] or 12
    tolerance = max(6.0, median_height * 0.6)

    ordered = sorted(words, key=lambda word: (word.top, word.left))
    rows: list[list[Word]] = []
    for word in ordered:
        placed = False
        for row in rows:
            row_cy = sum(item.cy for item in row) / len(row)
            row_height = max(item.height for item in row)
            if abs(word.cy - row_cy) <= max(tolerance, row_height * 0.55):
                row.append(word)
                placed = True
                break
        if not placed:
            rows.append([word])
    lines = []
    for row in rows:
        row.sort(key=lambda word: word.left)
        lines.append(TextLine(words=row, block_id=row[0].block_id))
    lines.sort(key=lambda line: (line.top, line.left))
    return lines


def _page_text(lines: list[TextLine]) -> str:
    return "\n".join(line.text for line in lines if line.text.strip())


def run_ocr(
    image: DocumentImage,
    ocfg: OcrSettings | None = None,
    min_conf: float = 25.0,
) -> OcrResult:
    """Run Tesseract on a document image and return text plus word boxes."""
    ocfg = ocfg or settings.ocr
    ocr_environment()
    enhanced, scale = preprocess_for_ocr(image.grey, ocfg, getattr(image, "quality", "clean"))
    languages = resolve_languages(ocfg.languages)
    config = f"--oem {ocfg.oem} --psm {ocfg.psm} -c preserve_interword_spaces=1"
    try:
        data = pytesseract.image_to_data(
            enhanced,
            lang=languages,
            config=config,
            output_type=Output.DICT,
            timeout=ocfg.tesseract_timeout_s,
        )
    except RuntimeError as exc:  # pytesseract raises RuntimeError on timeout
        raise OcrUnavailableError(f"OCR не завершился: {exc}") from exc
    except pytesseract.TesseractNotFoundError as exc:  # pragma: no cover
        raise OcrUnavailableError("Tesseract не найден в PATH.") from exc

    words = _build_words(data, scale, min_conf)
    lines = _group_into_lines(words)
    confs = [word.conf for word in words if word.conf >= 0]
    mean_conf = round(sum(confs) / len(confs), 2) if confs else 0.0
    return OcrResult(
        text=_page_text(lines),
        lines=lines,
        words=words,
        scale=scale,
        mean_conf=mean_conf,
    )


def ocr_health() -> dict:
    """Diagnostics for ``GET /api/v1/health`` — never raises."""
    binary = find_tesseract_binary()
    info: dict = {
        "engine": "tesseract",
        "binary": str(binary) if binary else None,
        "available": False,
        "languages": [],
        "tessdata": None,
        "version": None,
    }
    if binary is None:
        info["error"] = "tesseract binary not found"
        return info
    tessdata = _default_tessdata()
    info["tessdata"] = str(tessdata) if tessdata else None
    try:
        ocr_environment()
        languages = list(available_languages())
        info["languages"] = languages
        info["available"] = "rus" in languages
        try:
            info["version"] = str(pytesseract.get_tesseract_version())
        except Exception:  # noqa: BLE001
            info["version"] = None
        if not info["available"]:
            info["error"] = "russian traineddata (rus.traineddata) is missing"
    except Exception as exc:  # noqa: BLE001
        info["error"] = str(exc)
    return info


def warm_up() -> dict:
    """Touch the OCR engine once so the first API request is not slowed down."""
    return ocr_health()


def image_path_to_document(path: str | Path) -> DocumentImage:  # pragma: no cover - convenience
    from .loader import load_document

    return load_document(path)
