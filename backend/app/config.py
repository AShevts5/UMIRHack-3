"""Runtime configuration for the UMIR 3 "Онлайн-деканат" service.

Everything here is local: no external OCR/LLM endpoints are contacted anywhere in
this project.  Values are read from environment variables so the same code runs on
a laptop (Windows/Linux/macOS) and in a free Colab runtime.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Repository layout
# ---------------------------------------------------------------------------
PACKAGE_ROOT = Path(__file__).resolve().parent  # .../backend/app
BACKEND_ROOT = PACKAGE_ROOT.parent  # .../backend
PROJECT_ROOT = BACKEND_ROOT.parent  # .../umir-online-dean


def _env_path(*names: str) -> Path | None:
    for name in names:
        raw = os.environ.get(name)
        if raw:
            candidate = Path(raw).expanduser()
            if candidate.exists():
                return candidate
    return None


def _default_tessdata() -> Path | None:
    """Locate a tessdata directory that contains the Russian traineddata."""
    candidates = []
    env = os.environ.get("TESSDATA_PREFIX")
    if env:
        candidates.append(Path(env))
    binary = find_tesseract_binary()
    if binary is not None:
        candidates.append(binary.parent)
        candidates.append(binary.parent / "share" / "tessdata")
        candidates.append(binary.parent.parent / "share" / "tessdata")
    candidates += [
        Path("/usr/share/tesseract-ocr/5/tessdata"),
        Path("/usr/share/tesseract-ocr/4.00/tessdata"),
        Path("/usr/share/tessdata"),
        Path("/usr/local/share/tessdata"),
        Path(r"C:\Program Files\Tesseract-OCR\tessdata"),
        Path(r"C:\Program Files (x86)\Tesseract-OCR\tessdata"),
    ]
    for candidate in candidates:
        try:
            if candidate.is_dir() and (candidate / "rus.traineddata").is_file():
                return candidate
        except OSError:  # pragma: no cover - defensive
            continue
    return None


def find_tesseract_binary() -> Path | None:
    """Return the local Tesseract executable, if one can be found."""
    explicit = os.environ.get("TESSERACT_CMD")
    if explicit:
        candidate = Path(explicit).expanduser()
        if candidate.is_file():
            return candidate
    found = shutil.which("tesseract")
    if found:
        return Path(found)
    for candidate in (
        Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
        Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
        Path(r"C:\msys64\ucrt64\bin\tesseract.exe"),
        Path("/usr/bin/tesseract"),
        Path("/usr/local/bin/tesseract"),
        Path("/opt/homebrew/bin/tesseract"),
    ):
        try:
            if candidate.is_file():
                return candidate
        except OSError:  # pragma: no cover
            continue
    return None


@dataclass(frozen=True)
class OcrSettings:
    """Tesseract/OpenCV knobs.

    Defaults are not guesswork: they come from the sweep recorded in
    ``benchmarks/README.md`` (30 pages of the open set, page-level character
    accuracy, field accuracy, speed, plus a synthetic-page check that catches
    dropped text blocks):

    ==========================  ============  ==========  ==============  ==========
    configuration               char accuracy  fields      s / page        synthetic
    ==========================  ============  ==========  ==============  ==========
    psm 6 + Otsu + 2200 (old)   0.7753        1.000       3.41            ok
    **psm 3, no binarization**  **0.9075**    **1.000**   **2.92**        **ok**
    psm 4, no binarization      0.9063        1.000       2.87            ok
    psm 6, no binarization      0.8713        1.000       2.61            ok
    psm 11 / psm 12, no binar.  0.9114        1.000       2.74 / 3.28     **loses marks**
    ==========================  ============  ==========  ==============  ==========

    Two measured conclusions:

    1. Otsu binarization **hurt** on this material.  It erodes the thin
       anti-aliased strokes: character accuracy drops from 0.8713 to 0.7753, and
       the standalone «М.П.» caption survives on only 20 of 30 pages instead of 30,
       which drags the seal zone leftwards.  ``use_binarization`` therefore defaults
       to ``False``, but stays switchable (``UMIR_OCR_BINARIZE=1``) for noisy real
       photos — material this set does not measure.
    2. Layout modes that score higher still break the marker pipeline: ``psm 3``
       reaches 0.9075 yet stops recognising the isolated «М.П.», and ``psm 11``/``12``
       (0.9114) drop the whole signature/seal caption block on synthetic pages.  Both
       silently disable signature detection, so ``psm 6`` — which keeps every caption
       (30/30 pages) and every synthetic case — is the default.  Anchors are never
       guessed from absolute coordinates.
    """

    languages: str = os.environ.get("UMIR_OCR_LANGS", "rus+eng")
    oem: int = 1  # LSTM engine only
    psm: int = int(os.environ.get("UMIR_OCR_PSM", "6"))
    target_long_edge: int = 2200  # upscale factor is derived from the page size
    upscale_min: float = 1.0
    upscale_max: float = 3.0
    use_binarization: bool = os.environ.get("UMIR_OCR_BINARIZE", "0") not in {"0", "false", "no"}
    tesseract_timeout_s: float = 60.0
    # Marker (signature/seal) analysis is performed on the *raw* greyscale image
    # so that faint strokes are not destroyed by binarization.
    keep_original_greyscale: bool = True


@dataclass(frozen=True)
class Limits:
    """Input hardening: size, page count and MIME constraints."""

    max_upload_bytes: int = 12 * 1024 * 1024
    max_pdf_pages: int = 1
    max_image_pixels: int = 40_000_000
    allowed_suffixes: tuple[str, ...] = (".png", ".jpg", ".jpeg", ".pdf")
    allowed_mime: tuple[str, ...] = (
        "image/png",
        "image/jpeg",
        "image/jpg",
        "application/pdf",
    )


@dataclass(frozen=True)
class Settings:
    ocr: OcrSettings = field(default_factory=OcrSettings)
    limits: Limits = field(default_factory=Limits)
    tesseract_cmd: Path | None = field(default_factory=find_tesseract_binary)
    tessdata_prefix: Path | None = field(default_factory=_default_tessdata)
    document_types_config: Path = field(
        default_factory=lambda: Path(
            os.environ.get(
                "UMIR_DOCUMENT_TYPES",
                str(PACKAGE_ROOT / "configs" / "document_types.yaml"),
            )
        )
    )
    # Never log document contents in full; the pipeline only logs counters.
    log_full_text: bool = False


settings = Settings()
