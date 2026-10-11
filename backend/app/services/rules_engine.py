"""Rules engine: completeness check and Положение (rules.md) citations.

The only source of rules is ``rules.md`` shipped with the case (the fictional
"Меридиан" university regulation).  Nothing else is invented here, and no real
university regulation is substituted.

Mapping used (identical to README/rules.md):

======================  ==========================  ==============  ==============
document type           text fields                 signature       seal
======================  ==========================  ==============  ==============
study_certificate       rule:3.1                    rule:3.2        rule:3.3
academic_leave_appl.    rule:4.1                    rule:4.2        —
internship_certificate  rule:5.1                    rule:5.2        rule:5.3
======================  ==========================  ==============  ==============

``rules.md`` 3.4 / 4.5 / 5.4 describe the *procedure* and are deliberately **not**
used as sources: the case requires citing the clauses that make an element
mandatory.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .document_config import DocumentTypeSpec
from .parse_fields import NOT_FOUND, labels_for_missing

STATUS_OK = "document_ok"
STATUS_MISSING = "missing_fields"

#: rules.md 1.3 — the dean's office employee takes the final decision.
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
    """Pre-check completeness against the mandatory requirement numbers.

    ``fields`` must contain every key of the type; a text key equal to
    ``не найдено`` or a ``False`` flag counts as a missing element.
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
    """Requirement numbers for a set of field/mark keys, order preserved, no dupes."""
    sources: list[str] = []
    for key in keys:
        rule = spec.rule_for_key(key)
        if rule and rule not in sources:
            sources.append(rule)
    return sources


def clause_list(sources: list[str]) -> str:
    """Render ``['rule:3.1', 'rule:3.2']`` as ``3.1, 3.2`` for human answers."""
    numbers = [source.split(":", 1)[1] for source in sources if source.startswith("rule:")]
    return ", ".join(numbers)


def pluralize_requirements(count: int) -> str:
    """Russian plural for «пункт/пункта/пунктов»."""
    if count % 10 == 1 and count % 100 != 11:
        return "пункт"
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return "пункта"
    return "пунктов"
