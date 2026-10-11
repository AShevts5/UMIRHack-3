"""Тесты на движок правил и JSON-контракт (rules.md 3.1-3.3, 4.1-4.2, 5.1-5.3)."""

from __future__ import annotations

import pytest

from backend.app.schemas import FIELD_KEYS, Answer, DocumentResult, ResultsFile, Validation
from backend.app.services import rules_engine
from backend.app.services.parse_fields import NOT_FOUND
from backend.app.services.rules_engine import FINAL_DECISION_RULE, STATUS_MISSING, STATUS_OK, validate


def complete_fields(document_type: str) -> dict[str, str | bool]:
    """Синтетическая карточка, где заполнено всё (настоящих персональных данных тут нет)."""
    fields: dict[str, str | bool] = {}
    for key in FIELD_KEYS[document_type]:
        if key.endswith("_present"):
            fields[key] = True
        elif "date" in key or key.endswith(("_start", "_end")):
            fields[key] = "2026-01-01"
        else:
            fields[key] = "значение"
    if "course" in fields:
        fields["course"] = "3"
    return fields


class TestRuleMapping:
    @pytest.mark.parametrize(
        ("document_type", "text_rule", "signature_rule", "seal_rule"),
        [
            ("study_certificate", "rule:3.1", "rule:3.2", "rule:3.3"),
            ("academic_leave_application", "rule:4.1", "rule:4.2", None),
            ("internship_certificate", "rule:5.1", "rule:5.2", "rule:5.3"),
        ],
    )
    def test_rules_come_from_the_regulation(
        self, registry, document_type: str, text_rule: str, signature_rule: str, seal_rule: str | None
    ) -> None:
        spec = registry.require(document_type)
        assert spec.text_rule == text_rule
        assert spec.signature_rule == signature_rule
        assert spec.seal_rule == seal_rule

    def test_full_document_lists_all_requirement_clauses(self, registry) -> None:
        for document_type in FIELD_KEYS:
            spec = registry.require(document_type)
            result = validate(spec, complete_fields(document_type))
            assert result.status == STATUS_OK
            assert result.missing_fields == []
            assert set(result.sources) == set(spec.required_rules)
            # пункты про порядок оформления (3.4/4.5/5.4) в источники пролезать не должны
            assert not any(source.endswith(".4") or source.endswith(".5") for source in result.sources)

    def test_procedural_clauses_are_not_sources(self, registry) -> None:
        spec = registry.require("study_certificate")
        fields = complete_fields("study_certificate")
        fields["signature_present"] = False
        result = validate(spec, fields)
        assert result.sources == ["rule:3.2"]


class TestMissingFields:
    def test_single_text_field(self, registry) -> None:
        spec = registry.require("study_certificate")
        fields = complete_fields("study_certificate")
        fields["issue_date"] = NOT_FOUND
        result = validate(spec, fields)
        assert result.status == STATUS_MISSING
        assert result.missing_fields == ["issue_date"]
        assert result.sources == ["rule:3.1"]
        assert result.missing_labels == ["Дата выдачи"]

    def test_two_text_fields_deduplicate_sources(self, registry) -> None:
        spec = registry.require("study_certificate")
        fields = complete_fields("study_certificate")
        fields["student_name"] = NOT_FOUND
        fields["document_number"] = NOT_FOUND
        result = validate(spec, fields)
        assert set(result.missing_fields) == {"student_name", "document_number"}
        assert result.sources == ["rule:3.1"]

    def test_both_marks_missing(self, registry) -> None:
        spec = registry.require("internship_certificate")
        fields = complete_fields("internship_certificate")
        fields["signature_present"] = False
        fields["seal_present"] = False
        result = validate(spec, fields)
        assert set(result.missing_fields) == {"signature_present", "seal_present"}
        assert set(result.sources) == {"rule:5.2", "rule:5.3"}

    def test_application_has_no_seal_concept(self, registry) -> None:
        spec = registry.require("academic_leave_application")
        fields = complete_fields("academic_leave_application")
        result = validate(spec, fields)
        assert result.status == STATUS_OK
        assert set(result.sources) == {"rule:4.1", "rule:4.2"}
        assert "seal_present" not in fields

    def test_empty_string_counts_as_missing(self, registry) -> None:
        spec = registry.require("academic_leave_application")
        fields = complete_fields("academic_leave_application")
        fields["group"] = ""
        result = validate(spec, fields)
        assert "group" in result.missing_fields


class TestPluralization:
    @pytest.mark.parametrize(
        ("count", "expected"), [(1, "пункт"), (2, "пункта"), (3, "пункта"), (4, "пункта"), (5, "пунктов"), (11, "пунктов")]
    )
    def test_plural(self, count: int, expected: str) -> None:
        assert rules_engine.pluralize_requirements(count) == expected

    def test_clause_list(self) -> None:
        assert rules_engine.clause_list(["rule:3.1", "rule:3.2"]) == "3.1, 3.2"
        assert rules_engine.clause_list([]) == ""


class TestJsonContract:
    def test_validation_rejects_bad_source_format(self) -> None:
        with pytest.raises(ValueError):
            Validation(status="document_ok", missing_fields=[], sources=["3.1"])

    def test_answer_requires_non_empty_text(self) -> None:
        with pytest.raises(ValueError):
            Answer(question_id="Q1", answerable=False, value=None, answer="  ", sources=[])

    def test_answer_source_prefixes(self) -> None:
        with pytest.raises(ValueError):
            Answer(question_id="Q1", answerable=True, value="x", answer="x", sources=["student_name"])
        Answer(question_id="Q1", answerable=True, value="x", answer="x", sources=["field:student_name"])

    def test_document_requires_exact_field_keys(self) -> None:
        fields = complete_fields("study_certificate")
        minimal_answer = {
            "question_id": "Q1",
            "answerable": False,
            "value": None,
            "answer": "нет данных",
            "sources": [],
        }
        payload = {
            "document_id": "x-1",
            "document_type": "study_certificate",
            "fields": fields,
            "validation": {"status": "document_ok", "missing_fields": [], "sources": ["rule:3.1"]},
            "answers": [minimal_answer],
        }
        DocumentResult(**payload)

        broken = dict(payload)
        broken["fields"] = {key: value for key, value in fields.items() if key != "seal_present"}
        with pytest.raises(ValueError):
            DocumentResult(**broken)

        extra = dict(payload)
        extra["fields"] = {**fields, "extra_key": "x"}
        with pytest.raises(ValueError):
            DocumentResult(**extra)

    def test_flag_types_are_enforced(self) -> None:
        fields = complete_fields("study_certificate")
        fields["seal_present"] = "true"
        with pytest.raises(ValueError):
            DocumentResult(
                document_id="x-2",
                document_type="study_certificate",
                fields=fields,
                validation={"status": "document_ok", "missing_fields": [], "sources": []},
                answers=[],
            )

    def test_results_file_version(self) -> None:
        assert ResultsFile().model_dump() == {"version": "1.0", "documents": []}
        with pytest.raises(ValueError):
            ResultsFile(version="2.0")  # type: ignore[arg-type]

    def test_final_decision_constant(self) -> None:
        assert FINAL_DECISION_RULE == "rule:1.3"


class TestConfigRegistry:
    def test_config_matches_schema_keys(self, registry) -> None:
        for document_type, keys in FIELD_KEYS.items():
            spec = registry.require(document_type)
            assert set(spec.field_keys) | set(spec.mark_keys) == set(keys), document_type

    def test_field_order_is_document_order(self, registry) -> None:
        # порядок в YAML = как рисуется карточка и как режутся зоны, не перепутать
        spec = registry.require("study_certificate")
        assert spec.field_keys[:2] == ("student_name", "university")

    def test_new_type_can_be_added_by_config(self, tmp_path) -> None:
        import yaml

        from backend.app.services.document_config import load_registry

        config = {
            "version": 1,
            "document_types": {
                "custom_type": {
                    "title": "Тестовый тип",
                    "title_anchors": ["тестовый документ"],
                    "title_pattern": "ТЕСТОВЫЙ ДОКУМЕНТ",
                    "text_rule": "rule:3.1",
                    "signature_rule": "rule:3.2",
                    "seal_rule": None,
                    "has_seal": False,
                    "fields": [
                        {"key": "student_name", "label": "ФИО студента", "type": "person", "patterns": ["ФИО"]}
                    ],
                    "marks": [
                        {"key": "signature_present", "label": "Подпись", "rule": "rule:3.2", "anchors": ["Подпись"]}
                    ],
                }
            },
        }
        path = tmp_path / "types.yaml"
        path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
        custom = load_registry(path)
        assert custom.require("custom_type").required_rules == ("rule:3.1", "rule:3.2")

    def test_detect_type_from_text(self, registry) -> None:
        text = "СПРАВКА ОБ ОБУЧЕНИИ\nФИО студента: ...\nНомер справки: ..."
        assert registry.detect_type(text)[0] == "study_certificate"
        text = "ЗАЯВЛЕНИЕ НА АКАДЕМИЧЕСКИЙ ОТПУСК\nУчебная группа: ..."
        assert registry.detect_type(text)[0] == "academic_leave_application"
        assert registry.detect_type("ничего похожего")[0] is None
