"""Question engine: intent classification, answer text and structured value.

Four question classes required by the case (README «Формат results.json»):

1. **completeness** — "Содержит ли документ все обязательные сведения?" →
   ``answerable=true``, ``value=validation.status``, ``sources=validation.sources``.
2. **field value** — "Укажи значение поля «Дата выдачи»." → ``answerable=true``,
   ``value=fields[key]``, ``sources=["field:key"]``; if the field is missing →
   ``answerable=false``, ``value=null``, ``sources=[]``.
3. **final decision** — "Кто принимает окончательное решение?" →
   ``value="сотрудник деканата"``, ``sources=["rule:1.3"]``.
4. **unknown** — nothing in the document or the Положение answers it →
   ``answerable=false``, ``value=null``, ``sources=[]``.

Matching is done through a synonym table per field and per intent, so the engine
does not depend on the exact wording used in ``questions.json``.  An unrecognised
question never produces an invented value: it yields the honest
«Не могу определить ответ по документу и Положению» answer (rules.md 2.4, 6.3).

No language model is used: the whole path is deterministic and explainable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from ..utils.dates import human_date
from ..utils.textnorm import norm_text
from ..schemas import Answer
from .document_config import DocumentTypeSpec, FieldSpec
from .parse_fields import NOT_FOUND, ParseResult
from .rules_engine import (
    FINAL_DECISION_RULE,
    FINAL_DECISION_VALUE,
    STATUS_OK,
    ValidationResult,
    clause_list,
    pluralize_requirements,
)

NO_INFO_ANSWER = "В документе и в Положении нет информации для ответа на этот вопрос. Обратитесь в деканат."
UNKNOWN_ANSWER = (
    "Не могу определить ответ по документу и Положению. "
    "Уточните вопрос или обратитесь в деканат."
)


class Intent(str, Enum):
    COMPLETENESS = "completeness"
    FINAL_DECISION = "final_decision"
    FIELD_VALUE = "field_value"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Synonym tables
# ---------------------------------------------------------------------------

#: Intent markers.  Order matters: the first matching group wins.
COMPLETENESS_MARKERS: tuple[str, ...] = (
    "все обязательные сведения",
    "все ли обязательные",
    "обязательные сведения",
    "обязательные поля",
    "обязательные элементы",
    "обязательные реквизиты",
    "комплектн",
    "комплектен",
    "все ли поля",
    "все поля заполнены",
    "все ли на месте",
    "все ли необходимые",
    "достаточно ли сведений",
    "хватает ли сведений",
    "не хватает",
    "чего не хватает",
    "что отсутствует",
    "какие поля отсутствуют",
    "каких сведений не хватает",
    "полный ли документ",
    "документ заполнен полностью",
    "проверить документ",
    "проверь документ",
    "проверка документа",
    "проверь справку",
    "проверить справку",
    "проверь заявление",
    "проверить заявление",
    "в порядке ли документ",
    "документ в порядке",
    "можно ли принять документ",
    "статус проверки",
    "статус документа",
    "соответствует ли документ",
)

FINAL_DECISION_MARKERS: tuple[str, ...] = (
    "кто принимает окончательное решение",
    "кто принимает решение",
    "кто принимает",
    "окончательное решение",
    "кто решает",
    "кто подписывает решение",
    "кто утверждает",
    "кто выносит решение",
    "кому передается решение",
    "кто принимает документ",
    "who takes the final decision",
    "who decides",
)

#: Field synonyms.  Only phrases that *name* a field are listed: single adjectives
#: ("очная", "заочная") are deliberately absent, because they appear inside
#: questions about topics the Положение does not define (e.g. "стоимость
#: обучения"), which must be answered with an honest refusal.
FIELD_SYNONYMS: dict[str, tuple[str, ...]] = {
    "student_name": ("фио студента", "фио обучающегося", "фио", "студент", "обучающийся"),
    "university": ("название вуза", "наименование вуза", "вуз", "университет", "учебное заведение"),
    "program": ("направление подготовки", "направление", "специальность", "программа обучения"),
    "study_form": ("форма обучения", "форма обучения студента"),
    "study_start": ("начало периода обучения", "начало обучения", "дата начала обучения"),
    "study_end": ("окончание периода обучения", "окончание обучения", "дата окончания обучения"),
    "document_number": ("номер справки", "номер документа", "номер бланка", "номер"),
    "issue_date": ("дата выдачи", "дата выдачи справки", "когда выдана", "выдана"),
    "group": ("учебная группа", "группа", "номер группы"),
    "course": ("курс", "курс обучения", "на каком курсе"),
    "leave_reason": ("причина отпуска", "причина", "основание"),
    "leave_start": ("дата начала отпуска", "начало отпуска"),
    "leave_end": ("дата окончания отпуска", "окончание отпуска", "конец отпуска"),
    "application_date": ("дата заявления", "дата подачи заявления", "когда подано заявление"),
    "organization": ("название организации", "организация", "место практики", "предприятие"),
    "internship_start": ("дата начала практики", "начало практики"),
    "internship_end": ("дата окончания практики", "окончание практики", "конец практики"),
    "supervisor_name": ("фио руководителя практики", "руководитель практики", "руководитель"),
    "grade": ("оценка", "отметка", "результат практики", "оценка за практику"),
    "signature_present": ("подпись", "наличие подписи", "есть ли подпись"),
    "seal_present": ("печать", "наличие печати", "есть ли печать", "м.п."),
}

#: A question word that does not name a field is a request for a value.
VALUE_MARKERS: tuple[str, ...] = (
    "укажи",
    "укажите",
    "значение поля",
    "значение",
    "каково",
    "какова",
    "каков",
    "какой",
    "какая",
    "какие",
    "чему равно",
    "найди",
    "покажи",
    "выведи",
    "what is",
    "value of",
)

#: No-data topics: ``phrase fragment -> (field keys that must not win, display label)``.
#:
#: All keys are matched as substrings of the normalized question, so stems are used
#: where Russian inflection would otherwise need a dozen spellings.  The display
#: label is what the user sees in the answer, so it is always a full phrase.
#: The field keys listed for a topic are the ones whose *name occurs inside* the
#: topic phrase (e.g. «стоимость обучения» contains «обучение» but is not the
#: «начало периода обучения» field), and they must not win over the refusal.
NO_DATA_TOPICS: dict[str, tuple[tuple[str, ...], str]] = {
    "стоимость обучения": (("study_form", "study_start", "study_end"), "стоимость обучения"),
    "цена обучения": (("study_form", "study_start", "study_end"), "стоимость обучения"),
    "плата за обучение": (("study_form", "study_start", "study_end"), "стоимость обучения"),
    "стоимость": (("study_form", "study_start", "study_end"), "стоимость обучения"),
    "сколько стоит обучение": (("study_form", "study_start", "study_end"), "стоимость обучения"),
    "стоит обучение": (("study_form", "study_start", "study_end"), "стоимость обучения"),
    "стипенди": ((), "размер стипендии"),
    "срок рассмотрения": ((), "срок рассмотрения документа"),
    "сроки рассмотрения": ((), "срок рассмотрения документа"),
    "сколько рассматривается": ((), "срок рассмотрения документа"),
    "сколько рассматривают": ((), "срок рассмотрения документа"),
    "телефон": ((), "телефон деканата"),
    "кабинет": ((), "кабинет приёма оригиналов"),
    "аудитори": ((), "кабинет приёма оригиналов"),
    "адрес деканата": ((), "адрес деканата"),
    "почт": ((), "электронная почта деканата"),
    "перевод на другой курс": (("course",), "курс перевода"),
    "курс перевода": (("course",), "курс перевода"),
    "перевестись": (("course",), "перевод на другой курс"),
    "для перевода": (("course",), "перевод на другой курс"),
    "перевода на": (("course",), "перевод на другой курс"),
    "перевод на": (("course",), "перевод на другой курс"),
    "перевестись на": (("course",), "перевод на другой курс"),
    "перезачет": ((), "перезачёт дисциплин"),
    "перезачёт": ((), "перезачёт дисциплин"),
    "задолженност": ((), "академические задолженности"),
    "выплат": ((), "сохранение выплат"),
    "максимальная длительность отпуска": (("leave_start", "leave_end"), "максимальная длительность отпуска"),
    "порядок выставления оценки": (("grade",), "порядок выставления оценки"),
    "зачёт практики": ((), "зачёт практики"),
    "зачет практики": ((), "зачёт практики"),
    "требования к содержанию отчета": ((), "требования к содержанию отчёта"),
    "приказ о переводе": ((), "приказ о переводе"),
    "подлинность": (("signature_present", "seal_present"), "подлинность подписи и печати"),
    "юридическая сила": ((), "юридическая сила документа"),
    "срок давности": ((), "срок давности документа"),
}

#: Tokens that must not, on their own, be read as a field name: Russian case
#: endings make «студенту»/«документа» look like «студент»/«документ».  A single
#: token match only counts together with an explicit value marker, see
#: :func:`_value_requested`.
STOP_TOKENS = frozenset(
    {
        "студенту",
        "студентом",
        "студентам",
        "документа",
        "документом",
        "документы",
        "деканат",
        "деканата",
        "деканате",
        "деканатом",
    }
)

FIELD_SOURCE_PREFIX = "field:"


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


@dataclass
class QuestionAnalysis:
    intent: Intent
    field_key: str | None = None
    field_title: str = ""
    matched_term: str = ""
    topic: str | None = None
    score: float = 0.0


_FILLER_RE = re.compile(
    r"(укажи(?:те)?|покажи(?:те)?|выведи(?:те)?|найди(?:те)?|значение|поля|поле|"
    r"каков[аоы]?|какой|какая|какие|чему|равно|пожалуйста|мне|нужно|хочу|узнать|"
    r"скажи(?:те)?|подскажи(?:те)?|в|на|по|для|и|или|the|is|of|value|what|please)"
)


def _clean_question(question: str) -> str:
    text = norm_text(question)
    text = text.replace("«", " ").replace("»", " ").replace('"', " ")
    text = re.sub(r"[?!.,;:()\[\]]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _strip_fillers(text: str) -> str:
    words = [word for word in text.split() if not _FILLER_RE.fullmatch(word)]
    return " ".join(words)


def _collect_field_candidates(
    text: str, spec: DocumentTypeSpec, penalty: float = 0.0
) -> tuple[float, str, str] | None:
    """Best (score, key, matched term) for a field/mark mentioned in ``text``."""
    best: tuple[float, str, str] | None = None
    for field_spec in spec.fields:
        for term in _synonyms_for(field_spec):
            normalized = norm_text(term)
            if not normalized or normalized not in text:
                continue
            if normalized in STOP_TOKENS:
                continue
            score = len(normalized.split())
            if normalized == norm_text(field_spec.label):
                score += 2.0
            score -= penalty
            if best is None or score > best[0]:
                best = (score, field_spec.key, term)
    for key, terms in FIELD_SYNONYMS.items():
        if spec.field(key) is None and spec.mark(key) is None:
            continue
        for term in terms:
            normalized = norm_text(term)
            if not normalized or normalized not in text:
                continue
            if normalized in STOP_TOKENS:
                continue
            score = len(normalized.split()) - 0.2  # generic synonyms rank lower
            score -= penalty
            if best is None or score > best[0]:
                best = (score, key, term)
    return best


def _value_requested(clean: str) -> bool:
    """True when the question explicitly asks for a value/state of something."""
    return any(norm_text(marker) in clean for marker in VALUE_MARKERS)


def _no_data_topic(clean: str, candidate_key: str | None) -> str | None:
    """Display label of the no-data topic in the question, if any.

    ``clean`` must already be normalized (``norm_text``); topics are compared the
    same way so that «ё»/quotes/spacing differences cannot hide a match.
    """
    for fragment, (shielded_keys, label) in NO_DATA_TOPICS.items():
        if norm_text(fragment) not in clean:
            continue
        if shielded_keys and candidate_key is not None and candidate_key not in shielded_keys:
            # The phrase contains a field name, but not *this* field: the question is
            # about the field, not about the undefined topic.  Keep looking.
            continue
        return label
    return None


#: A match of this length or longer names a real field even without a value marker
#: ("Учебная группа?" is still a field question).
STRONG_MATCH_TOKENS = 1.5


def classify_question(question: str, spec: DocumentTypeSpec) -> QuestionAnalysis:
    """Deterministically classify a Russian question about a document."""
    clean = _clean_question(question)
    if not clean:
        return QuestionAnalysis(intent=Intent.UNKNOWN)

    # 1) completeness (checked first: these phrasings contain "сведения")
    for marker in COMPLETENESS_MARKERS:
        if norm_text(marker) in clean:
            return QuestionAnalysis(intent=Intent.COMPLETENESS, matched_term=marker, score=1.0)

    # 2) final decision
    for marker in FINAL_DECISION_MARKERS:
        if norm_text(marker) in clean:
            return QuestionAnalysis(intent=Intent.FINAL_DECISION, matched_term=marker, score=1.0)

    # 3) field / mark mention
    candidate = _collect_field_candidates(clean, spec)
    if candidate is None:
        stripped = _strip_fillers(clean)
        if stripped and stripped != clean:
            candidate = _collect_field_candidates(stripped, spec, penalty=0.5)

    if candidate is not None:
        score, key, term = candidate
        strong = score >= STRONG_MATCH_TOKENS
        # A single short token ("студенту", "документа") is not a field name unless
        # the question really asks for a value.
        if strong or _value_requested(clean):
            # A topic the Положение does not define always wins over a field name
            # that merely occurs inside the phrase ("стоимость обучения").
            topic = _no_data_topic(clean, key)
            if topic is None:
                field_spec = spec.field(key)
                mark_spec = spec.mark(key)
                title = field_spec.label if field_spec else (mark_spec.label if mark_spec else key)
                return QuestionAnalysis(
                    intent=Intent.FIELD_VALUE,
                    field_key=key,
                    field_title=title,
                    matched_term=term,
                    score=score,
                )
            return QuestionAnalysis(intent=Intent.UNKNOWN, topic=topic, matched_term=topic, score=1.0)

    # 4) known "no data" topics documented in the Положение
    topic = _no_data_topic(clean, None)
    if topic is not None:
        return QuestionAnalysis(intent=Intent.UNKNOWN, topic=topic, matched_term=topic, score=1.0)

    return QuestionAnalysis(intent=Intent.UNKNOWN, matched_term="")


def _synonyms_for(field_spec: FieldSpec) -> tuple[str, ...]:
    configured = tuple(field_spec.synonyms)
    extra = FIELD_SYNONYMS.get(field_spec.key, ())
    seen: list[str] = [field_spec.label, *configured]
    for term in extra:
        if term not in seen:
            seen.append(term)
    return tuple(seen)


# ---------------------------------------------------------------------------
# Answer building
# ---------------------------------------------------------------------------


def _field_answer_text(spec: DocumentTypeSpec, field_spec: FieldSpec, value: str | None) -> str:
    if value is None:
        return f"Поле «{field_spec.label}» не найдено в документе. Обратитесь в деканат."
    if field_spec.is_date:
        return f"{field_spec.label}: {value} ({human_date(value)})."
    if field_spec.type == "course":
        return f"{field_spec.label}: {value}."
    return f"{field_spec.label}: {value}."


def _mark_answer_text(spec: DocumentTypeSpec, mark_key: str, present: bool) -> tuple[str, str | None]:
    mark = spec.mark(mark_key)
    label = mark.label if mark else mark_key
    if present:
        return f"В документе обнаружена графическая отметка «{label}» (пункт {mark.rule.split(':')[1]} Положения).", "есть"
    return f"Графическая отметка «{label}» в документе не обнаружена.", None


def build_answer(
    question_id: str,
    question: str,
    spec: DocumentTypeSpec,
    fields: dict[str, str | bool],
    validation: ValidationResult,
    parse_result: ParseResult | None = None,
) -> Answer:
    """Produce the answer record for one question (contract-shaped)."""
    analysis = classify_question(question, spec)

    if analysis.intent is Intent.COMPLETENESS:
        return _completeness_answer(question_id, spec, validation)

    if analysis.intent is Intent.FINAL_DECISION:
        return Answer(
            question_id=question_id,
            answerable=True,
            value=FINAL_DECISION_VALUE,
            answer=(
                "Окончательное решение принимает сотрудник деканата, пункт "
                f"{FINAL_DECISION_RULE.split(':')[1]} Положения. Сервис даёт только "
                "предварительную проверку."
            ),
            sources=[FINAL_DECISION_RULE],
        )

    if analysis.intent is Intent.FIELD_VALUE and analysis.field_key:
        key = analysis.field_key
        mark_spec = spec.mark(key)
        if mark_spec is not None:
            present = bool(fields.get(key, False))
            text, value = _mark_answer_text(spec, key, present)
            if not present:
                return Answer(
                    question_id=question_id,
                    answerable=False,
                    value=None,
                    answer=f"{text} {NO_INFO_ANSWER}".strip(),
                    sources=[],
                )
            return Answer(
                question_id=question_id, answerable=True, value=value, answer=text, sources=[mark_spec.rule]
            )
        field_spec = spec.field(key)
        if field_spec is not None:
            raw = fields.get(key)
            value = raw if isinstance(raw, str) else None
            if value is None or value == NOT_FOUND:
                return Answer(
                    question_id=question_id,
                    answerable=False,
                    value=None,
                    answer=_field_answer_text(spec, field_spec, None),
                    sources=[],
                )
            return Answer(
                question_id=question_id,
                answerable=True,
                value=value,
                answer=_field_answer_text(spec, field_spec, value),
                sources=[f"{FIELD_SOURCE_PREFIX}{key}"],
            )

    # 4) unknown / no data
    if analysis.topic:
        return Answer(
            question_id=question_id,
            answerable=False,
            value=None,
            answer=(
                f"В документе и в Положении нет сведений по теме «{analysis.topic}». "
                "Положение такие условия не устанавливает. Обратитесь в деканат."
            ),
            sources=[],
        )
    return Answer(
        question_id=question_id,
        answerable=False,
        value=None,
        answer=UNKNOWN_ANSWER,
        sources=[],
    )


def _completeness_answer(question_id: str, spec: DocumentTypeSpec, validation: ValidationResult) -> Answer:
    numbers = clause_list(validation.sources)
    if validation.status == STATUS_OK:
        answer = (
            f"Документ содержит все обязательные сведения по пунктам {numbers} Положения. "
            "Окончательное решение примет сотрудник деканата."
        )
        return Answer(
            question_id=question_id,
            answerable=True,
            value=validation.status,
            answer=answer,
            sources=list(validation.sources),
        )
    labels = validation.missing_labels or validation.missing_fields
    joined = ", ".join(labels)
    # «Не хватает сведения: X» / «Не хватает сведений: X, Y» — обе формы корректны.
    word = "сведения" if len(labels) == 1 else "сведений"
    required = pluralize_requirements(len(validation.sources))
    answer = (
        f"Не хватает {word}: {joined}. "
        f"Основание: {required} {numbers} Положения. "
        "Это предварительная проверка, решение принимает сотрудник деканата."
    )
    return Answer(
        question_id=question_id,
        answerable=True,
        value=validation.status,
        answer=answer,
        sources=list(validation.sources),
    )


def answer_document(
    spec: DocumentTypeSpec,
    fields: dict[str, str | bool],
    validation: ValidationResult,
    questions: list[tuple[str, str]],
    parse_result: ParseResult | None = None,
) -> list[Answer]:
    """Answer every question of one document, preserving the manifest order."""
    return [
        build_answer(question_id, text, spec, fields, validation, parse_result)
        for question_id, text in questions
    ]


def default_completeness_question() -> str:
    """The standard question behind the «Проверить документ» button (rules.md 6.5)."""
    return "Содержит ли документ все обязательные сведения по Положению?"
