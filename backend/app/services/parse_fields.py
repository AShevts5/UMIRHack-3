"""Вытаскиваем значения полей: ищем подпись поля, собираем значение, нормализуем.

Парсер на координаты страницы не завязан: находим напечатанную подпись поля
(её распознал OCR) и берём текст справа от неё, плюс хвост на следующей
строке, если значение перенеслось. Абсолютные координаты не используем и по
document_id ничего не подсовываем — один и тот же код работает и в вебе, и в
пакетном режиме, и на переименованных файлах.

Если значение пустое или не читается, по rules.md 2.2 пишем ровно ``не найдено``, и точка.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from statistics import median
from typing import TYPE_CHECKING

from ..utils.dates import parse_course, parse_date_with_year
from ..utils.textnorm import (
    is_blank_value,
    norm_label,
    norm_text,
    strip_fill_artifacts,
    strip_ocr_noise,
)
from .document_config import DocumentTypeSpec, FieldSpec, MarkSpec
from .ocr import OcrResult, TextLine, Word

if TYPE_CHECKING:  # импорт чисто для типов: detect_marks сам импортирует этот модуль, иначе будет цикл
    from .detect_marks import MarkVerdict

NOT_FOUND = "не найдено"

#: Всякие символы, которые Tesseract иногда выдаёт вместо двоеточия.
_COLON_CHARS = ":：﹕"
_COLON_RE = re.compile(r"[" + re.escape(_COLON_CHARS) + r"]")

#: Закрывающую » OCR любит рисовать обычной или двойной кавычкой.
_QUOTE_FIX_RE = re.compile(r"«([^»\"]{1,80})[\"“”]+")
_OPEN_QUOTE_RE = re.compile(r"[\"“”]([^«»\"“”]{1,80})[«»]")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,.;:])")
_DOUBLE_SPACE_RE = re.compile(r"\s{2,}")


def normalize_value_text(value: str) -> str:
    """Отмываем сырое значение после OCR, но смысл не трогаем."""
    text = strip_ocr_noise(strip_fill_artifacts(value))
    text = text.replace("\u00a0", " ")
    # Фигурных скобок в наших документах не бывает — это OCR путает их с круглыми.
    text = text.replace("{", "(").replace("}", ")")
    text = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", text)
    text = _DOUBLE_SPACE_RE.sub(" ", text)
    text = _QUOTE_FIX_RE.sub(lambda m: "«" + m.group(1).rstrip() + "»", text)
    text = _OPEN_QUOTE_RE.sub(lambda m: "«" + m.group(1).rstrip() + "»", text)
    text = text.strip(" \t")
    # Точки и запятые перед значением («. Учебный…», «; Кузнецова…») — просто мусор со страницы.
    text = re.sub(r"^[.,;:'`‘’|]+\s*", "", text)
    # Одиночная точка в конце у нас никогда не часть значения, срезаем.
    while len(text) > 1 and text.endswith(".") and not text.endswith(".."):
        if re.search(r"\d\.$", text):  # а окончания вида «38.03.01» не портим
            break
        text = text[:-1].rstrip()
    return text


@dataclass
class FieldResult:
    key: str
    value: str | None
    confidence: float
    box: tuple[int, int, int, int] | None = None
    raw: str = ""
    lines: list[str] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return self.value is not None and self.value != NOT_FOUND


@dataclass
class ParseResult:
    document_type: str
    fields: dict[str, str | bool]
    field_results: dict[str, FieldResult]
    mark_results: dict[str, "MarkVerdict"]
    leftover_lines: list[TextLine] = field(default_factory=list)
    detected_type: str | None = None
    detected_type_score: float = 0.0
    issues: list[str] = field(default_factory=list)

    @property
    def missing_fields(self) -> list[str]:
        missing = [key for key, result in self.field_results.items() if not result.found]
        missing += [key for key, mark in self.mark_results.items() if not mark.present]
        return missing


# ---------------------------------------------------------------------------
# Ищем подписи полей на странице
# ---------------------------------------------------------------------------


def _label_regex(spec: FieldSpec) -> re.Pattern[str]:
    alternatives = "|".join(f"(?:{pattern})" for pattern in spec.patterns)
    # Подпись обязана стоять в начале строки, дальше либо двоеточие, либо сразу значение.
    return re.compile(rf"^\s*(?P<label>{alternatives})\s*(?P<sep>[{re.escape(_COLON_CHARS)}])?", re.IGNORECASE)


def match_label(line_text: str, spec: FieldSpec) -> re.Match[str] | None:
    """Отдаём совпадение, если строка начинается с напечатанной подписи поля."""
    match = _label_regex(spec).match(line_text)
    if not match:
        # Вариант без двоеточия («ФИО студента Иванов И.И.») тоже берём, но
        # только если после подписи есть пробел и какое-то значение.
        plain = re.compile(
            r"^\s*(?P<label>" + "|".join(f"(?:{p})" for p in spec.patterns) + r")\s+(?P<rest>.*)$",
            re.IGNORECASE,
        ).match(line_text)
        if plain and not is_blank_value(plain.group("rest")):
            return plain
        return None
    if match.group("sep") is None:
        rest = line_text[match.end():]
        if is_blank_value(rest):
            return None
    return match


def _value_in_line(line_text: str, match: re.Match[str]) -> str:
    """То, что идёт после подписи и двоеточия, — уже без разделителя и лишних пробелов."""
    rest = line_text[match.end():]
    if match.groupdict().get("sep") is None and "rest" in match.groupdict():
        rest = match.group("rest")
    return rest.strip()


def detect_field_blocks(
    lines: list[TextLine], spec: DocumentTypeSpec
) -> dict[str, tuple[int, str]]:
    """По каждому полю: номер строки с подписью и значение из этой же строки."""
    blocks: dict[str, tuple[int, str]] = {}
    compiled = [(field_spec, _label_regex(field_spec)) for field_spec in spec.fields]
    for index, line in enumerate(lines):
        text = line.text
        if not text.strip():
            continue
        best: tuple[int, FieldSpec, re.Match[str]] | None = None
        for field_spec, _ in compiled:
            match = match_label(text, field_spec)
            if match is None:
                continue
            span = match.end() - match.start()
            if best is None or span > best[0]:
                best = (span, field_spec, match)
        if best is not None:
            _, field_spec, match = best
            if field_spec.key not in blocks:
                blocks[field_spec.key] = (index, _value_in_line(text, match))
    return blocks


def _line_gap(upper: TextLine, lower: TextLine) -> float:
    return float(lower.top - upper.bottom)


def _collect_continuation(
    lines: list[TextLine],
    start_index: int,
    inline_value: str,
    spec: DocumentTypeSpec,
    max_gap_factor: float = 1.9,
) -> tuple[str, int]:
    """Подклеиваем перенесённые куски значения, которое началось в ``start_index``."""
    parts = [inline_value] if inline_value else []
    heights = [line.height for line in lines] or [20]
    typical_height = median(heights)
    previous = lines[start_index]
    last_index = start_index
    for index in range(start_index + 1, len(lines)):
        line = lines[index]
        text = line.text.strip()
        if not text:
            break
        # Наткнулись на следующую подпись — значит, началось новое поле, стоп.
        if any(match_label(text, other) is not None for other in spec.fields):
            break
        gap = _line_gap(previous, line)
        if gap > typical_height * max_gap_factor:
            break
        # Продолжение должно реально продолжать фразу: начинаться со строчной буквы
        # или цифры и идти сразу за предыдущим куском. Иначе это уже что-то другое.
        first = text[:1]
        if not (first.islower() or first.isdigit()):
            break
        if not parts:
            break
        parts.append(text)
        previous = line
        last_index = index
    return " ".join(parts).strip(), last_index


def _region_box(lines: list[TextLine], start: int, end: int) -> tuple[int, int, int, int]:
    subset = lines[start : end + 1]
    left = min(line.left for line in subset)
    top = min(line.top for line in subset)
    right = max(line.right for line in subset)
    bottom = max(line.bottom for line in subset)
    return (left, top, right - left, bottom - top)


# ---------------------------------------------------------------------------
# Основной парсер — вся магия тут
# ---------------------------------------------------------------------------


def parse_document(ocr: OcrResult, spec: DocumentTypeSpec) -> ParseResult:
    """Вытаскиваем из результата OCR все поля, которые объявлены в ``spec``."""
    lines = [line for line in ocr.lines if line.text.strip()]
    blocks = detect_field_blocks(lines, spec)
    results: dict[str, FieldResult] = {}
    consumed: set[int] = set()
    issues: list[str] = []

    # Порядок полей в YAML = порядок в документе, по нему и нарезаем области.
    order = {field_spec.key: position for position, field_spec in enumerate(spec.fields)}
    ordered_keys = sorted(blocks, key=lambda key: blocks[key][0])

    for position, key in enumerate(ordered_keys):
        field_spec = spec.field(key)
        if field_spec is None:  # pragma: no cover - подстраховка
            continue
        start_index, inline_value = blocks[key]
        # Область заканчивается там, где ниже по документу начинается следующая подпись.
        limit = len(lines)
        for other_key in ordered_keys[position + 1 :]:
            other_index = blocks[other_key][0]
            if other_index > start_index:
                limit = other_index
                break
        region_lines = lines[start_index:limit]
        raw, last_index = _collect_continuation(region_lines, 0, inline_value, spec)
        for offset in range(start_index, start_index + last_index + 1):
            consumed.add(offset)
        value, confidence = _normalize_field(field_spec, raw)
        box = _region_box(lines, start_index, start_index + last_index)
        results[key] = FieldResult(
            key=key,
            value=value,
            confidence=confidence,
            box=box,
            raw=raw,
            lines=[line.text for line in lines[start_index : start_index + last_index + 1]],
        )
        if value is None:
            issues.append(f"поле '{key}' не заполнено в документе")

    # Все объявленные ключи обязаны быть на месте: схема хочет полный набор, без дырок.
    for field_spec in spec.fields:
        if field_spec.key not in results:
            results[field_spec.key] = FieldResult(
                key=field_spec.key, value=None, confidence=0.0, raw=""
            )
            issues.append(f"поле '{field_spec.key}' не найдено на странице")

    leftover = [line for index, line in enumerate(lines) if index not in consumed]
    return ParseResult(
        document_type=spec.code,
        fields={},
        field_results=results,
        mark_results={},
        leftover_lines=leftover,
        issues=issues,
    )


_TEXT_TYPES = {"person", "organization", "text", "document_number", "code", "grade"}


def _normalize_field(field_spec: FieldSpec, raw: str) -> tuple[str | None, float]:
    """Отдаём (нормализованное значение или None, уверенность 0..1)."""
    if is_blank_value(raw):
        return None, 0.0
    text = normalize_value_text(raw)
    if field_spec.is_date:
        iso = parse_date_with_year(text, None)
        if iso is None:
            # Дата не читается — считаем, что поле не заполнено.
            return None, 0.0
        return iso, 0.95
    if field_spec.type == "course":
        course = parse_course(text)
        return (course, 0.9) if course else (None, 0.0)
    if field_spec.type in _TEXT_TYPES:
        collapsed = re.sub(r"\s+", " ", text).strip()
        if field_spec.key == "program":
            # код направления всегда ДД.ДД.ДД, а на мелком снимке точка читается как пробел или запятая
            collapsed = re.sub(r"^(\d{2})\s?[.,\s]\s?(\d{2})\s?[.,\s]\s?(\d{2})\b", r"\1.\2.\3", collapsed)
        if len(collapsed) < 2:
            return None, 0.0
        return collapsed, 0.9
    return (text or None), 0.9  # pragma: no cover - запасной вариант для неизвестного типа


def fields_payload(parse_result: ParseResult, spec: DocumentTypeSpec) -> dict[str, str | bool]:
    """Собираем объект ``fields`` для одного документа — строго по контракту."""
    payload: dict[str, str | bool] = {}
    for field_spec in spec.fields:
        result = parse_result.field_results.get(field_spec.key)
        payload[field_spec.key] = result.value if result and result.value else NOT_FOUND
    for mark_spec in spec.marks:
        mark = parse_result.mark_results.get(mark_spec.key)
        payload[mark_spec.key] = bool(mark.present) if mark else False
    return payload


def labels_for_missing(spec: DocumentTypeSpec, keys: list[str]) -> list[str]:
    """Нормальные человеческие названия пропущенных полей — их ждут правила и вопросы."""
    names: list[str] = []
    for key in keys:
        field_spec = spec.field(key)
        if field_spec is not None:
            names.append(field_spec.label)
            continue
        mark_spec = spec.mark(key)
        names.append(mark_spec.failure_hint if mark_spec else key)
    return names


def anchor_words(ocr: OcrResult, mark_spec: MarkSpec) -> list[Word]:
    """Слова на странице, которые совпали с напечатанными якорями отметки."""
    patterns = [re.compile(pattern, re.IGNORECASE) for pattern in mark_spec.anchors]
    hits: list[Word] = []
    for word in ocr.words:
        for pattern in patterns:
            if pattern.fullmatch(word.text) or (pattern.search(word.text) and len(word.text) > 2):
                hits.append(word)
                break
    return hits


def normalized_label_set(spec: DocumentTypeSpec) -> set[str]:
    return {norm_label(field_spec.label) for field_spec in spec.fields}


def label_index(text: str, spec: DocumentTypeSpec) -> int | None:
    """Где в строке сидит известная подпись поля, если она там вообще есть (для отладки)."""
    normalized = norm_text(text)
    for field_spec in spec.fields:
        if norm_label(field_spec.label) in normalized:
            return normalized.index(norm_label(field_spec.label))
    return None
