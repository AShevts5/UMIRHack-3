"""Настройки сервиса «Онлайн-деканат» (UMIR 3).

Всё крутится локально: никакие внешние OCR- и LLM-сервисы мы не
дёргаем. Значения читаем из переменных окружения, чтобы один и тот же код
заводился и на ноуте (Windows/Linux/macOS), и в бесплатном Colab.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Что где лежит в репе
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
    """Ищем папку tessdata, где лежит русский traineddata."""
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
        # сюда складывает файлы python -m backend.scripts.download_models
        PROJECT_ROOT / "models" / "tessdata",
    ]
    for candidate in candidates:
        try:
            if candidate.is_dir() and (candidate / "rus.traineddata").is_file():
                return candidate
        except OSError:  # pragma: no cover - подстраховка
            continue
    return None


# Наша модель распознавания: взяли базовую русскую и дообучили на своих снимках
# печатного датасета и на «убитых» бланках кейса (подробности в docs/ocr-finetune.md).
OWN_MODEL = BACKEND_ROOT / "models" / "rus_umir.traineddata"


def with_own_model(base: Path | None) -> Path | None:
    """Движок видит модели только из одной папки, поэтому собираем свою: системные rus/eng
    с конфигами + rus_umir из репы. Не получилось скопировать — работаем из системной
    папки, а rus_umir тогда заменится на обычный rus (см. ocr.resolve_languages)."""
    if base is None or not OWN_MODEL.is_file() or (base / OWN_MODEL.name).is_file():
        return base
    import tempfile

    target = Path(os.environ.get("UMIR_TESSDATA_DIR") or Path(tempfile.gettempdir()) / "umir-tessdata")
    try:
        target.mkdir(parents=True, exist_ok=True)
        for src in [*base.glob("*.traineddata"), OWN_MODEL]:
            dst = target / src.name
            if not dst.is_file() or dst.stat().st_size != src.stat().st_size or dst.stat().st_mtime < src.stat().st_mtime:
                shutil.copyfile(src, dst)
        for folder in ("configs", "tessconfigs"):
            if (base / folder).is_dir() and not (target / folder).is_dir():
                shutil.copytree(base / folder, target / folder)
        return target
    except OSError:
        return base


def find_tesseract_binary() -> Path | None:
    """Ищем локальный бинарник OCR, если он вообще где-то есть."""
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
    """Настройки OCR и предобработки картинки.

    Значения не «на глаз»: мы честно перебрали варианты на 30 страницах открытого
    набора. Что выяснили по ходу:

    * Бинаризация (Otsu) тут только вредит: съедает тонкие штрихи мелкого
      серого текста. С ней точность по символам 0.7753, без неё 0.8713. Так что по
      умолчанию выключена, но включается через UMIR_OCR_BINARIZE=1 — на случай шумных
      реальных фото, которых в наборе нет и мерить их не на чем.
    * У режимов psm 3, psm 4, psm 11 точность по символам выше (0.90–0.91), зато они
      начинают терять подписи-якоря. У psm 3 и psm 4 пропадает отдельное «М.П.»
      (20 страниц из 30 → 4), из-за этого уезжает зона печати, а потом не находится
      подпись: точность полей падает с 1.0 до 0.953. А psm 11 и psm 12 на
      сгенерированной странице теряют сразу весь блок подписи и печати. Короче,
      сидим на psm 6: с ним все 30 страниц дают нужные якоря и поля 1.0.

    На будущее: точность по символам — не единственная метрика, детектор
    отметок зависит от того, распознались ли подписи «Подпись» и «М.П.». Если
    крутите psm или бинаризацию — гоняйте и тесты отметок тоже.
    """

    languages: str = os.environ.get("UMIR_OCR_LANGS", "rus_umir+eng")
    oem: int = 1  # только LSTM-движок, другие не берём
    psm: int = int(os.environ.get("UMIR_OCR_PSM", "6"))
    target_long_edge: int = 2200  # масштаб считаем от размера страницы
    upscale_min: float = 1.0
    upscale_max: float = 3.0
    use_binarization: bool = os.environ.get("UMIR_OCR_BINARIZE", "0") not in {"0", "false", "no"}
    tesseract_timeout_s: float = 60.0
    # подпись и печать ищем на исходной серой картинке, не на обработанной:
    # бинаризация запросто съедает слабые штрихи.
    keep_original_greyscale: bool = True


@dataclass(frozen=True)
class Limits:
    """Что пускаем на вход: размер файла, число страниц, форматы."""

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

    Нужен только разделу «Положение» в интерфейсе. Ищем в привычных местах: переменная
    окружения, архив кейса рядом с репой, копия в самой репе.
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
    tessdata_prefix: Path | None = field(default_factory=lambda: with_own_model(_default_tessdata()))
    document_types_config: Path = field(
        default_factory=lambda: Path(
            os.environ.get(
                "UMIR_DOCUMENT_TYPES",
                str(PACKAGE_ROOT / "configs" / "document_types.yaml"),
            )
        )
    )
    rules_path: Path | None = field(default_factory=_default_rules_path)
    # текст документов целиком в логи не льём, только счётчики
    log_full_text: bool = False


settings = Settings()
