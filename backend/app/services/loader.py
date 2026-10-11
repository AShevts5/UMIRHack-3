"""Загрузка входного файла: PNG/JPG/PDF -> массив RGB + серая картинка.

Что тут проверяем (см. README, раздел про ограничения):

* принимаем только ``.png``, ``.jpg``, ``.jpeg`` и ``.pdf``;
* ограничиваем размер файла, число пикселей в картинке и число страниц PDF;
* PDF рендерим локально через PyMuPDF — без сети и без внешних рендереров;
* на битом или зашифрованном файле отдаём понятную ошибку, а не трассировку;
* на диск ничего не пишем, всё держим в памяти.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageFile, ImageOps

from ..config import Limits, settings

ImageFile.LOAD_TRUNCATED_IMAGES = False
Image.MAX_IMAGE_PIXELS = None  # свой лимит по пикселям проверяем сами

PDF_RENDER_DPI = 200


class DocumentLoadError(Exception):
    """Файл не подходит по формату или не читается."""


@dataclass
class DocumentImage:
    """Одна страница документа, готовая к OCR и поиску отметок."""

    rgb: np.ndarray  # HxWx3, uint8
    grey: np.ndarray  # HxW, uint8
    source_suffix: str
    source_name: str
    page_count: int = 1
    render_dpi: int | None = None
    inverted_preview: bool = False
    #: "clean" — скан или рендер, "photo" — выровненное и пересвеченное фото,
    #: "lowres" — увеличенное фото; ставит enhance.py, читает предобработка OCR.
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
    except ImportError as exc:  # pragma: no cover - проверка зависимости
        raise DocumentLoadError(
            "Для обработки PDF требуется PyMuPDF (pip install PyMuPDF)."
        ) from exc

    try:
        document = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001 - ошибки стороннего парсера бывают любые
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
    """Загружаем присланный документ из сырых байтов."""
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
        except Exception as exc:  # noqa: BLE001 - Pillow кидает исключения разных типов
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
    """Загружаем документ с диска — этим пользуется CLI и пакетный режим."""
    file_path = Path(path)
    if not file_path.is_file():
        raise DocumentLoadError(f"Файл не найден: {file_path.name}")
    return load_document_bytes(file_path.read_bytes(), file_path.name, limits=limits)
