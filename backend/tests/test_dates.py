"""Тесты нормализации дат и текста (rules.md 2.5, README «Нормализация текстов»)."""

from __future__ import annotations

import pytest

from backend.app.utils.dates import human_date, parse_course, parse_date, parse_date_with_year
from backend.app.utils.textnorm import (
    is_blank_value,
    is_empty_fill_line,
    norm_label,
    norm_text,
    strip_ocr_noise,
)


class TestParseDate:
    @pytest.mark.parametrize(
        ("fragment", "expected"),
        [
            ("01.09.2023", "2023-09-01"),
            ("1.9.2023", "2023-09-01"),
            ("31.08.2026", "2026-08-31"),
            ("01/09/2023", "2023-09-01"),
            ("2026-09-01", "2026-09-01"),
            ("1 сентября 2026 года", "2026-09-01"),
            ("1 сентября 2022 года", "2022-09-01"),
            ("31 августа 2027 года", "2027-08-31"),
            ("7 октября 2026 года", "2026-10-07"),
            ("20 сентября 2026 г.", "2026-09-20"),
            ("15 июля 2026", "2026-07-15"),
        ],
    )
    def test_formats(self, fragment: str, expected: str) -> None:
        assert parse_date(fragment) == expected

    def test_numeric_and_text_forms_agree(self) -> None:
        assert parse_date("01.09.2023") == parse_date("1 сентября 2023 года")

    def test_yo_and_case(self) -> None:
        assert parse_date("1 ДЕКАБРЯ 2026 года") == "2026-12-01"
        assert parse_date("1 сентября 2026 года") == parse_date("1 сентября 2026 года")

    @pytest.mark.parametrize(
        "fragment",
        [
            "",
            None,
            "не найдено",
            "дата не указана",
            "___.___.____",
            "31.02.2026",  # несуществующая дата
            "45.13.2026",
            "1 сентября",  # без года
        ],
    )
    def test_rejects_non_dates(self, fragment: str | None) -> None:
        assert parse_date(fragment) is None

    def test_year_default(self) -> None:
        assert parse_date_with_year("1 сентября", 2026) == "2026-09-01"


class TestParseCourse:
    @pytest.mark.parametrize(
        ("fragment", "expected"),
        [("3", "3"), ("Курс 3", "3"), ("2 курс", "2"), (" 4 ", "4"), ("1", "1")],
    )
    def test_course_is_string(self, fragment: str, expected: str) -> None:
        result = parse_course(fragment)
        assert result == expected
        assert isinstance(result, str)

    @pytest.mark.parametrize("fragment", ["", None, "не найдено", "седьмой", "0", "9"])
    def test_course_rejects_bad_values(self, fragment: str | None) -> None:
        assert parse_course(fragment) is None


class TestNormalization:
    def test_norm_text_folds_case_yo_quotes(self) -> None:
        assert norm_text("Учебный университет «Меридиан»") == 'учебный университет "меридиан"'
        assert norm_text("Артём") == norm_text("Артем")
        assert norm_text("  много   пробелов ") == "много пробелов"

    def test_norm_label_strips_punctuation(self) -> None:
        assert norm_label("Дата выдачи:") == "дата выдачи"
        assert norm_label("«Причина отпуска»") == "причина отпуска"

    def test_fill_lines_are_blank(self) -> None:
        for fragment in ("", "   ", "____", "______", ". . . . .", "—", "____ "):
            assert is_empty_fill_line(fragment), fragment
            assert is_blank_value(fragment), fragment

    def test_single_character_is_a_value(self) -> None:
        # «Курс: 3» — однозначное значение, не пустая строка.
        assert not is_blank_value("3")
        assert not is_empty_fill_line("3")

    def test_strip_ocr_noise(self) -> None:
        assert strip_ocr_noise("Иванов . . . . Петр") == "Иванов Петр"

    def test_human_date(self) -> None:
        assert human_date("2026-09-01") == "1 сентября 2026 года"
