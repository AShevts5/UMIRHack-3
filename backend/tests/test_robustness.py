"""Robustness additions: photo enhancement, pixel re-check of values, batch fallback."""

from __future__ import annotations

import random

import cv2
import numpy as np
import pytest

from backend.app.schemas import DocumentResult
from backend.app.services.document_config import get_registry
from backend.app.services.enhance import enhance_document, estimate_skew, find_page_quad, rotate_page
from backend.app.services.loader import DocumentImage
from backend.app.services.question_engine import Intent, classify_question
from backend.app.services.refine_fields import CODE_RE, value_has_ink
from backend.scripts.make_degraded import rotate as rotate_photo
from backend.scripts.make_degraded import shadow as add_shadow
from backend.scripts.run_batch import fallback_payload

registry = get_registry()


def _page(seed: int = 3) -> np.ndarray:
    """A white A4-like page with dark text-like lines (no OCR needed)."""
    rng = random.Random(seed)
    page = np.full((1600, 1132, 3), 255, np.uint8)
    for row in range(14):
        y = 260 + row * 38
        x = 70
        while x < 900:
            w = rng.randint(30, 110)
            cv2.rectangle(page, (x, y), (x + w, y + 14), (30, 33, 42), -1)
            x += w + rng.randint(10, 18)
    return page


def _image(rgb: np.ndarray) -> DocumentImage:
    return DocumentImage(rgb=rgb, grey=cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), source_suffix=".png", source_name="t.png")


class TestEnhance:
    def test_clean_page_is_untouched(self) -> None:
        image = _image(_page())
        out, report = enhance_document(image)
        assert out is image
        assert report.steps == []

    @pytest.mark.parametrize("angle", [-25.0, -8.0, 4.0, 17.0])
    def test_skew_is_estimated(self, angle: float) -> None:
        tilted = rotate_page(_page(), angle)
        grey = cv2.cvtColor(tilted, cv2.COLOR_RGB2GRAY)
        assert estimate_skew(grey) == pytest.approx(-angle, abs=0.6)

    def test_photo_on_table_is_cropped(self) -> None:
        photo = rotate_photo(_page(), random.Random(5), max_angle=20)
        quad = find_page_quad(cv2.cvtColor(photo, cv2.COLOR_RGB2GRAY))
        assert quad is not None
        out, report = enhance_document(_image(photo))
        assert "page_crop" in report.steps
        assert out.quality in {"photo", "lowres"}

    def test_shadow_is_not_mistaken_for_page_edge(self) -> None:
        shaded = add_shadow(_page(), random.Random(11))
        out, report = enhance_document(_image(shaded))
        assert "page_crop" not in report.steps
        assert "flatten_lighting" in report.steps
        # Paper is bright again everywhere after relighting.
        assert np.percentile(out.grey, 20) > 200


class TestValueInk:
    def _line(self, draw) -> np.ndarray:
        grey = np.full((60, 600), 255, np.uint8)
        draw(grey)
        return grey

    def test_fill_line_is_blank(self) -> None:
        grey = self._line(lambda g: cv2.line(g, (10, 40), (580, 41), 20, 2))
        assert not value_has_ink(grey, (0, 10, 600, 50), 26)

    def test_glyphs_are_ink(self) -> None:
        grey = self._line(lambda g: cv2.putText(g, "AB-12", (20, 42), cv2.FONT_HERSHEY_SIMPLEX, 1.0, 0, 2))
        assert value_has_ink(grey, (0, 10, 600, 50), 26)

    @pytest.mark.parametrize("code", ["ОБ-1001", "ОБ/К-11580", "ЭЭ-20-1", "ИВТ-23-4", "ПР/У-55308"])
    def test_code_pattern_accepts(self, code: str) -> None:
        assert CODE_RE.match(code)

    @pytest.mark.parametrize("code", ["OB/K-11580", "33-20-1", "CO-74662", "ро"])
    def test_code_pattern_rejects_ocr_slips(self, code: str) -> None:
        assert not CODE_RE.match(code)


class TestQuestions:
    @pytest.mark.parametrize(
        "question",
        ["Хватит ли этой справки для перевода на 2 курс?", "Можно ли с этим заявлением перевестись на 3 курс?"],
    )
    def test_transfer_is_no_information_even_with_course_field(self, question: str) -> None:
        spec = registry.require("academic_leave_application")
        assert classify_question(question, spec).intent == Intent.UNKNOWN


class TestBatchFallback:
    @pytest.mark.parametrize("document_type", ["study_certificate", "academic_leave_application", "internship_certificate"])
    def test_fallback_record_is_schema_valid(self, document_type: str) -> None:
        questions = [
            ("q1", "Кто принимает окончательное решение о принятии документа?"),
            ("q2", "Какова стоимость обучения за следующий семестр?"),
        ]
        payload = fallback_payload("x-1", document_type, questions)
        record = DocumentResult(**payload)
        assert record.validation.status == "missing_fields"
        decision, refusal = payload["answers"]
        assert decision["value"] == "сотрудник деканата" and decision["sources"] == ["rule:1.3"]
        assert refusal["answerable"] is False and refusal["sources"] == []
