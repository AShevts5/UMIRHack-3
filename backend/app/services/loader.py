"""Safe input loading: PNG/JPG/PDF -> RGB numpy array + greyscale.

Security/robustness rules implemented here (see README, section «Ограничения»):

* only ``.png``, ``.jpg``, ``.jpeg`` and ``.pdf`` are accepted;
* file size, image pixel count, and PDF page count are bounded;
* PDF is rendered locally with PyMuPDF — no network, no external renderer;
* a broken/encrypted file produces the typed :class:`UnsupportedDocumentError`
  instead of a traceback;
* nothing is written to disk unless the caller explicitly asks for a temp file.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageFile, ImageOps

from ..config import Limits, settings

ImageFile.LOAD_TRUNCATED_IMAGES = False
Image.MAX_IMAGE_PIXELS = None  # we enforce our own limit

PDF_RENDER_DPI = 200


class DocumentLoadError(Exception):
    """Raised for any unacceptable or unreadable input document."""


@dataclass
class DocumentImage:
    """A single-page document image ready for OCR and marker analysis."""

    rgb: np.ndarray  # HxWx3, uint8
    grey: np.ndarray  # HxW, uint8
    source_suffix: str
    source_name: str
    page_count: int = 1
    render_dpi: int | None = None
    inverted_preview: bool = False
    #: "clean" scan/render, "photo" (straightened/relit) or "lowres" (upscaled);
    #: set by enhance.py and used by the OCR preprocessing.
    quality: str = "clean"

    @property
    def height(self) -> int:
        return int(self.rgb.shape[0])

    @property
    def width(self) -> int:
        return int(self.rgb.shape[1])

    def to_png_bytes(self) -> bytes:
        buffer = io.BytesIO()
        Image.fromarray(self.rgb).save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()


def _check_size(num_bytes: int, limits: Limits) -> None:
    if num_bytes <= 0:
        raise DocumentLoadError("Файл пустой.")
    if num_bytes > limits.max_upload_bytes:
        raise DocumentLoadError(
            f"Файл больше допустимого размера ({limits.max_upload_bytes // (1024 * 1024)} МБ)."
        )


def _validate_suffix(name: str, limits: Limits) -> str:
    suffix = Path(name).suffix.lower()
    if suffix not in limits.allowed_suffixes:
        allowed = ", ".join(limits.allowed_suffixes)
        raise DocumentLoadError(f"Формат {suffix or 'без расширения'} не поддерживается. Допустимо: {allowed}.")
    return suffix


def _from_pil(image: Image.Image) -> np.ndarray:
    image = ImageOps.exif_transpose(image)
    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGBA")
        background = Image.new("RGBA", image.size, (255, 255, 255, 255))
        image = Image.alpha_composite(background, image)
    image = image.convert("RGB")
    return np.asarray(image, dtype=np.uint8)


def _render_pdf(data: bytes, limits: Limits, name: str) -> tuple[np.ndarray, int, int]:
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise DocumentLoadError(
            "Для обработки PDF требуется PyMuPDF (pip install PyMuPDF)."
        ) from exc

    try:
        document = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001 - third-party parser errors
        raise DocumentLoadError("PDF не удалось открыть: файл повреждён или зашифрован.") from exc
    try:
        if document.needs_pass:
            raise DocumentLoadError("PDF защищён паролем и не может быть обработан.")
        pages = document.page_count
        if pages < 1:
            raise DocumentLoadError("PDF не содержит страниц.")
        if pages > limits.max_pdf_pages:
            raise DocumentLoadError(
                f"Поддерживается одностраничный PDF, в файле {pages} страниц. "
                "Загрузите страницу с документом отдельно."
            )
        page = document.load_page(0)
        pixmap = page.get_pixmap(dpi=PDF_RENDER_DPI, alpha=False, colorspace=fitz.csRGB)
        array = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width, 3)
        return np.ascontiguousarray(array), pages, PDF_RENDER_DPI
    finally:
        document.close()


def load_document_bytes(data: bytes, filename: str, limits: Limits | None = None) -> DocumentImage:
    """Load an uploaded document from raw bytes."""
    limits = limits or settings.limits
    _check_size(len(data), limits)
    suffix = _validate_suffix(filename, limits)

    if suffix == ".pdf":
        rgb, pages, dpi = _render_pdf(data, limits, filename)
    else:
        try:
            with Image.open(io.BytesIO(data)) as image:
                image.load()
                rgb = _from_pil(image)
        except DocumentLoadError:
            raise
        except Exception as exc:  # noqa: BLE001 - Pillow raises many types
            raise DocumentLoadError("Изображение не удалось прочитать: файл повреждён.") from exc
        pages, dpi = 1, None

    if rgb.size == 0:
        raise DocumentLoadError("Пустое изображение.")
    if rgb.shape[0] * rgb.shape[1] > limits.max_image_pixels:
        raise DocumentLoadError("Слишком большое изображение для локальной обработки.")
    grey = np.asarray(Image.fromarray(rgb).convert("L"), dtype=np.uint8)
    return DocumentImage(
        rgb=rgb,
        grey=grey,
        source_suffix=suffix,
        source_name=Path(filename).name,
        page_count=pages,
        render_dpi=dpi,
    )


def load_document(path: str | Path, limits: Limits | None = None) -> DocumentImage:
    """Load a document from the filesystem (used by the CLI/batch flow)."""
    file_path = Path(path)
    if not file_path.is_file():
        raise DocumentLoadError(f"Файл не найден: {file_path.name}")
    return load_document_bytes(file_path.read_bytes(), file_path.name, limits=limits)
