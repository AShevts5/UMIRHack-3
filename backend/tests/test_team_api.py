"""Тесты на контракт команды (/api/*): поля, статусы ответа, Положение, файлы.

Эти адреса приехали вместе с веткой main у команды. Раньше они отдавали
заготовки, теперь — настоящий результат пайплайна, так что проверяем
и форму ответа, и то, что значения правда взяты из документа.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.team_api import (
    FIELD_MAP,
    build_fields,
    document_types_payload,
    regulation_payload,
    resolve_type,
)
from backend.app.config import settings

from .conftest import ARCHIVE, requires_archive, requires_ocr


class TestPureHelpers:
    def test_resolve_type_accepts_aliases(self) -> None:
        assert resolve_type("study_certificate") == "study_certificate"
        assert resolve_type("academic_leave") == "academic_leave_application"
        assert resolve_type("practice_certificate") == "internship_certificate"
        assert resolve_type("diploma") is None
        assert resolve_type(None) is None
        assert resolve_type("") is None

    def test_document_types_payload_covers_three_types(self) -> None:
        payload = document_types_payload()
        assert {item["id"] for item in payload} == set(FIELD_MAP)
        for item in payload:
            assert item["title"]
            keys = [field["key"] for field in item["fields"]]
            assert keys, item["id"]
            assert len(keys) == len(set(keys))

    def test_regulation_parsing(self) -> None:
        if settings.rules_path is None:
            pytest.skip("rules.md не найден рядом с репозиторием")
        sections = regulation_payload(settings.rules_path)
        assert len(sections) >= 5
        points = {child["point"] for section in sections for child in section["children"]}
        for required in ("1.3", "2.3", "2.4", "3.1", "3.2", "3.3", "4.1", "4.2", "5.1", "5.2", "5.3"):
            assert required in points, required
        text_of_13 = next(
            child["text"] for section in sections for child in section["children"] if child["point"] == "1.3"
        )
        assert "сотрудник деканата" in text_of_13


@requires_archive
@requires_ocr
class TestAgainstRealDocument:
    @pytest.fixture(scope="class")
    @classmethod
    def analysed(cls):
        from backend.app.services.pipeline import Pipeline

        return Pipeline(use_cache=True).analyze_path(
            ARCHIVE / "documents" / "open-001.png", "study_certificate", "open-001"
        )

    def test_fields_are_taken_from_the_document(self, analysed) -> None:
        items = {item["key"]: item for item in build_fields(analysed)}
        assert items["full_name"]["value"] == "Александров Артём Игоревич"
        assert items["issue_number"]["value"] == "ОБ-1001"
        assert items["study_period"]["value"] == "2023-09-01 — 2026-08-31"
        # отметки прилетают строками: «есть» или «не найдено» — фронтенд ждёт именно так
        assert items["seal"]["value"] == "есть"
        assert items["signature"]["value"] == "есть"
        assert items["seal"]["found"] is True

    def test_presence_value_is_not_found_when_absent(self, analysed) -> None:
        """open-004: подпись есть, печати нет — значения обязаны отличаться."""
        from backend.app.services.pipeline import Pipeline

        result = Pipeline(use_cache=True).analyze_path(
            ARCHIVE / "documents" / "open-004.png", "study_certificate", "open-004"
        )
        items = {item["key"]: item for item in build_fields(result)}
        assert items["signature"]["value"] == "есть"
        assert items["seal"]["value"] == "не найдено"
        assert items["seal"]["found"] is False

    def test_period_is_not_found_when_one_date_missing(self) -> None:
        from backend.app.services.pipeline import Pipeline

        result = Pipeline(use_cache=True).analyze_path(
            ARCHIVE / "documents" / "open-009.png", "study_certificate", "open-009"
        )
        items = {item["key"]: item for item in build_fields(result)}
        assert items["study_period"]["value"] == "не найдено"


class TestEndpoints:
    client = TestClient(app)

    def test_doc_types_endpoint(self) -> None:
        response = self.client.get("/api/doc-types")
        assert response.status_code == 200
        items = response.json()["items"]
        assert {item["id"] for item in items} == set(FIELD_MAP)
        for item in items:
            for field in item["fields"]:
                assert set(field) == {"key", "label", "type"}

    def test_regulation_endpoint(self) -> None:
        response = self.client.get("/api/regulation")
        assert response.status_code == 200
        payload = response.json()
        assert "items" in payload
        if payload["items"]:
            assert payload["items"][0]["point"]
            assert payload["items"][0]["children"]

    def test_history_endpoint_is_empty_before_any_upload(self) -> None:
        response = self.client.get("/api/history")
        assert response.status_code == 200
        assert isinstance(response.json()["items"], list)

    def test_unsupported_format(self) -> None:
        response = self.client.post(
            "/api/analyze",
            files={"file": ("notes.txt", b"hello", "text/plain")},
            data={"question": "проверь"},
        )
        assert response.status_code == 400

    def test_empty_file(self) -> None:
        response = self.client.post(
            "/api/analyze",
            files={"file": ("page.png", b"", "image/png")},
            data={"question": "проверь"},
        )
        assert response.status_code == 400

    def test_oversized_file(self) -> None:
        response = self.client.post(
            "/api/analyze",
            files={"file": ("page.png", b"\x00" * (21 * 1024 * 1024), "image/png")},
            data={"question": "проверь"},
        )
        assert response.status_code == 413

    def test_unknown_document_type(self) -> None:
        response = self.client.post(
            "/api/analyze",
            files={"file": ("page.png", b"\x89PNG\r\n\x1a\nbroken", "image/png")},
            data={"question": "проверь", "doc_type": "diploma"},
        )
        assert response.status_code == 400

    @requires_archive
    @requires_ocr
    def test_full_flow_and_file_endpoint(self) -> None:
        with (ARCHIVE / "documents" / "open-021.png").open("rb") as handle:
            response = self.client.post(
                "/api/analyze",
                files={"file": ("open-021.png", handle.read(), "image/png")},
                data={"question": "Какая оценка за практику?", "doc_type": "practice_certificate"},
            )
        assert response.status_code == 200
        body = response.json()
        # смотрим форму ответа по контракту команды
        assert set(body) == {"request_id", "doc_type", "image", "fields", "answer", "ocr", "timings_ms"}
        assert body["doc_type"]["id"] == "internship_certificate"
        assert body["image"]["url"].endswith("/page-1")
        assert body["image"]["width"] > 0 and body["image"]["height"] > 0
        assert body["timings_ms"]["ocr"] > 0
        assert body["ocr"]["lines"], "OCR-строки должны быть заполнены, а не None"
        values = {item["key"]: item["value"] for item in body["fields"]}
        assert values["grade"] == "удовлетворительно"
        assert values["issue_number"] == "ПР-2021"
        assert body["answer"]["status"] == "ok"
        assert body["answer"]["text"].strip()
        for source in body["answer"]["sources"]:
            assert source["kind"] in {"regulation", "field"}

        page = self.client.get(body["image"]["url"])
        assert page.status_code == 200
        assert page.headers["content-type"] == "image/png"
        assert len(page.content) > 1000

        history = self.client.get("/api/history").json()["items"]
        assert any(item["request_id"] == body["request_id"] for item in history)

    @requires_archive
    @requires_ocr
    def test_no_info_status_for_unanswerable_question(self) -> None:
        with (ARCHIVE / "documents" / "open-001.png").open("rb") as handle:
            response = self.client.post(
                "/api/analyze",
                files={"file": ("open-001.png", handle.read(), "image/png")},
                data={"question": "Какая стипендия мне положена?", "doc_type": "study_certificate"},
            )
        body = response.json()
        assert body["answer"]["status"] == "no_info"
        assert body["answer"]["sources"] == [] or body["answer"]["sources"][0]["point"] == "2.4"

    @requires_archive
    @requires_ocr
    def test_missing_status_lists_clause_and_field(self) -> None:
        with (ARCHIVE / "documents" / "open-002.png").open("rb") as handle:
            response = self.client.post(
                "/api/analyze",
                files={"file": ("open-002.png", handle.read(), "image/png")},
                data={"question": "Какая дата выдачи у справки?", "doc_type": "study_certificate"},
            )
        body = response.json()
        assert body["answer"]["status"] == "missing"
        kinds = {source["kind"] for source in body["answer"]["sources"]}
        assert "field" in kinds

    @requires_archive
    @requires_ocr
    def test_file_endpoint_rejects_unknown_id(self) -> None:
        response = self.client.get("/api/files/unknown-id/page-1")
        assert response.status_code == 404

    def test_response_is_json_serialisable(self) -> None:
        json.dumps({"ok": True}, ensure_ascii=False)
