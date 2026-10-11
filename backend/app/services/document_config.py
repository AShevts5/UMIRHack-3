"""Описания типов документов — читаем их из ``configs/document_types.yaml``.

Типы живут в YAML (подписи полей, якоря, номера пунктов) — поэтому бонус
«новый тип документа через конфиг» у нас честный: чтобы добавить тип, хватит
дописать блок в YAML, а парсер, движок правил и движок вопросов трогать не надо.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from ..config import settings
from ..utils.textnorm import norm_label, norm_text

RULE_RE = re.compile(r"^rule:\d+\.\d+$")


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    type: str  # text | date | course | person | organization | document_number | code | grade
    patterns: tuple[str, ...]
    synonyms: tuple[str, ...]

    @property
    def is_date(self) -> bool:
        return self.type == "date"

    @property
    def is_text(self) -> bool:
        return self.type != "date"


@dataclass(frozen=True)
class MarkSpec:
    key: str
    label: str
    rule: str
    anchors: tuple[str, ...]
    failure_hint: str


@dataclass(frozen=True)
class DocumentTypeSpec:
    code: str
    title: str
    title_anchors: tuple[str, ...]
    title_pattern: str
    text_rule: str
    signature_rule: str
    seal_rule: str | None
    fields: tuple[FieldSpec, ...]
    marks: tuple[MarkSpec, ...]
    signature_anchor: str = "подпись"
    has_seal: bool = False

    @property
    def field_keys(self) -> tuple[str, ...]:
        return tuple(field.key for field in self.fields)

    @property
    def text_keys(self) -> tuple[str, ...]:
        return tuple(field.key for field in self.fields)

    @property
    def mark_keys(self) -> tuple[str, ...]:
        return tuple(mark.key for mark in self.marks)

    @property
    def required_rules(self) -> tuple[str, ...]:
        """Все обязательные пункты для этого типа (rules.md 3.1–3.3 ...), без повторов."""
        rules = [self.text_rule]
        for mark in self.marks:
            if mark.rule not in rules:
                rules.append(mark.rule)
        return tuple(rules)

    def field(self, key: str) -> FieldSpec | None:
        for spec in self.fields:
            if spec.key == key:
                return spec
        return None

    def mark(self, key: str) -> MarkSpec | None:
        for spec in self.marks:
            if spec.key == key:
                return spec
        return None

    def rule_for_field(self, key: str) -> str:
        """Смотрим, какой пункт Положения делает это поле обязательным."""
        return self.text_rule

    def rule_for_key(self, key: str) -> str | None:
        """Отдаём пункт по ключу поля или отметки (``signature_present``, ``seal_present``)."""
        if self.field(key) is not None:
            return self.text_rule
        mark = self.mark(key)
        return mark.rule if mark else None


@dataclass(frozen=True)
class DocumentTypeRegistry:
    specs: dict[str, DocumentTypeSpec]

    def get(self, code: str) -> DocumentTypeSpec | None:
        return self.specs.get(code)

    def require(self, code: str) -> DocumentTypeSpec:
        spec = self.specs.get(code)
        if spec is None:
            known = ", ".join(sorted(self.specs))
            raise KeyError(f"Неизвестный тип документа '{code}'. Доступны: {known}")
        return spec

    def codes(self) -> list[str]:
        return sorted(self.specs)

    def titles(self) -> dict[str, str]:
        return {code: spec.title for code, spec in self.specs.items()}

    def detect_type(self, text: str) -> tuple[str | None, float]:
        """Бонус: пробуем угадать тип документа по распознанному тексту.

        Отдаём ``(code, score)``; ``code`` будет ``None``, если уверенно ничего не
        совпало. Если тип выбрали руками — он всегда главнее этой эвристики.
        """
        normalized = norm_text(text)
        best_code, best_score = None, 0.0
        for code, spec in self.specs.items():
            score = 0.0
            for anchor in spec.title_anchors:
                if norm_text(anchor) in normalized:
                    score = max(score, 1.0)
            header = re.search(spec.title_pattern, text, re.IGNORECASE)
            if header:
                score = max(score, 1.0)
            label_hits = sum(
                1
                for field in spec.fields
                if any(norm_label(pattern.replace("\\s+", " ")) in normalized for pattern in field.patterns)
            )
            score = max(score, min(0.9, label_hits / max(len(spec.fields), 1)))
            if score > best_score:
                best_code, best_score = code, score
        return (best_code, round(best_score, 3)) if best_score >= 0.45 else (None, round(best_score, 3))


def _load_yaml(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"Не найден конфиг типов документов: {path}")
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _parse_spec(code: str, raw: dict) -> DocumentTypeSpec:
    fields = tuple(
        FieldSpec(
            key=item["key"],
            label=item.get("label", item["key"]),
            type=item.get("type", "text"),
            patterns=tuple(item.get("patterns") or []),
            synonyms=tuple(item.get("synonyms") or []),
        )
        for item in raw.get("fields", [])
    )
    marks = tuple(
        MarkSpec(
            key=item["key"],
            label=item.get("label", item["key"]),
            rule=item["rule"],
            anchors=tuple(item.get("anchors") or []),
            failure_hint=item.get("failure_hint", item.get("label", item["key"])),
        )
        for item in raw.get("marks", [])
    )
    seal_rule = raw.get("seal_rule")
    if seal_rule is not None and not RULE_RE.match(str(seal_rule)):
        raise ValueError(f"{code}: seal_rule должен иметь вид rule:N.M")
    for field in fields:
        if not field.patterns:
            raise ValueError(f"{code}.{field.key}: нужен хотя бы один pattern")
    return DocumentTypeSpec(
        code=code,
        title=raw.get("title", code),
        title_anchors=tuple(raw.get("title_anchors") or []),
        title_pattern=raw.get("title_pattern", ""),
        text_rule=raw["text_rule"],
        signature_rule=raw["signature_rule"],
        seal_rule=seal_rule,
        fields=fields,
        marks=marks,
        signature_anchor=str(raw.get("signature_anchor", "подпись")),
        has_seal=bool(raw.get("has_seal", seal_rule is not None)),
    )


def _base_types(config_path: Path, payload: dict, depth: int = 0) -> dict:
    """Подтягиваем типы из файла, указанного в ``extends`` (путь относительно этого конфига).

    Так пример нового типа хранит только свой блок, а три типа кейса берёт из
    основного конфига — копипастить их не нужно.
    """
    base = payload.get("extends")
    if not base:
        return {}
    if depth > 3:
        raise ValueError(f"{config_path}: слишком длинная цепочка extends")
    base_path = (config_path.parent / str(base)).resolve()
    base_payload = _load_yaml(base_path)
    types = dict(_base_types(base_path, base_payload, depth + 1))
    types.update(base_payload.get("document_types") or {})
    return types


def load_registry(path: str | Path | None = None) -> DocumentTypeRegistry:
    config_path = Path(path) if path else settings.document_types_config
    payload = _load_yaml(config_path)
    types = dict(_base_types(config_path, payload))
    types.update(payload.get("document_types") or {})
    if not types:
        raise ValueError(f"{config_path}: секция document_types пуста")
    specs = {code: _parse_spec(code, raw) for code, raw in types.items()}
    for code, spec in specs.items():
        if spec.text_rule not in {field_rule for field_rule in [spec.text_rule]}:
            raise ValueError(f"{code}: некорректный text_rule")
        if not RULE_RE.match(spec.text_rule) or not RULE_RE.match(spec.signature_rule):
            raise ValueError(f"{code}: правила должны иметь вид rule:N.M")
        if spec.has_seal and spec.seal_rule is None:
            raise ValueError(f"{code}: has_seal=true требует seal_rule")
    return DocumentTypeRegistry(specs=specs)


@lru_cache(maxsize=4)
def _cached_registry(path_str: str) -> DocumentTypeRegistry:
    return load_registry(path_str)


def get_registry(path: str | Path | None = None) -> DocumentTypeRegistry:
    """Реестр с кэшем: конфиг читаем один раз на процесс, а не на каждый вызов."""
    config_path = Path(path) if path else settings.document_types_config
    return _cached_registry(str(config_path))
