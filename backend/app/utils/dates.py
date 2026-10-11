"""Приводим даты к одному виду: ``ДД.ММ.ГГГГ`` и «1 сентября 2026 года» -> ``ГГГГ-ММ-ДД``.

В наборе кейса обе записи должны давать одно и то же значение. Поэтому ничего не
угадываем: не смогли разобрать — возвращаем ``None``, и поле уходит в
``не найдено``.
"""

from __future__ import annotations

import calendar
import re
from datetime import date

MONTHS_GENITIVE = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}

MONTHS_NOMINATIVE = {
    "январь": 1,
    "февраль": 2,
    "март": 3,
    "апрель": 4,
    "май": 5,
    "июнь": 6,
    "июль": 7,
    "август": 8,
    "сентябрь": 9,
    "октябрь": 10,
    "ноябрь": 11,
    "декабрь": 12,
}

#: Название месяца -> номер, в обоих падежах и с поправкой на частое «ё».
MONTHS = {**MONTHS_GENITIVE, **MONTHS_NOMINATIVE}

_MONTH_ALT = "|".join(sorted(MONTHS, key=len, reverse=True))

_NUMERIC_RE = re.compile(
    r"(?<!\d)(?P<d>0?[1-9]|[12]\d|3[01])\s*[.,/\-]\s*(?P<m>0?[1-9]|1[0-2])\s*[.,/\-]\s*(?P<y>(?:19|20)\d{2})(?!\d)"
)
_ISO_RE = re.compile(r"(?<!\d)(?P<y>(?:19|20)\d{2})-(?P<m>0?[1-9]|1[0-2])-(?P<d>0?[1-9]|[12]\d|3[01])(?!\d)")
_TEXT_RE = re.compile(
    r"(?<!\d)(?P<d>0?[1-9]|[12]\d|3[01])\s*(?:-?го)?\s+"
    r"(?P<m>" + _MONTH_ALT + r")\s*[,.]?\s*"
    r"(?P<y>(?:19|20)\d{2})?\s*(?:года|г\.?|гг\.?)?",
    re.IGNORECASE,
)


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        if year < 1900 or year > 2100:
            return None
        return date(year, month, day)
    except ValueError:
        return None


def parse_date(fragment: str | None) -> str | None:
    """Отдаём ``ГГГГ-ММ-ДД`` для первого куска, похожего на дату, а если такого нет — ``None``."""
    if not fragment:
        return None
    text = fragment.strip()
    for pattern in (_ISO_RE, _NUMERIC_RE):
        match = pattern.search(text)
        if match:
            found = _safe_date(int(match.group("y")), int(match.group("m")), int(match.group("d")))
            if found:
                return found.isoformat()
    match = _TEXT_RE.search(text)
    if match:
        month = MONTHS.get(match.group("m").lower())
        year_raw = match.group("y")
        if month and year_raw:
            found = _safe_date(int(year_raw), month, int(match.group("d")))
            if found:
                return found.isoformat()
    return None


def parse_date_with_year(fragment: str | None, default_year: int | None) -> str | None:
    """То же, что :func:`parse_date`, только подставляет потерявшийся год (``1 сентября``)."""
    found = parse_date(fragment)
    if found or not fragment or default_year is None:
        return found
    match = _TEXT_RE.search(fragment)
    if not match:
        return None
    month = MONTHS.get(match.group("m").lower())
    if not month:
        return None
    candidate = _safe_date(default_year, month, int(match.group("d")))
    return candidate.isoformat() if candidate else None


def parse_course(fragment: str | None) -> str | None:
    """Вытаскиваем номер курса *строкой* из цифр — контракт хочет именно str."""
    if not fragment:
        return None
    match = re.search(r"(?<!\d)([1-6])(?!\d)", fragment)
    if match:
        return match.group(1)
    match = re.search(r"\b([1-6])\b", fragment)
    return match.group(1) if match else None


def looks_like_date(fragment: str | None) -> bool:
    return parse_date(fragment) is not None


def month_name(month: int) -> str:
    """Русское название месяца в родительном падеже — нужно только для текста ответа."""
    if not 1 <= month <= 12:
        raise ValueError(month)
    for name, number in MONTHS_GENITIVE.items():
        if number == month:
            return name
    raise ValueError(month)  # pragma: no cover


def human_date(iso: str | None) -> str:
    """Делаем из ``2026-09-01`` «1 сентября 2026 года» — для текста ответа."""
    if not iso:
        return ""
    try:
        parsed = date.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{parsed.day} {month_name(parsed.month)} {parsed.year} года"


def days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]
