"""Тесты на то, как ищем подпись и печать.

Сначала — главные отрицательные случаи из ТЗ, на них легко проколоться:

* **пустая линия подписи** (подпись-подсказка и линия есть, чернил нет) — это не подпись;
* одинокое печатное «М.П.» без круглого макета — это не печать;
* печатную подпись-подсказку за рукопись не засчитываем.

Положительные случаи тащим из открытого набора (настоящие пиксели и графика), а то,
чего там нет, дорисовываем сами синтетикой.
"""

from __future__ import annotations

import io
import json

import numpy as np
import pytest
from PIL import Image, ImageDraw

from backend.app.services.detect_marks import (
    BLUE_CHROMA_DIFF,
    blue_ink_mask,
    detect_marks,
    detect_marks_dict,
    find_horizontal_rules,
    find_seal_words,
    find_signature_words,
    seal_zone,
    signature_zone,
)
from backend.app.services.loader import load_document, load_document_bytes
from backend.app.services.ocr import run_ocr

from .conftest import ARCHIVE, requires_archive, requires_ocr


def make_document(rgb: np.ndarray):
    grey = np.asarray(Image.fromarray(rgb).convert("L"), dtype=np.uint8)
    from backend.app.services.loader import DocumentImage

    return DocumentImage(rgb=rgb, grey=grey, source_suffix=".png", source_name="synthetic.png")


def blank_page(width: int = 900, height: int = 1200) -> np.ndarray:
    return np.full((height, width, 3), 255, dtype=np.uint8)


def draw_signature(rgb: np.ndarray, x: int, y: int, scale: float = 1.0, colour=(28, 42, 120)) -> None:
    """Рисуем синюю рваную загогулину, похожую на рукописную подпись."""
    image = Image.fromarray(rgb)
    draw = ImageDraw.Draw(image)
    points = []
    for index in range(60):
        px = x + index * 2.6 * scale
        py = y + (18 if index % 4 == 0 else -14 if index % 3 == 0 else 4) * scale
        points.append((px, py))
    draw.line(points, fill=colour, width=max(2, int(3 * scale)), joint="curve")
    image_array = np.asarray(image, dtype=np.uint8)
    rgb[:, :, :] = image_array


def draw_seal(rgb: np.ndarray, cx: int, cy: int, radius: int = 70) -> None:
    image = Image.fromarray(rgb)
    draw = ImageDraw.Draw(image)
    for offset in (0, 10):
        draw.ellipse(
            [cx - radius + offset, cy - radius + offset, cx + radius - offset, cy + radius - offset],
            outline=(40, 70, 150),
            width=3,
        )
    draw.text((cx - 26, cy - 6), "ПЕЧАТЬ", fill=(40, 70, 150))
    image_array = np.asarray(image, dtype=np.uint8)
    rgb[:, :, :] = image_array


class TestMasks:
    def test_blue_mask_ignores_grey_print(self) -> None:
        rgb = blank_page(200, 100)
        rgb[40:60, 40:160] = 30  # тёмно-серое — это печатный текст
        assert blue_ink_mask(rgb).sum() == 0
        rgb[70:80, 40:160] = (30, 60, 170)  # а это синие чернила: 10 строк на 120 столбцов
        assert blue_ink_mask(rgb).sum() == 1200
        assert BLUE_CHROMA_DIFF > 0

    def test_blue_mask_detects_seal_blue(self) -> None:
        rgb = blank_page(200, 200)
        draw_seal(rgb, 100, 100, 60)
        assert blue_ink_mask(rgb).sum() > 100


class TestSignatureZones:
    def test_zone_starts_after_caption(self) -> None:
        rgb = blank_page()
        image = make_document(rgb)
        from backend.app.services.document_config import get_registry

        spec = get_registry().require("study_certificate")
        # подсовываем подставной результат OCR: нам нужна только подпись-подсказка
        from backend.app.services.ocr import OcrResult, TextLine, Word

        word = Word(text="Подпись", left=100, top=900, width=90, height=24, conf=90)
        word2 = Word(text="сотрудника:", left=196, top=900, width=120, height=24, conf=90)
        line = TextLine(words=[word, word2])
        ocr = OcrResult(text="Подпись сотрудника:", lines=[line], words=[word, word2], scale=1.0, mean_conf=90)
        zone = signature_zone(image, ocr, spec)
        assert zone is not None
        assert zone.x > word2.right  # буквы подписи-подсказки в зону не залезают
        assert zone.y <= line.bottom


class TestSyntheticNegativeCases:
    """Случаи, которых в открытом наборе нет: рисуем их сами, по пикселям."""

    def _analyse(self, rgb: np.ndarray, document_type: str = "study_certificate"):
        from backend.app.services.document_config import get_registry
        from backend.app.services.ocr import OcrResult, TextLine, Word

        image = make_document(rgb)
        spec = get_registry().require(document_type)
        words = [
            Word(text="Подпись", left=100, top=900, width=90, height=24, conf=90),
            Word(text="сотрудника:", left=196, top=900, width=120, height=24, conf=90),
            Word(text="м.п.", left=560, top=1000, width=40, height=22, conf=88),
        ]
        line = TextLine(words=words[:2])
        seal_line = TextLine(words=[words[2]])
        ocr = OcrResult(
            text="Подпись сотрудника: \nм.п.",
            lines=[line, seal_line],
            words=words,
            scale=1.0,
            mean_conf=90,
        )
        return image, ocr, spec

    def test_empty_signature_rule_is_not_a_signature(self) -> None:
        rgb = blank_page()
        ImageDraw.Draw(Image.fromarray(rgb))  # заглушка: ничего не рисуем, просто чтобы порядок вызовов был привычный
        image_draw = ImageDraw.Draw(Image.fromarray(rgb))
        del image_draw
        from PIL import ImageDraw as _Draw

        pil = Image.fromarray(rgb)
        _Draw.Draw(pil).line([(300, 924), (600, 924)], fill=(120, 120, 120), width=1)
        rgb[:, :, :] = np.asarray(pil, dtype=np.uint8)
        image, ocr, spec = self._analyse(rgb)
        verdicts = detect_marks_dict(image, ocr, spec)
        assert verdicts["signature_present"] is False
        assert verdicts["seal_present"] is False

    def test_caption_without_ink_is_not_a_signature(self) -> None:
        rgb = blank_page()
        image, ocr, spec = self._analyse(rgb)
        assert detect_marks_dict(image, ocr, spec)["signature_present"] is False

    def test_printed_seal_caption_without_graphic_is_not_a_seal(self) -> None:
        rgb = blank_page()
        # одни печатные слова, круглого макета нигде нет
        pil = Image.fromarray(rgb)
        ImageDraw.Draw(pil).text((560, 1000), "М.П.", fill=(40, 40, 40))
        ImageDraw.Draw(pil).text((520, 940), "УЧЕБНАЯ ПЕЧАТЬ МАКЕТ", fill=(40, 40, 40))
        rgb[:, :, :] = np.asarray(pil, dtype=np.uint8)
        image, ocr, spec = self._analyse(rgb)
        assert detect_marks_dict(image, ocr, spec)["seal_present"] is False

    def test_real_stroke_is_detected_as_signature(self) -> None:
        rgb = blank_page()
        draw_signature(rgb, x=310, y=920)
        image, ocr, spec = self._analyse(rgb)
        verdicts = detect_marks_dict(image, ocr, spec)
        assert verdicts["signature_present"] is True

    def test_seal_mockup_is_detected(self) -> None:
        rgb = blank_page()
        draw_seal(rgb, cx=580, cy=940, radius=70)
        image, ocr, spec = self._analyse(rgb)
        verdicts = detect_marks_dict(image, ocr, spec)
        assert verdicts["seal_present"] is True

    def test_monochrome_scan_still_finds_signature(self) -> None:
        """Подпись должны находить не только по цвету."""
        rgb = blank_page()
        draw_signature(rgb, x=310, y=920, colour=(20, 20, 20))
        image, ocr, spec = self._analyse(rgb)
        evidence = detect_marks(image, ocr, spec)["signature_present"]
        assert evidence.present is True
        assert evidence.method in {"colour", "greyscale"}


class TestMarkersAreNotLostByOcrHostSettings:
    """Страховка на случай, если настройки OCR втихую сломают поиск отметок.

    Детектор ищет зоны по печатным подписям («Подпись …», «М.П.»). Когда мы перебирали
    настройки, конфигурации с лучшей точностью текста (``psm 3``, ``psm 4``, ``psm 11``)
    переставали видеть эти подписи: на 14 документах подпись не находилась,
    точность по полям проседала с 1.0 до 0.953, а OCR-бенчмарк при этом спокойно показывал 1.0 —
    он смотрит значения, а не якоря. Тесты ниже валятся ровно в такой ситуации.
    """

    def _analyze(self, payload: bytes):
        from backend.app.services.pipeline import Pipeline

        return Pipeline(use_cache=False).analyze_bytes(payload, "guard.png", "study_certificate")

    def test_caption_words_are_recognised(self) -> None:
        image = make_document(np.asarray(Image.open(io.BytesIO(_render())).convert("RGB")))
        result = run_ocr(image)
        assert find_signature_words(result), "OCR перестал распознавать подпись «Подпись»"
        assert find_seal_words(result), "OCR перестал распознавать подпись места печати"

    def test_marks_still_detected_with_current_settings(self) -> None:
        result = self._analyze(_render())
        assert result.fields["signature_present"] is True
        assert result.fields["seal_present"] is True
        assert result.validation.status == "document_ok"

    def test_absence_still_detected_with_current_settings(self) -> None:
        result = self._analyze(_render(include_signature=False, include_seal=False))
        assert result.fields["signature_present"] is False
        assert result.fields["seal_present"] is False
        assert set(result.validation.missing_fields) == {"signature_present", "seal_present"}

    def test_settings_are_the_measured_ones(self) -> None:
        """Дефолты обязаны совпадать с настройками, на которых снимали benchmarks/README.md."""
        from backend.app.config import settings

        assert settings.ocr.psm == 6, "psm 3/4/11 теряют подписи-якоря (см. benchmarks/README.md)"
        assert settings.ocr.use_binarization is False, "Otsu снижает точность на 9.6 п.п."


def _render(include_signature: bool = True, include_seal: bool = True) -> bytes:
    """Синтетическая страница справки, тест рисует её сам."""
    from .test_pipeline import SYNTHETIC_VALUES, render_study_certificate

    return render_study_certificate(
        SYNTHETIC_VALUES, include_signature=include_signature, include_seal=include_seal
    )


@requires_archive
@requires_ocr
class TestOpenSetReference:
    @pytest.fixture(scope="class")
    @classmethod
    def analyser(cls):
        from backend.app.services.pipeline import Pipeline

        return Pipeline(use_cache=True)

    def test_marker_flags_match_reference(self, analyser, documents, reference) -> None:
        ref_by_id = {item["document_id"]: item["fields"] for item in reference["documents"]}
        failures = []
        for entry in documents:
            expected = ref_by_id[entry["document_id"]]
            path = ARCHIVE / entry["image"]
            result = analyser.analyze_path(path, entry["document_type"], entry["document_id"])
            for key in ("signature_present", "seal_present"):
                if key in expected and result.fields[key] != expected[key]:
                    failures.append(
                        f"{entry['document_id']}.{key}: ожидалось {expected[key]}, получено {result.fields[key]} "
                        f"(метод {result.marks[key].method!r})"
                    )
        assert not failures, "\n".join(failures)

    def test_empty_line_document_has_no_signature(self, analyser) -> None:
        # open-003: подпись-подсказка напечатана, линия нарисована, а чернил за ней ноль
        result = analyser.analyze_path(ARCHIVE / "documents/open-003.png", "study_certificate", "open-003")
        assert result.fields["signature_present"] is False
        assert result.fields["seal_present"] is True

    def test_lone_seal_caption_document(self, analyser) -> None:
        # open-010: подписи нет совсем, только печатное «М.П.» без макета
        result = analyser.analyze_path(ARCHIVE / "documents/open-010.png", "study_certificate", "open-010")
        assert result.fields["signature_present"] is False
        assert result.fields["seal_present"] is False

    def test_seal_without_signature(self, analyser) -> None:
        # open-023: печать на месте, а линия подписи пустая
        result = analyser.analyze_path(
            ARCHIVE / "documents/open-023.png", "internship_certificate", "open-023"
        )
        assert result.fields["signature_present"] is False
        assert result.fields["seal_present"] is True

    def test_signature_without_seal(self, analyser) -> None:
        # open-004: подпись на месте, макет печати убрали
        result = analyser.analyze_path(ARCHIVE / "documents/open-004.png", "study_certificate", "open-004")
        assert result.fields["signature_present"] is True
        assert result.fields["seal_present"] is False
