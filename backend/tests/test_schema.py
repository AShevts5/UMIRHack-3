"""Contract tests for the results JSON and the HTTP API."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.app.main import app
from backend.app.schemas import FIELD_KEYS, Answer, DocumentResult, ResultsFile, Validation
from backend.app.services.document_config import get_registry

from .conftest import ARCHIVE, requires_archive, requires_ocr
from .test_rules import complete_fields

client = TestClient(app)


def png_bytes(width: int = 40, height: int = 40, colour=(255, 255, 255)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buffer, format="PNG")
    return buffer.getvalue()


class TestHealthEndpoints:
    def test_health_shape(self) -> None:
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] in {"ok", "degraded"}
        assert payload["ocr"]["engine"] == "tesseract"
        assert set(payload["document_types"]) == set(FIELD_KEYS)

    def test_document_types_shape(self) -> None:
        response = client.get("/api/v1/document-types")
        assert response.status_code == 200
        payload = response.json()
        codes = {item["code"] for item in payload["document_types"]}
        assert codes == set(FIELD_KEYS)
        for item in payload["document_types"]:
            assert item["fields"], item["code"]
            for rule in item["required_rules"]:
                assert rule.startswith("rule:")
        assert payload["default_question"]


class TestAnalyzeEndpointValidation:
    def test_unsupported_extension(self) -> None:
        response = client.post(
            "/api/v1/analyze",
            files={"file": ("notes.txt", b"hello", "text/plain")},
            data={"document_type": "study_certificate"},
        )
        assert response.status_code == 422
        assert "не поддерживается" in response.json()["detail"]

    def test_unknown_document_type(self) -> None:
        response = client.post(
            "/api/v1/analyze",
            files={"file": ("page.png", png_bytes(), "image/png")},
            data={"document_type": "diploma"},
        )
        assert response.status_code == 422
        assert "Неизвестный тип" in response.json()["detail"]

    def test_broken_image(self) -> None:
        response = client.post(
            "/api/v1/analyze",
            files={"file": ("page.png", b"\x89PNG\r\n\x1a\nbroken", "image/png")},
            data={"document_type": "study_certificate"},
        )
        assert response.status_code == 422

    def test_too_long_question(self) -> None:
        response = client.post(
            "/api/v1/analyze",
            files={"file": ("page.png", png_bytes(), "image/png")},
            data={"document_type": "study_certificate", "question": "а" * 501},
        )
        assert response.status_code == 422

    def test_empty_upload(self) -> None:
        response = client.post(
            "/api/v1/analyze",
            files={"file": ("page.png", b"", "image/png")},
            data={"document_type": "study_certificate"},
        )
        assert response.status_code == 422


@requires_archive
@requires_ocr
class TestAnalyzeEndpointOpenSet:
    def _post(self, document_id: str, document_type: str, question: str | None = None):
        with (ARCHIVE / "documents" / f"{document_id}.png").open("rb") as handle:
            return client.post(
                "/api/v1/analyze",
                files={"file": (f"{document_id}.png", handle.read(), "image/png")},
                data={"document_type": document_type, **({"question": question} if question else {})},
            )

    def test_default_question_is_completeness(self) -> None:
        response = self._post("open-001", "study_certificate")
        assert response.status_code == 200
        payload = response.json()
        assert payload["answer"]["value"] in {"document_ok", "missing_fields"}
        assert payload["answer"]["sources"] == payload["validation"]["sources"]

    def test_field_question_returns_value_and_source(self) -> None:
        response = self._post("open-011", "academic_leave_application", "Укажи значение поля «Учебная группа».")
        payload = response.json()
        assert payload["answer"]["sources"] == ["field:group"]
        assert payload["answer"]["value"] == payload["fields"]["group"]

    def test_missing_field_question_is_not_answerable(self) -> None:
        response = self._post("open-002", "study_certificate", "Укажи значение поля «Дата выдачи».")
        payload = response.json()
        assert payload["answer"]["answerable"] is False
        assert payload["answer"]["value"] is None
        assert payload["answer"]["sources"] == []

    def test_no_data_question(self) -> None:
        response = self._post("open-004", "study_certificate", "Какова стоимость обучения за следующий семестр?")
        payload = response.json()
        assert payload["answer"]["answerable"] is False
        assert payload["answer"]["sources"] == []

    def test_auto_type_bonus(self) -> None:
        response = self._post("open-021", "auto")
        assert response.status_code == 200
        payload = response.json()
        assert payload["document_type"] == "internship_certificate"
        assert payload["document_type_source"] == "auto"

    def test_boxes_and_mark_views_present(self) -> None:
        response = self._post("open-021", "internship_certificate")
        payload = response.json()
        assert payload["boxes"]
        marks = {item["key"]: item for item in payload["mark_views"]}
        assert marks["signature_present"]["present"] is True
        assert marks["seal_present"]["present"] is True
        assert marks["signature_present"]["box"] is not None

    def test_ocr_reuses_cache_between_requests(self) -> None:
        """Two identical requests must not run OCR twice (no per-request model load)."""
        from backend.app.services.pipeline import pipeline

        before = pipeline.stats["documents"]
        self._post("open-005", "study_certificate")
        middle = pipeline.stats["documents"]
        self._post("open-005", "study_certificate")
        after = pipeline.stats["documents"]
        assert middle == before + 1
        assert after == middle  # served from the cache


class TestJsonContractOfExport:
    def test_answers_accept_bool_and_none(self) -> None:
        Answer(question_id="Q", answerable=False, value=None, answer="нет данных", sources=[])
        with pytest.raises(ValueError):
            Answer(question_id="Q", answerable="yes", value=None, answer="x", sources=[])  # type: ignore[arg-type]

    def test_validation_status_enum(self) -> None:
        Validation(status="document_ok", missing_fields=[], sources=["rule:1.3"])
        with pytest.raises(ValueError):
            Validation(status="accepted", missing_fields=[], sources=[])  # type: ignore[arg-type]

    def test_export_round_trip(self, tmp_path: Path) -> None:
        spec = get_registry().require("internship_certificate")
        fields = complete_fields(spec.code)
        payload = {
            "version": "1.0",
            "documents": [
                {
                    "document_id": "unit-1",
                    "document_type": spec.code,
                    "fields": fields,
                    "validation": {"status": "document_ok", "missing_fields": [], "sources": list(spec.required_rules)},
                    "answers": [
                        {
                            "question_id": "unit-1-Q1",
                            "answerable": True,
                            "value": "document_ok",
                            "answer": "Документ содержит все обязательные сведения.",
                            "sources": list(spec.required_rules),
                        }
                    ],
                }
            ],
        }
        path = tmp_path / "results.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        restored = ResultsFile(**json.loads(path.read_text(encoding="utf-8")))
        assert restored.documents[0].fields == fields
        DocumentResult(**payload["documents"][0])
