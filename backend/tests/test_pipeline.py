"""End-to-end pipeline tests.

Two layers:

* **synthetic end-to-end** — a document image is drawn from scratch with Pillow
  (never taken from the case archive), fed through the real loader → OCR → parser →
  markers → rules → questions path, and the resulting contract is asserted.  This
  proves the pipeline is not tied to the provided samples.
* **open-set** — the same path on the organizer archive, including the integrity
  test with renamed files and re-numbered ids.
"""

from __future__ import annotations

import json
import random
import shutil
import string
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from backend.app.schemas import FIELD_KEYS, DocumentResult, ResultsFile
from backend.app.services.document_config import get_registry
from backend.app.services.loader import DocumentLoadError, load_document, load_document_bytes
from backend.app.services.pipeline import Pipeline, build_api_response
from backend.app.services.question_engine import default_completeness_question
from backend.scripts.run_batch import group_questions, run_batch

from .conftest import ARCHIVE, requires_archive, requires_ocr

NOT_FOUND = "не найдено"


# ---------------------------------------------------------------------------
# Synthetic document renderer (used by the archive-independent tests)
# ---------------------------------------------------------------------------


def _font(size: int):
    from PIL import ImageFont

    for candidate in (
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/Library/Fonts/Arial.ttf",
    ):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def render_study_certificate(values: dict[str, str], include_signature: bool = True, include_seal: bool = True) -> bytes:
    """Draw a synthetic «Справка об обучении» page; returns PNG bytes.

    The layout mirrors the field captions used by the case so the same label
    anchors apply, but every value here is invented by the test.
    """
    width, height = 1700, 2400
    image = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    title_font = _font(64)
    body_font = _font(46)

    draw.text((140, 150), "СПРАВКА ОБ ОБУЧЕНИИ", fill=(20, 20, 20), font=title_font)
    labels = [
        ("student_name", "ФИО студента"),
        ("university", "Название вуза"),
        ("program", "Направление подготовки"),
        ("study_form", "Форма обучения"),
        ("study_start", "Начало периода обучения"),
        ("study_end", "Окончание периода обучения"),
        ("document_number", "Номер справки"),
        ("issue_date", "Дата выдачи"),
    ]
    y = 400
    for key, label in labels:
        value = values.get(key)
        text = f"{label}: {value}" if value else f"{label}:"
        draw.text((140, y), text, fill=(25, 25, 25), font=body_font)
        if not value:
            draw.line([(140 + 26 * len(label), y + 58), (140 + 26 * len(label) + 420, y + 58)], fill=(150, 150, 150), width=2)
        y += 110

    draw.text((140, 1900), "Подпись сотрудника:", fill=(25, 25, 25), font=body_font)
    draw.line([(600, 1990), (1180, 1990)], fill=(120, 120, 120), width=2)
    if include_signature:
        points = []
        for index in range(70):
            px = 620 + index * 7.6
            py = 1960 + (26 if index % 4 == 0 else -22 if index % 3 == 0 else 6)
            points.append((px, py))
        draw.line(points, fill=(30, 45, 130), width=4, joint="curve")

    if include_seal:
        cx, cy, radius = 1380, 1880, 110
        for offset in (0, 16):
            draw.ellipse(
                [cx - radius + offset, cy - radius + offset, cx + radius - offset, cy + radius - offset],
                outline=(40, 70, 150),
                width=4,
            )
        draw.text((cx - 60, cy - 12), "ПЕЧАТЬ", fill=(40, 70, 150), font=_font(28))
    draw.text((1380, 2020), "М.П.", fill=(30, 30, 30), font=body_font)

    import io

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


SYNTHETIC_VALUES = {
    "student_name": "Тестов Тест Тестович",
    "university": "Учебный университет «Меридиан»",
    "program": "01.03.02 Прикладная математика",
    "study_form": "очная",
    "study_start": "1 сентября 2021 года",
    "study_end": "31 августа 2025 года",
    "document_number": "ТЕ-9001",
    "issue_date": "15.06.2025",
}


class TestLoader:
    def test_rejects_unsupported_extension(self) -> None:
        with pytest.raises(DocumentLoadError):
            load_document_bytes(b"data", "document.txt")

    def test_rejects_empty_file(self) -> None:
        with pytest.raises(DocumentLoadError):
            load_document_bytes(b"", "document.png")

    def test_rejects_broken_image(self) -> None:
        with pytest.raises(DocumentLoadError):
            load_document_bytes(b"\x89PNG\r\n\x1a\nbroken", "document.png")

    def test_rejects_oversized_file(self) -> None:
        with pytest.raises(DocumentLoadError):
            load_document_bytes(b"\x00" * (13 * 1024 * 1024), "document.png")

    def test_rejects_multipage_pdf(self) -> None:
        fitz = pytest.importorskip("fitz")
        document = fitz.open()
        document.new_page()
        document.new_page()
        payload = document.tobytes()
        document.close()
        with pytest.raises(DocumentLoadError):
            load_document_bytes(payload, "document.pdf")

    def test_rejects_encrypted_pdf(self) -> None:
        fitz = pytest.importorskip("fitz")
        document = fitz.open()
        document.new_page()
        payload = document.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="secret")
        document.close()
        with pytest.raises(DocumentLoadError):
            load_document_bytes(payload, "document.pdf")

    @requires_archive
    def test_loads_png_and_pdf_equivalently(self) -> None:
        png = load_document(ARCHIVE / "documents" / "open-001.png")
        pdf = load_document(ARCHIVE / "documents" / "open-001.pdf")
        assert png.width > 0 and png.height > 0
        assert pdf.page_count == 1
        assert abs(png.width / png.height - pdf.width / pdf.height) < 0.02


@requires_ocr
class TestSyntheticDocumentEndToEnd:
    """The whole chain on a document the project has never seen before."""

    @pytest.fixture(scope="class")
    @classmethod
    def analysed(cls):
        pipeline = Pipeline(use_cache=False)
        payload = render_study_certificate(SYNTHETIC_VALUES)
        result = pipeline.analyze_bytes(payload, "synthetic-study.png", "study_certificate")
        return result

    def test_all_values_are_read_from_pixels(self, analysed) -> None:
        assert analysed.fields["student_name"] == SYNTHETIC_VALUES["student_name"]
        assert analysed.fields["university"] == SYNTHETIC_VALUES["university"]
        assert analysed.fields["program"] == SYNTHETIC_VALUES["program"]
        assert analysed.fields["study_form"] == SYNTHETIC_VALUES["study_form"]
        assert analysed.fields["document_number"] == SYNTHETIC_VALUES["document_number"]

    def test_mixed_date_formats_normalize_identically(self, analysed) -> None:
        assert analysed.fields["study_start"] == "2021-09-01"
        assert analysed.fields["study_end"] == "2025-08-31"
        assert analysed.fields["issue_date"] == "2025-06-15"

    def test_marks_and_validation(self, analysed) -> None:
        assert analysed.fields["signature_present"] is True
        assert analysed.fields["seal_present"] is True
        assert analysed.validation.status == "document_ok"
        assert set(analysed.validation.sources) == {"rule:3.1", "rule:3.2", "rule:3.3"}

    def test_contract_shape(self, analysed) -> None:
        payload = {
            "document_id": "synthetic-1",
            "document_type": "study_certificate",
            "fields": analysed.fields,
            "validation": analysed.validation.as_dict(),
            "answers": [
                answer.model_dump()
                for answer in analysed.answers(
                    [
                        ("s-1", default_completeness_question()),
                        ("s-2", "Укажи значение поля «Дата выдачи»."),
                        ("s-3", "Какова стоимость обучения за следующий семестр?"),
                    ]
                )
            ],
        }
        document = DocumentResult(**payload)
        assert set(document.fields) == set(FIELD_KEYS["study_certificate"])
        assert document.answers[0].value == "document_ok"
        assert document.answers[1].value == "2025-06-15"
        assert document.answers[1].sources == ["field:issue_date"]
        assert document.answers[2].answerable is False

    def test_api_response_is_serialisable(self, analysed) -> None:
        response = build_api_response(analysed, include_boxes=False)
        dumped = response.model_dump()
        assert dumped["document_type"] == "study_certificate"
        # 8 text fields + 2 graphic marks of this type.
        assert len(dumped["field_views"]) == 8
        assert len(dumped["mark_views"]) == 2
        assert dumped["ocr_text"]

    def test_negative_mark_case(self) -> None:
        """Same layout, no ink and no seal mock-up: both flags must be False."""
        pipeline = Pipeline(use_cache=False)
        payload = render_study_certificate(SYNTHETIC_VALUES, include_signature=False, include_seal=False)
        result = pipeline.analyze_bytes(payload, "synthetic-no-marks.png", "study_certificate")
        assert result.fields["signature_present"] is False
        assert result.fields["seal_present"] is False
        assert set(result.validation.missing_fields) == {"signature_present", "seal_present"}
        assert set(result.validation.sources) == {"rule:3.2", "rule:3.3"}

    def test_missing_text_field_is_not_found(self) -> None:
        pipeline = Pipeline(use_cache=False)
        values = dict(SYNTHETIC_VALUES)
        values.pop("document_number")
        values.pop("study_start")
        payload = render_study_certificate(values)
        result = pipeline.analyze_bytes(payload, "synthetic-missing.png", "study_certificate")
        assert result.fields["document_number"] == NOT_FOUND
        assert result.fields["study_start"] == NOT_FOUND
        # Absence of two fields must not erase the remaining values.
        assert result.fields["student_name"] == SYNTHETIC_VALUES["student_name"]
        assert set(result.validation.missing_fields) == {"document_number", "study_start"}
        assert result.validation.sources == ["rule:3.1"]

    def test_auto_type_on_synthetic_page(self) -> None:
        pipeline = Pipeline(use_cache=False)
        payload = render_study_certificate(SYNTHETIC_VALUES)
        result = pipeline.analyze_bytes(payload, "synthetic-auto.png", "auto")
        assert result.document_type == "study_certificate"
        assert result.document_type_source == "auto"


@requires_archive
@requires_ocr
@pytest.mark.slow
class TestPipelineOnOpenSet:
    @pytest.fixture(scope="class")
    @classmethod
    def pipeline(cls):
        return Pipeline(use_cache=True)

    def test_single_document_contract(self, pipeline, documents) -> None:
        entry = documents[0]
        result = pipeline.analyze_path(ARCHIVE / entry["image"], entry["document_type"], entry["document_id"])
        payload = {
            "document_id": entry["document_id"],
            "document_type": entry["document_type"],
            "fields": result.fields,
            "validation": result.validation.as_dict(),
            "answers": [
                answer.model_dump()
                for answer in result.answers([("Q1", "Содержит ли документ все обязательные сведения?")])
            ],
        }
        parsed = DocumentResult(**payload)
        assert set(parsed.fields) == set(FIELD_KEYS[entry["document_type"]])
        assert parsed.answers[0].value in {"document_ok", "missing_fields"}

    def test_missing_field_does_not_erase_others(self, pipeline) -> None:
        result = pipeline.analyze_path(ARCHIVE / "documents/open-007.png", "study_certificate", "open-007")
        assert result.fields["program"] == NOT_FOUND
        assert result.fields["study_form"] == NOT_FOUND
        assert result.fields["student_name"] != NOT_FOUND
        assert result.fields["document_number"] != NOT_FOUND
        assert set(result.validation.missing_fields) == {"program", "study_form"}

    def test_renamed_files_and_renumbered_ids_are_irrelevant(
        self, tmp_path: Path, documents, questions
    ) -> None:
        """Integrity test: predictions must not depend on names or ids."""
        random_gen = random.Random(7)
        anon_dir = tmp_path / "anon"
        anon_dir.mkdir()

        id_map: dict[str, str] = {}
        anon_documents = []
        for index, entry in enumerate(documents):
            source = ARCHIVE / entry["image"]
            stem = "".join(random_gen.choice(string.ascii_lowercase + string.digits) for _ in range(14))
            target = anon_dir / f"{stem}{source.suffix}"
            shutil.copyfile(source, target)
            new_id = f"zzz-{index + 1:03d}"
            id_map[entry["document_id"]] = new_id
            anon_documents.append(
                {
                    "document_id": new_id,
                    "document_type": entry["document_type"],
                    "image": target.name,
                    "pdf": target.name,
                }
            )
        anon_questions = [
            {
                "question_id": item["question_id"].replace(item["document_id"], id_map[item["document_id"]]),
                "document_id": id_map[item["document_id"]],
                "question": item["question"],
            }
            for item in questions
        ]
        documents_path = tmp_path / "documents.json"
        questions_path = tmp_path / "questions.json"
        documents_path.write_text(json.dumps(anon_documents, ensure_ascii=False), encoding="utf-8")
        questions_path.write_text(json.dumps(anon_questions, ensure_ascii=False), encoding="utf-8")
        output = tmp_path / "results.json"
        summary = run_batch(documents_path, questions_path, anon_dir, output, verbose=False)
        assert summary["failures"] == []

        baseline = Pipeline(use_cache=False)
        grouped = group_questions(anon_questions)
        predictions = {
            item["document_id"]: item for item in json.loads(output.read_text(encoding="utf-8"))["documents"]
        }

        for entry in documents:
            reference_result = baseline.analyze_path(
                ARCHIVE / entry["image"], entry["document_type"], entry["document_id"]
            )
            anon = predictions[id_map[entry["document_id"]]]
            assert anon["fields"] == reference_result.fields, entry["document_id"]
            assert anon["validation"] == reference_result.validation.as_dict(), entry["document_id"]
            expected_answers = reference_result.answers(grouped[id_map[entry["document_id"]]])
            got_answers = {item["question_id"]: item for item in anon["answers"]}
            for answer in expected_answers:
                got = got_answers[answer.question_id]
                assert got["answerable"] == answer.answerable
                assert got["value"] == answer.value
                assert set(got["sources"]) == set(answer.sources)


@requires_archive
@requires_ocr
@pytest.mark.slow
class TestBatchExport:
    def test_export_is_schema_valid_and_complete(self, tmp_path: Path) -> None:
        output = tmp_path / "results.json"
        summary = run_batch(
            ARCHIVE / "documents.json",
            ARCHIVE / "questions.json",
            ARCHIVE,
            output,
            verbose=False,
        )
        assert summary["failures"] == []
        assert summary["documents"] == 30
        assert summary["questions"] == 120

        payload = json.loads(output.read_text(encoding="utf-8"))
        model = ResultsFile(**payload)
        assert model.version == "1.0"
        assert len(model.documents) == 30
        question_ids = [answer.question_id for document in model.documents for answer in document.answers]
        assert len(question_ids) == len(set(question_ids)) == 120
        for document in model.documents:
            for answer in document.answers:
                assert answer.answer.strip()
                if not answer.answerable:
                    assert answer.value is None
                    assert answer.sources == []

    def test_pdf_representation_is_usable(self, tmp_path: Path) -> None:
        output = tmp_path / "results_pdf.json"
        summary = run_batch(
            ARCHIVE / "documents.json",
            ARCHIVE / "questions.json",
            ARCHIVE,
            output,
            prefer="pdf",
            verbose=False,
        )
        assert summary["failures"] == []
        payload = json.loads(output.read_text(encoding="utf-8"))
        model = ResultsFile(**payload)
        assert len(model.documents) == 30
        first = model.documents[0]
        assert first.fields["student_name"] != NOT_FOUND
        assert first.validation.status in {"document_ok", "missing_fields"}
