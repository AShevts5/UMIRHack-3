"""Модели данных и контракт results.json.

Повторяют ``results.schema.json`` из пакета кейса, так что пакетный экспорт
сразу выходит валидным: лишние ключи запрещены, состав ключей и типы за нас проверяет
Pydantic.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

DocumentTypeCode = Literal["study_certificate", "academic_leave_application", "internship_certificate"]
ValidationStatus = Literal["document_ok", "missing_fields"]

NOT_FOUND = "не найдено"

#: Наборы ключей по типам документов (порядок важен для карточки в интерфейсе, не перемешивать).
FIELD_KEYS: dict[str, tuple[str, ...]] = {
    "study_certificate": (
        "student_name",
        "university",
        "program",
        "study_form",
        "study_start",
        "study_end",
        "document_number",
        "issue_date",
        "signature_present",
        "seal_present",
    ),
    "academic_leave_application": (
        "student_name",
        "group",
        "course",
        "leave_reason",
        "leave_start",
        "leave_end",
        "application_date",
        "signature_present",
    ),
    "internship_certificate": (
        "student_name",
        "organization",
        "internship_start",
        "internship_end",
        "supervisor_name",
        "grade",
        "document_number",
        "issue_date",
        "signature_present",
        "seal_present",
    ),
}


class StrictModel(BaseModel):
    """Строгая модель: лишних ключей нет, типы не подменяются.

    ``strict=True`` стоит специально, не трогать: проверка сравнивает типы значений строго
    (``type(a) is type(b)``), так что строка "true" нигде в пайплайне не должна
    втихую превратиться в ``True`` или ``1``.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True, strict=True)


class StudyCertificateFields(StrictModel):
    student_name: str
    university: str
    program: str
    study_form: str
    study_start: str
    study_end: str
    document_number: str
    issue_date: str
    signature_present: bool
    seal_present: bool


class AcademicLeaveFields(StrictModel):
    student_name: str
    group: str
    course: str
    leave_reason: str
    leave_start: str
    leave_end: str
    application_date: str
    signature_present: bool


class InternshipCertificateFields(StrictModel):
    student_name: str
    organization: str
    internship_start: str
    internship_end: str
    supervisor_name: str
    grade: str
    document_number: str
    issue_date: str
    signature_present: bool
    seal_present: bool


class Validation(StrictModel):
    status: ValidationStatus
    missing_fields: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)

    @field_validator("sources")
    @classmethod
    def check_sources(cls, value: list[str]) -> list[str]:
        for source in value:
            if not source.startswith("rule:") or "." not in source:
                raise ValueError(f"неверный формат источника правила: {source}")
        return value


class Answer(StrictModel):
    question_id: str
    answerable: bool
    value: str | None
    answer: str
    sources: list[str] = Field(default_factory=list)

    @field_validator("answer")
    @classmethod
    def check_answer(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("answer не может быть пустым")
        return value

    @field_validator("sources")
    @classmethod
    def check_sources(cls, value: list[str]) -> list[str]:
        for source in value:
            if not (source.startswith("rule:") or source.startswith("field:")):
                raise ValueError(f"неверный формат источника: {source}")
        if len(set(value)) != len(value):
            raise ValueError("источники не должны повторяться")
        return value


class DocumentResult(StrictModel):
    document_id: str
    document_type: DocumentTypeCode
    fields: dict[str, Any]
    validation: Validation
    answers: list[Answer] = Field(default_factory=list)

    @field_validator("fields")
    @classmethod
    def check_field_keys(cls, value: dict[str, Any], info) -> dict[str, Any]:
        document_type = info.data.get("document_type")
        if document_type and document_type in FIELD_KEYS:
            expected = set(FIELD_KEYS[document_type])
            if set(value) != expected:
                missing = sorted(expected - set(value))
                extra = sorted(set(value) - expected)
                raise ValueError(f"fields: пропущены {missing}, лишние {extra}")
            text_keys = {key for key in expected if not key.endswith("_present")}
            for key in text_keys:
                if not isinstance(value[key], str):
                    raise ValueError(f"fields.{key}: ожидалась строка")
            for key in expected - text_keys:
                if not isinstance(value[key], bool):
                    raise ValueError(f"fields.{key}: ожидался boolean")
        return value


class ResultsFile(StrictModel):
    version: Literal["1.0"] = "1.0"
    documents: list[DocumentResult] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Модели ответов API (для веб-режима)
# ---------------------------------------------------------------------------


class BoundingBox(StrictModel):
    x: int
    y: int
    width: int
    height: int


class FieldView(StrictModel):
    key: str
    label: str
    value: str | bool
    found: bool
    rule: str
    sources: list[str] = Field(default_factory=list)
    box: BoundingBox | None = None
    confidence: float = 0.0


class MarkView(StrictModel):
    key: str
    label: str
    present: bool
    confidence: float
    box: BoundingBox | None = None
    method: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)


class WordBox(StrictModel):
    text: str
    box: BoundingBox
    confidence: float


class AnalysisResponse(StrictModel):
    document_id: str | None = None
    document_type: str
    document_type_title: str = ""
    document_type_source: Literal["request", "auto"] = "request"
    document_type_confidence: float = 0.0
    ocr_text: str
    ocr_engine: str = "tesseract"
    ocr_languages: str = "rus+eng"
    ocr_mean_confidence: float = 0.0
    fields: dict[str, Any]
    field_views: list[FieldView] = Field(default_factory=list)
    mark_views: list[MarkView] = Field(default_factory=list)
    validation: Validation
    validation_message: str = ""
    answer: Answer
    boxes: list[WordBox] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    # для веб-морды: id разбора (по нему /api/v1/ask отвечает без повторного OCR)
    # и страница, которую читал OCR (все рамки лежат в её координатах).
    analysis_id: str | None = None
    page_image_url: str | None = None
    page_width: int = 0
    page_height: int = 0
    # True, если страницу подправляли (поворот, обрезка по листу, свет): тогда
    # интерфейс предложит сравнить с исходником.
    page_enhanced: bool = False
    timings_ms: dict[str, float] = Field(default_factory=dict)


class HealthResponse(StrictModel):
    status: Literal["ok", "degraded"]
    ocr: dict[str, Any]
    document_types: dict[str, str]
    llm: dict[str, Any] = {}  # мини-LLM помощника: state = ready/available/no-model/no-package/off/error
    version: str = "1.0"
