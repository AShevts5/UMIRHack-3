"""Нормализация текста для парсера, правил и вопросов.

Проверочный скрипт кейса (`evaluate.py`) сравнивает строки так: NFC, нижний
регистр, ``ё`` -> ``е``, кавычки-ёлочки к обычным, повторные пробелы в один. Здесь
делаем ровно то же, чтобы сравнения внутри пайплайна совпадали с проверкой.
"""

from __future__ import annotations

import re
import unicodedata

QUOTE_CHARS = "«»“”„‟‹›"
DASH_CHARS = "‐‑‒–—―−"

_WS_RE = re.compile(r"\s+")
_EDGE_CHARS = " \t:：.,;·•\"'" + re.escape(DASH_CHARS) + "_"
_LABEL_TAIL_RE = re.compile(r"[" + _EDGE_CHARS + r"]+$")
_LABEL_HEAD_RE = re.compile(r"^[" + _EDGE_CHARS + r"]+")
_UNDERSCORE_RUN_RE = re.compile(r"[_—–\-]{3,}")
#: Точечные отбивки, с пробелами между точками или без: ". . . ." / ".....".
_DOTTED_LEADER_RE = re.compile(r"(?:[.·•]\s*){2,}")
_DIGIT_ONLY_RE = re.compile(r"^\d+$")


def norm_text(value: str | None) -> str:
    """Складываем строку так же, как проверка: регистр, ё->е, кавычки, пробелы."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFC", value)
    text = text.casefold().replace("ё", "е")
    for char in QUOTE_CHARS:
        text = text.replace(char, '"')
    for char in DASH_CHARS:
        text = text.replace(char, "-")
    text = _WS_RE.sub(" ", text)
    return text.strip()


def norm_label(value: str | None) -> str:
    """Нормализуем подпись поля: убираем кавычки и хвосты из ``:``, дефисов и подчёркиваний."""
    text = norm_text(value)
    text = _LABEL_TAIL_RE.sub("", text)
    text = _LABEL_HEAD_RE.sub("", text)
    return text.strip()


def collapse_spaces(value: str | None) -> str:
    """Сжимаем пробелы, но регистр букв не трогаем."""
    if value is None:
        return ""
    return _WS_RE.sub(" ", value.replace("\u00a0", " ")).strip()


def strip_fill_artifacts(value: str) -> str:
    """Убираем из значения линии заполнения из подчёркиваний и дефисов и хвосты двоеточий."""
    text = value.replace("_", " ")
    text = _UNDERSCORE_RUN_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text)
    return text.strip()


def strip_ocr_noise(value: str) -> str:
    """Убираем символы, которых в значении документа быть не может.

    Для точечных линий заполнения из синтетических образцов (``. . . . .``) и для
    линеек OCR иногда выдаёт псевдографику. Она не должна попадать в значение,
    иначе пустое на вид поле уедет в отчёт как заполненное.
    """
    text = value.replace("\u00a0", " ")
    text = re.sub(r"[|¦│┃]", " ", text)
    text = _DOTTED_LEADER_RE.sub(" ", text)
    text = re.sub(r"[\u2500-\u257f]", " ", text)  # псевдографика
    text = _WS_RE.sub(" ", text)
    return text.strip()


def is_blank_value(value: str | None) -> bool:
    """Проверяем, что в пойманном куске вообще нет информации.

    Пустотой считаем только пробелы, знаки препинания и символы заполнения из
    синтетических образцов (подчёркивания, штриховые линейки, точечные отбивки):
    один осмысленный символ — уже нормальное значение (``Курс: 3``).
    """
    if value is None:
        return True
    text = strip_ocr_noise(strip_fill_artifacts(value))
    text = text.strip(" \t:;,.·•-—–")
    return len(text) < 1


def is_empty_fill_line(value: str | None) -> bool:
    """Проверяем, что кусок — это напечатанная линия заполнения *и больше ничего*.

    В отличие от :func:`is_blank_value` вариант строгий: он нужен для
    необязательного текста, который законно может быть одним символом.
    """
    if value is None:
        return True
    stripped = value.strip()
    if not stripped:
        return True
    residue = strip_ocr_noise(strip_fill_artifacts(stripped)).strip(" \t:;,.·•-—–_\"'")
    return len(residue) < 1


def is_digits_only(value: str) -> bool:
    return bool(_DIGIT_ONLY_RE.match(value.strip()))


def first_meaningful(value: str) -> str:
    """Первый символ, который не пробел и не знак заполнения."""
    return value.strip(" \t:;,.·•-—–_")[:1]


def trim_template_tail(value: str) -> str:
    """Убираем точки в конце, которые OCR приклеил к значению."""
    return value.strip()
