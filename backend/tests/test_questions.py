"""Гоняем движок вопросов: четыре класса вопросов, синонимы, честные отказы."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.services import question_engine as qe
from backend.app.services.document_config import get_registry
from backend.app.services.parse_fields import NOT_FOUND
from backend.app.services.question_engine import (
    FINAL_DECISION_VALUE,
    Intent,
    build_answer,
    classify_question,
    default_completeness_question,
)
from backend.app.services.rules_engine import validate

from .conftest import ARCHIVE
from .test_rules import complete_fields

registry = get_registry()


class TestClassification:
    @pytest.mark.parametrize(
        "question",
        [
            "Содержит ли документ все обязательные сведения по Положению?",
            "Все ли обязательные сведения есть?",
            "Каких сведений не хватает?",
            "Проверь документ",
            "Документ в порядке?",
            "Комплектность документа",
        ],
    )
    def test_completeness(self, question: str) -> None:
        spec = registry.require("study_certificate")
        assert classify_question(question, spec).intent is Intent.COMPLETENESS

    @pytest.mark.parametrize(
        "question",
        [
            "Кто принимает окончательное решение о принятии документа?",
            "Кто принимает решение?",
            "Кто утверждает документ?",
        ],
    )
    def test_final_decision(self, question: str) -> None:
        spec = registry.require("study_certificate")
        assert classify_question(question, spec).intent is Intent.FINAL_DECISION

    @pytest.mark.parametrize(
        ("question", "key"),
        [
            ("Укажи значение поля «ФИО студента».", "student_name"),
            ("Укажи значение поля «Дата выдачи».", "issue_date"),
            ("Какова дата выдачи?", "issue_date"),
            ("Какое название вуза указано?", "university"),
            ("Назови направление подготовки", "program"),
            ("Форма обучения?", "study_form"),
            ("Укажи значение поля «Номер справки».", "document_number"),
        ],
    )
    def test_field_value(self, question: str, key: str) -> None:
        spec = registry.require("study_certificate")
        analysis = classify_question(question, spec)
        assert analysis.intent is Intent.FIELD_VALUE
        assert analysis.field_key == key

    @pytest.mark.parametrize(
        ("document_type", "question", "key"),
        [
            ("academic_leave_application", "Укажи значение поля «Учебная группа».", "group"),
            ("academic_leave_application", "На каком курсе студент?", "course"),
            ("academic_leave_application", "Укажи значение поля «Причина отпуска».", "leave_reason"),
            ("academic_leave_application", "Дата начала отпуска?", "leave_start"),
            ("internship_certificate", "Укажи значение поля «Оценка».", "grade"),
            ("internship_certificate", "Название организации", "organization"),
            ("internship_certificate", "Кто руководитель практики?", "supervisor_name"),
        ],
    )
    def test_field_value_other_types(self, document_type: str, question: str, key: str) -> None:
        spec = registry.require(document_type)
        assert classify_question(question, spec).field_key == key

    @pytest.mark.parametrize(
        "question",
        [
            "Какова стоимость обучения за следующий семестр?",
            "Какой размер стипендии положен студенту?",
            "Какой срок рассмотрения документа деканатом?",
            "По какому номеру телефона можно связаться с деканатом?",
            "В каком кабинете принимают оригиналы документов?",
            "Сколько стоит обучение?",
            "Есть ли у студента академические задолженности?",
            "Каков адрес электронной почты деканата?",
            "Можно ли перевестись на другой курс?",
        ],
    )
    def test_no_data_topics(self, question: str) -> None:
        spec = registry.require("study_certificate")
        analysis = classify_question(question, spec)
        assert analysis.intent is Intent.UNKNOWN
        assert analysis.topic, question

    def test_unknown_question_is_honest(self) -> None:
        spec = registry.require("study_certificate")
        analysis = classify_question("Расскажи анекдот про деканат", spec)
        assert analysis.intent is Intent.UNKNOWN
        assert analysis.topic is None

    def test_seal_and_signature_questions(self) -> None:
        spec = registry.require("study_certificate")
        assert classify_question("Есть ли печать?", spec).field_key == "seal_present"
        assert classify_question("Есть ли подпись?", spec).field_key == "signature_present"


class TestAnswers:
    def test_completeness_ok(self) -> None:
        spec = registry.require("study_certificate")
        fields = complete_fields("study_certificate")
        validation = validate(spec, fields)
        answer = build_answer("Q1", default_completeness_question(), spec, fields, validation)
        assert answer.answerable is True
        assert answer.value == "document_ok"
        assert set(answer.sources) == {"rule:3.1", "rule:3.2", "rule:3.3"}
        assert "3.1" in answer.answer

    def test_completeness_missing_lists_elements(self) -> None:
        spec = registry.require("study_certificate")
        fields = complete_fields("study_certificate")
        fields["issue_date"] = NOT_FOUND
        validation = validate(spec, fields)
        answer = build_answer("Q1", default_completeness_question(), spec, fields, validation)
        assert answer.answerable is True
        assert answer.value == "missing_fields"
        assert answer.sources == ["rule:3.1"]
        assert "Дата выдачи" in answer.answer

    def test_field_value_found(self) -> None:
        spec = registry.require("study_certificate")
        fields = complete_fields("study_certificate")
        fields["issue_date"] = "2026-09-01"
        validation = validate(spec, fields)
        answer = build_answer("Q2", "Укажи значение поля «Дата выдачи».", spec, fields, validation)
        assert answer.answerable is True
        assert answer.value == "2026-09-01"
        assert answer.sources == ["field:issue_date"]

    def test_field_value_missing_is_not_answerable(self) -> None:
        spec = registry.require("study_certificate")
        fields = complete_fields("study_certificate")
        fields["issue_date"] = NOT_FOUND
        validation = validate(spec, fields)
        answer = build_answer("Q2", "Укажи значение поля «Дата выдачи».", spec, fields, validation)
        assert answer.answerable is False
        assert answer.value is None
        assert answer.sources == []
        assert "не найдено" in answer.answer.lower()

    def test_final_decision(self) -> None:
        spec = registry.require("academic_leave_application")
        fields = complete_fields("academic_leave_application")
        validation = validate(spec, fields)
        answer = build_answer("Q3", "Кто принимает окончательное решение?", spec, fields, validation)
        assert answer.answerable is True
        assert answer.value == FINAL_DECISION_VALUE
        assert answer.sources == ["rule:1.3"]

    def test_no_data_topic_is_not_answerable(self) -> None:
        spec = registry.require("study_certificate")
        fields = complete_fields("study_certificate")
        validation = validate(spec, fields)
        answer = build_answer("Q4", "Какова стоимость обучения за следующий семестр?", spec, fields, validation)
        assert answer.answerable is False
        assert answer.value is None
        assert answer.sources == []
        assert answer.answer

    def test_unknown_question_uses_honest_phrase(self) -> None:
        spec = registry.require("study_certificate")
        fields = complete_fields("study_certificate")
        validation = validate(spec, fields)
        answer = build_answer("Q5", "Посоветуй фильм", spec, fields, validation)
        assert answer.answerable is False
        assert answer.value is None
        assert qe.UNKNOWN_ANSWER == answer.answer

    def test_missing_mark_question(self) -> None:
        spec = registry.require("study_certificate")
        fields = complete_fields("study_certificate")
        fields["seal_present"] = False
        validation = validate(spec, fields)
        answer = build_answer("Q6", "Есть ли печать?", spec, fields, validation)
        assert answer.answerable is False
        assert answer.sources == []


@pytest.mark.skipif(ARCHIVE is None, reason="открытый архив кейса не найден")
class TestOpenSetClassificationCoverage:
    """Любую формулировку, которая реально есть в questions.json, обязаны разобрать правильно."""

    def test_all_manifest_questions(self) -> None:
        assert ARCHIVE is not None
        documents = json.loads((ARCHIVE / "documents.json").read_text(encoding="utf-8"))
        questions = json.loads((ARCHIVE / "questions.json").read_text(encoding="utf-8"))
        reference = json.loads((ARCHIVE / "reference_results.json").read_text(encoding="utf-8"))
        type_by_id = {item["document_id"]: item["document_type"] for item in documents}
        ref_answers = {
            answer["question_id"]: answer for doc in reference["documents"] for answer in doc["answers"]
        }
        failures: list[str] = []
        for item in questions:
            spec = registry.require(type_by_id[item["document_id"]])
            analysis = classify_question(item["question"], spec)
            ref = ref_answers[item["question_id"]]
            if ref["value"] == FINAL_DECISION_VALUE:
                expected = Intent.FINAL_DECISION
            elif ref["value"] in ("document_ok", "missing_fields"):
                expected = Intent.COMPLETENESS
            elif ref["answerable"] and ref["sources"] and ref["sources"][0].startswith("field:"):
                expected = Intent.FIELD_VALUE
            else:
                # поля нет или вопроса про документ нет: тут ждём отказ, а не какой-то другой класс
                if analysis.intent not in (Intent.FIELD_VALUE, Intent.UNKNOWN):
                    failures.append(f"{item['question_id']}: ожидался отказ, получено {analysis.intent}")
                continue
            if analysis.intent is not expected:
                failures.append(f"{item['question_id']}: {analysis.intent} != {expected} :: {item['question']}")
        assert not failures, "\n".join(failures)
