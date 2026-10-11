"""Проверка комплектности и ссылки на пункты Положения (rules.md).

Правила берём только из rules.md — это вымышленное Положение университета
«Меридиан» из пакета кейса. Ничего своего не придумываем и правила реальных вузов
не подставляем.

Какие пункты за что отвечают:

========================  ====================  ============  ========
тип документа             текстовые поля        подпись       печать
========================  ====================  ============  ========
study_certificate         rule:3.1              rule:3.2      rule:3.3
academic_leave_appl.      rule:4.1              rule:4.2      —
internship_certificate    rule:5.1              rule:5.2      rule:5.3
========================  ====================  ============  ========

Пункты 3.4 / 4.5 / 5.4 в источниках не используем: они про порядок проверки, а
кейс просит ссылаться на требования, которые делают элемент обязательным.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .document_config import DocumentTypeSpec
from .parse_fields import NOT_FOUND, labels_for_missing

STATUS_OK = "document_ok"
STATUS_MISSING = "missing_fields"

#: rules.md 1.3 — окончательное решение принимает сотрудник деканата.
FINAL_DECISION_RULE = "rule:1.3"
FINAL_DECISION_VALUE = "сотрудник деканата"


@dataclass
class ValidationResult:
    status: str
    missing_fields: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    missing_labels: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "missing_fields": list(self.missing_fields),
            "sources": list(self.sources),
        }


def validate(
    spec: DocumentTypeSpec,
    fields: dict[str, str | bool],
) -> ValidationResult:
    """Предварительно проверяем комплектность по обязательным пунктам.

    В ``fields`` должны быть все ключи типа; текстовый ключ со значением
    ``не найдено`` и флаг ``False`` считаются пропущенным элементом.
    """
    missing: list[str] = []
    for field_spec in spec.fields:
        value = fields.get(field_spec.key)
        if not isinstance(value, str) or value.strip() == "" or value == NOT_FOUND:
            missing.append(field_spec.key)
    for mark_spec in spec.marks:
        if not bool(fields.get(mark_spec.key, False)):
            missing.append(mark_spec.key)

    sources: list[str] = []
    for key in missing:
        rule = spec.rule_for_key(key)
        if rule and rule not in sources:
            sources.append(rule)

    if missing:
        return ValidationResult(
            status=STATUS_MISSING,
            missing_fields=missing,
            sources=sources,
            missing_labels=labels_for_missing(spec, missing),
        )
    return ValidationResult(
        status=STATUS_OK,
        missing_fields=[],
        sources=list(spec.required_rules),
        missing_labels=[],
    )


def sources_for_keys(spec: DocumentTypeSpec, keys: list[str]) -> list[str]:
    """Номера пунктов для набора ключей: порядок сохраняем, повторов не будет."""
    sources: list[str] = []
    for key in keys:
        rule = spec.rule_for_key(key)
        if rule and rule not in sources:
            sources.append(rule)
    return sources


def clause_list(sources: list[str]) -> str:
    """Превращаем ``['rule:3.1', 'rule:3.2']`` в ``3.1, 3.2`` для текста ответа."""
    numbers = [source.split(":", 1)[1] for source in sources if source.startswith("rule:")]
    return ", ".join(numbers)


def pluralize_requirements(count: int) -> str:
    """Русское склонение: «пункт», «пункта» или «пунктов»."""
    if count % 10 == 1 and count % 100 != 11:
        return "пункт"
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return "пункта"
    return "пунктов"
