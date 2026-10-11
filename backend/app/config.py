"""Настройки сервиса «Онлайн-деканат» (UMIR 3).

Всё считается локально: никакие внешние OCR- и LLM-сервисы в проекте не
вызываются. Значения читаем из переменных окружения, чтобы один и тот же код
работал и на ноутбуке (Windows/Linux/macOS), и в бесплатном Colab.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Где что лежит в репозитории
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
    """Ищем папку tessdata, в которой лежит русский traineddata."""
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
        except OSError:  # pragma: no cover - подстраховка
            continue
    return None


def find_tesseract_binary() -> Path | None:
    """Возвращаем локальный исполняемый файл Tesseract, если он вообще есть."""
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
    """Настройки Tesseract и предобработки картинки.

    Значения подобраны перебором вариантов на 30 страницах открытого набора, а не
    взяты «на глаз». Что выяснилось по ходу:

    * Бинаризация (Otsu) тут только мешает: она съедает тонкие штрихи мелкого
      серого текста. С ней точность по символам 0.7753, без неё 0.8713. Поэтому по
      умолчанию выключена, но включается через UMIR_OCR_BINARIZE=1 — для шумных
      реальных фото, которых в наборе нет и померить их не на чем.
    * У режимов psm 3, psm 4, psm 11 точность по символам выше (0.90–0.91), но они
      начинают терять подписи-якоря. У psm 3 и psm 4 пропадает отдельное «М.П.»
      (20 страниц из 30 → 4), из-за чего уезжает зона печати и потом не находится
      подпись: точность полей падает с 1.0 до 0.953. А psm 11 и psm 12 на
      синтетической странице теряют сразу весь блок подписи и печати. Поэтому
      остаётся psm 6: на нём все 30 страниц дают нужные якоря и поля 1.0.

    Вывод на будущее: точность по символам — не единственный показатель, детектор
    отметок зависит от того, распознались ли подписи «Подпись» и «М.П.». Если
    менять psm или бинаризацию, надо гонять и тесты отметок тоже.
    """

    languages: str = os.environ.get("UMIR_OCR_LANGS", "rus+eng")
    oem: int = 1  # только LSTM-движок
    psm: int = int(os.environ.get("UMIR_OCR_PSM", "6"))
    target_long_edge: int = 2200  # масштаб считается от размера страницы
    upscale_min: float = 1.0
    upscale_max: float = 3.0
    use_binarization: bool = os.environ.get("UMIR_OCR_BINARIZE", "0") not in {"0", "false", "no"}
    tesseract_timeout_s: float = 60.0
    # Подпись и печать ищем на исходной серой картинке, а не на обработанной:
    # бинаризация может съесть слабые штрихи.
    keep_original_greyscale: bool = True


@dataclass(frozen=True)
class Limits:
    """Ограничения на входной файл: размер, число страниц, форматы."""

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


def _default_rules_path() -> Path | None:
    """Где лежит rules.md — Положение из пакета кейса.

    Нужен только разделу «Положение» в интерфейсе. Ищем по обычным местам: переменная
    окружения, архив кейса рядом с репозиторием, копия в самом репозитории.
    """
    candidates = [
        Path(os.environ["UMIR_RULES"]) if os.environ.get("UMIR_RULES") else None,
        PROJECT_ROOT / "rules.md",
        PROJECT_ROOT.parent / "online-dean-participants-v1 (2)" / "rules.md",
        PROJECT_ROOT.parent / "online-dean-participants-v1" / "rules.md",
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            return candidate
    return None


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
    rules_path: Path | None = field(default_factory=_default_rules_path)
    # Текст документов в логи целиком не пишем, только счётчики.
    log_full_text: bool = False


settings = Settings()
