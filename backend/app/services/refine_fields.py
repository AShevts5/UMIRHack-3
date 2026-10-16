"""Второй заход по значениям полей — теперь уже по пикселям страницы.

OCR по всей странице иногда читает короткие коды не той
азбукой (``ОБ/К-1158`` -> ``OB/K-1158``, ``ЭЭ-20-1`` -> ``33-20-1``), теряет
значение с низкой уверенностью или принимает напечатанную линию заполнения
``______`` за пару букв. Любой такой косяк переворачивает всё поле, а за ним
и результат проверки.

Поэтому каждое поле перепроверяем по пикселям справа от подписи:

* чернил нет, только линия заполнения -> поле пустое (rules.md 2.2);
* чернила есть, а парсер ничего не нашёл -> читаем кроп со значением отдельно;
* поле-код (номер справки, группа) всегда перечитываем из кропа только русской
  моделью; выигрывает большинство нормальных кириллических прочтений.

Перечитываем только кропы значений (коды и подозрительные поля), так что чистая
страница обходится в одну проверку пикселей на поле плюс пару OCR по крошечным кропам.
"""

from __future__ import annotations

import re
from collections import Counter

import cv2
import numpy as np
import pytesseract

from ..config import settings
from .document_config import DocumentTypeSpec, FieldSpec
from .loader import DocumentImage
from .ocr import OcrResult, TextLine, resolve_languages
from .parse_fields import ParseResult, _normalize_field, detect_field_blocks, match_label

#: Типы полей, где значение — короткий код из букв и цифр.
CODE_TYPES = {"document_number", "code"}
#: Вот такие: «ОБ-1001», «ОБ/К-11580», «ИВТ-23-4», «ЭЭ-20-1».
CODE_RE = re.compile(r"^[А-ЯЁ]{1,6}(?:/[А-ЯЁ]{1,4})?(?:-\d{1,6})+$")
_LATIN_LOOKALIKES = str.maketrans("ABCEHKMOPTXY", "АВСЕНКМОРТХУ")


def _value_start_x(line: TextLine, field_spec: FieldSpec) -> int | None:
    """Колонка в пикселях, где начинается значение — сразу за подписью и двоеточием."""
    words = line.words
    for index, word in enumerate(words[:6]):
        if ":" in word.text or "：" in word.text:
            # «Курс:3» слиплось с двоеточием — значит, значение начинается прямо внутри слова.
            tail = word.text.split(":", 1)[-1]
            if tail.strip():
                return word.left + int(word.width * (len(word.text) - len(tail)) / max(len(word.text), 1))
            # Рамки слов бывают внахлёст («Курс:» залезает на половину «3»),
            # поэтому берём левый край следующего слова, если он левее.
            following = words[index + 1].left - 1 if index + 1 < len(words) else word.right + 1
            return min(word.right + 1, following)
    match = match_label(line.text, field_spec)
    if match is None:
        return None
    label_words = len(match.group("label").split())
    if label_words >= len(words):
        return None
    return words[label_words - 1].right + 1


def _value_region(image: DocumentImage, line: TextLine, x0: int) -> tuple[int, int, int, int]:
    pad = max(2, int(line.height * 0.25))
    top = max(0, line.top - pad)
    bottom = min(image.height, line.bottom + pad)
    # Значение — это пара десятков символов, так что на поля страницы не лезем.
    right = min(int(image.rgb.shape[1] * 0.97), x0 + line.height * 40)
    return x0, top, right, bottom


def value_has_ink(grey: np.ndarray, region: tuple[int, int, int, int], line_height: int) -> bool:
    """Смотрим, есть ли в области буквы, а не одна только линия из подчёркиваний."""
    x0, y0, x1, y1 = region
    crop = grey[y0:y1, x0:x1]
    if crop.size == 0:
        return False
    mask = (crop < 150).astype(np.uint8)
    # Сначала выкидываем горизонтальные линейки из подчёркиваний, потом уже ищем буквы.
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(15, line_height * 2), 1))
    rules = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.subtract(mask, rules)
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    min_height = max(4, int(line_height * 0.35))
    for index in range(1, count):
        width, height, area = stats[index, cv2.CC_STAT_WIDTH], stats[index, cv2.CC_STAT_HEIGHT], stats[index, cv2.CC_STAT_AREA]
        if height >= min_height and area >= 6 and width < line_height * 6:
            return True
    return False


def _reread(grey: np.ndarray, region: tuple[int, int, int, int], line_height: int, lang: str, scale: float) -> str:
    x0, y0, x1, y1 = region
    crop = grey[y0:y1, x0:x1]
    factor = max(1.0, min(5.0, scale * 40.0 / max(line_height, 1)))
    crop = cv2.resize(crop, None, fx=factor, fy=factor, interpolation=cv2.INTER_CUBIC)
    crop = cv2.copyMakeBorder(crop, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
    try:
        text = pytesseract.image_to_string(
            crop, lang=resolve_languages(lang), config="--oem 1 --psm 7",
            timeout=settings.ocr.tesseract_timeout_s,
        )
    except RuntimeError:  # таймаут: ну и ладно, оставляем то, что прочиталось по всей странице
        return ""
    return text.strip()


def _clean_code(text: str) -> str:
    text = re.sub(r"\s+", "", text)
    text = text.strip("_‚,.'\"`|:;")
    return text.replace("—", "-").replace("–", "-")


def _best_code(grey: np.ndarray, region: tuple[int, int, int, int], line_height: int, original: str | None) -> str | None:
    readings = [
        _clean_code(_reread(grey, region, line_height, "rus", scale)).upper()
        for scale in (0.8, 1.0, 1.3)
    ]
    # Прочтение со всей страницы тоже голосует и при ничьей выигрывает: кроп
    # лучше разбирается с азбукой, а страница иногда — со слэшами.
    page = _clean_code(original or "")
    valid = [reading for reading in [page, *readings] if CODE_RE.match(reading)]
    if valid:
        counts = Counter(valid)
        top = max(counts.values())
        if CODE_RE.match(page) and counts[page] == top:
            return page
        return next(reading for reading in valid if counts[reading] == top)
    if original:
        translated = _clean_code(original).upper().translate(_LATIN_LOOKALIKES)
        if CODE_RE.match(translated):
            return translated
    return None


def refine_fields(image: DocumentImage, ocr: OcrResult, spec: DocumentTypeSpec, parse: ParseResult) -> None:
    """Правим ``parse.field_results`` прямо на месте, подсматривая в пиксели значений."""
    lines = [line for line in ocr.lines if line.text.strip()]
    blocks = detect_field_blocks(lines, spec)
    for key, (index, _inline) in blocks.items():
        field_spec = spec.field(key)
        result = parse.field_results.get(key)
        if field_spec is None or result is None:
            continue
        line = lines[index]
        x0 = _value_start_x(line, field_spec)
        if x0 is None:
            continue
        region = _value_region(image, line, x0)
        multi_line = len(result.lines) > 1
        has_ink = value_has_ink(image.grey, region, line.height)

        if not has_ink:
            if result.found and not multi_line:
                result.value = None
                result.confidence = 0.0
                parse.issues.append(f"поле '{key}': на месте значения только линия для заполнения")
            continue

        is_code = field_spec.type in CODE_TYPES
        if result.found and not is_code:
            continue

        if is_code:
            # Коды перечитываем всегда: модель по всей странице путает азбуки и
            # теряет слэши, даже когда результат с виду нормальный.
            fixed = _best_code(image.grey, region, line.height, result.value)
            if fixed:
                result.value, result.confidence = fixed, 0.85
            continue

        # Чернила есть, а OCR по всей странице их прошляпил: читаем кроп значения отдельно.
        for lang in ("rus+eng", "rus"):
            raw = _reread(image.grey, region, line.height, lang, 1.0)
            value, confidence = _normalize_field(field_spec, raw)
            if value:
                result.value, result.confidence, result.raw = value, min(confidence, 0.8), raw
                break
