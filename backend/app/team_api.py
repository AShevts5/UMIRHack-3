"""Совместимость с API-контрактом команды (ветка main).

Фронтенд команды написан под схемы `app/schemas/*` и адреса `/api/analyze`,
`/api/doc-types`, `/api/files/...`, `/api/history`, `/api/regulation`. Этот модуль
оставляет те же адреса и те же поля ответа, но наполняет их настоящим результатом:
локальный OCR, парсер полей, графические отметки, движок правил и вопросы.

Заготовленных строк вида «Иванов Иван Иванович» тут нет: все значения приходят из
изображения, которое загрузил пользователь. Если значение не найдено, так и
пишем — «не найдено».

Про отличия от прежних заглушек:

* ключи полей в этом контракте другие (``full_name``, ``study_period``, ``seal``),
  поэтому есть таблица перевода в ключи кейса (``student_name``, ``study_start``
  и ``study_end``, ``signature_present`` и ``seal_present``);
* поля-периоды собираются из двух дат: по правилам кейса начало и окончание
  хранятся отдельно и извлекаются отдельно;
* ``ocr`` заполняется настоящими строками распознавания, а не ``None``;
* ``timings_ms`` — измеренные миллисекунды этапов, а не константы;
* печать заявления не показываем: по правилу 4.3 она в этот тип не входит.
"""

from __future__ import annotations

import io
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from .services.document_config import get_registry
from .services.pipeline import PipelineResult
from .services.question_engine import Intent, classify_question

NOT_FOUND = "не найдено"

TYPE_TITLES = {
    "study_certificate": "Справка об обучении",
    "academic_leave_application": "Заявление на академический отпуск",
    "internship_certificate": "Справка о прохождении практики",
}

# Синонимы id, чтобы работал и старый фронтенд с academic_leave / practice_certificate
TYPE_ALIASES = {
    "academic_leave": "academic_leave_application",
    "practice_certificate": "internship_certificate",
}

# ключ контракта -> (ключ кейса, подпись, тип поля)
FIELD_MAP: dict[str, tuple[tuple[str, str, str], ...]] = {
    "study_certificate": (
        ("full_name", "student_name", "ФИО студента", "person_name"),
        ("university", "university", "Учебное заведение", "text"),
        ("study_period", "", "Период обучения", "period"),
        ("issue_number", "document_number", "Номер справки", "text"),
        ("issue_date", "issue_date", "Дата выдачи", "date"),
        ("seal", "seal_present", "Печать", "presence"),
        ("signature", "signature_present", "Подпись", "presence"),
    ),
    "academic_leave_application": (
        ("full_name", "student_name", "ФИО студента", "person_name"),
        ("group", "group", "Группа", "text"),
        ("course", "course", "Курс", "number"),
        ("leave_reason", "leave_reason", "Основание", "text"),
        ("leave_start", "leave_start", "Дата начала отпуска", "date"),
        ("leave_end", "leave_end", "Дата окончания отпуска", "date"),
        ("application_date", "application_date", "Дата заявления", "date"),
        ("signature", "signature_present", "Подпись", "presence"),
    ),
    "internship_certificate": (
        ("full_name", "student_name", "ФИО студента", "person_name"),
        ("organization", "organization", "Организация", "text"),
        ("practice_period", "", "Период практики", "period"),
        ("supervisor", "supervisor_name", "Руководитель практики", "person_name"),
        ("grade", "grade", "Оценка", "grade"),
        ("issue_number", "document_number", "Номер справки", "text"),
        ("issue_date", "issue_date", "Дата выдачи", "date"),
        ("seal", "seal_present", "Печать", "presence"),
        ("signature", "signature_present", "Подпись", "presence"),
    ),
}

# поле-период -> из каких ключей кейса оно собирается
PERIOD_FIELDS = {
    "study_period": ("study_start", "study_end"),
    "practice_period": ("internship_start", "internship_end"),
    "leave_period": ("leave_start", "leave_end"),
}

# пункт Положения -> цитата для всплывающей подсказки в интерфейсе
RULE_QUOTES = {
    "1.3": "Окончательное решение о принятии документа и удовлетворении заявления принимает сотрудник деканата.",
    "2.3": "Для подписи и печати проверяется наличие графического элемента. Напечатанная подпись «Подпись» или «М.П.» сама по себе не подтверждает наличие отметки.",
    "2.4": "Когда ответ нельзя получить из документа и Положения, сервис сообщает об отсутствии информации и рекомендует обратиться в деканат.",
    "3.1": "Обязательные текстовые поля: ФИО студента, название вуза, направление подготовки, форма обучения, начало и окончание периода обучения, номер справки, дата выдачи.",
    "3.2": "Обязательна подпись уполномоченного сотрудника вуза.",
    "3.3": "Обязательна печать вуза.",
    "4.1": "Обязательные текстовые поля: ФИО студента, учебная группа, курс, причина отпуска, дата начала отпуска, дата окончания отпуска, дата заявления.",
    "4.2": "Обязательна подпись студента.",
    "5.1": "Обязательные текстовые поля: ФИО студента, название организации, дата начала практики, дата окончания практики, ФИО руководителя практики, оценка, номер справки, дата выдачи.",
    "5.2": "Обязательна подпись руководителя практики.",
    "5.3": "Обязательна печать организации.",
}

SECTION_TITLES = {
    "1": "Назначение и границы сервиса",
    "2": "Общие правила чтения и ответа",
    "3": "Справка об обучении",
    "4": "Заявление на академический отпуск",
    "5": "Справка о практике",
    "6": "Формулировки результатов",
}


# ---------------------------------------------------------------------------
# Хранилище последних разборов (для /api/files и /api/history)
# ---------------------------------------------------------------------------


@dataclass
class StoredAnalysis:
    request_id: str
    filename: str
    created_at: datetime
    document_type: str
    question: str
    answer_status: str
    page_png: bytes | None = None
    result: PipelineResult | None = None


class AnalysisStore:
    """Последние разборы в памяти процесса.

    На диск ничего не пишем: содержимое документов сохранять не нужно. Ключ —
    случайный request_id, старые записи вытесняются.
    """

    def __init__(self, size: int = 32) -> None:
        self._items: OrderedDict[str, StoredAnalysis] = OrderedDict()
        self._size = size

    def put(self, item: StoredAnalysis) -> None:
        self._items[item.request_id] = item
        self._items.move_to_end(item.request_id)
        while len(self._items) > self._size:
            self._items.popitem(last=False)

    def get(self, request_id: str) -> StoredAnalysis | None:
        return self._items.get(request_id)

    def history(self, limit: int) -> list[StoredAnalysis]:
        ordered = sorted(self._items.values(), key=lambda item: item.created_at, reverse=True)
        return ordered[:limit]


store = AnalysisStore()


# ---------------------------------------------------------------------------
# Типы документов
# ---------------------------------------------------------------------------


def resolve_type(document_type: str | None) -> str | None:
    """Id типа из контракта -> код кейса. Неизвестный id означает «определить авто»."""
    if not document_type:
        return None
    code = TYPE_ALIASES.get(document_type, document_type)
    return code if get_registry().get(code) is not None else None


def document_types_payload() -> list[dict]:
    """Справочник типов для интерфейса: собирается из конфига, а не из заглушки."""
    registry = get_registry()
    payload = []
    for code in registry.codes():
        mapping = FIELD_MAP.get(code)
        if not mapping:
            continue
        payload.append(
            {
                "id": code,
                "title": TYPE_TITLES.get(code, registry.require(code).title),
                "fields": [{"key": key, "label": label, "type": kind} for key, _case, label, kind in mapping],
            }
        )
    return payload


# ---------------------------------------------------------------------------
# Поля
# ---------------------------------------------------------------------------


def _period_value(fields: dict[str, str | bool], keys: tuple[str, str]) -> str:
    start, end = (fields.get(key) for key in keys)
    if not isinstance(start, str) or not isinstance(end, str) or NOT_FOUND in (start, end):
        return NOT_FOUND
    return f"{start} — {end}"


def _box_of_mark(result: PipelineResult, case_key: str) -> list[int] | None:
    verdict = result.marks.get(case_key)
    if verdict is None or not verdict.present or not verdict.box:
        return None
    x, y, width, height = verdict.box
    return [x, y, x + width, y + height]


def _box_of_field(result: PipelineResult, case_key: str) -> list[int] | None:
    field_result = result.parse.field_results.get(case_key)
    if field_result is None or not field_result.found or not field_result.box:
        return None
    x, y, width, height = field_result.box
    return [x, y, x + width, y + height]


def build_fields(result: PipelineResult) -> list[dict]:
    """Поля в контракте команды; значения — из распознанного документа."""
    items: list[dict] = []
    for team_key, case_key, label, kind in FIELD_MAP[result.document_type]:
        if kind == "period":
            source = PERIOD_FIELDS.get(team_key)
            value = _period_value(result.fields, source) if source else NOT_FOUND
            first_key = source[0] if source else None
            items.append(
                {
                    "key": team_key,
                    "label": label,
                    "value": value,
                    "found": value != NOT_FOUND,
                    "confidence": None,
                    "page": 1 if value != NOT_FOUND else None,
                    "bbox": _box_of_field(result, first_key) if first_key else None,
                }
            )
            continue

        raw = result.fields.get(case_key)
        if kind == "presence":
            found = bool(raw)
            value = "есть" if found else NOT_FOUND
            box = _box_of_mark(result, case_key)
            confidence = None
        else:
            value = raw if isinstance(raw, str) else NOT_FOUND
            found = value != NOT_FOUND
            box = _box_of_field(result, case_key)
            field_result = result.parse.field_results.get(case_key)
            confidence = round(field_result.confidence, 2) if field_result and found else None
        items.append(
            {
                "key": team_key,
                "label": label,
                "value": value,
                "found": found,
                "confidence": confidence,
                "page": 1 if found else None,
                "bbox": box,
            }
        )
    return items


# ---------------------------------------------------------------------------
# Ответ и его источники
# ---------------------------------------------------------------------------


def _rule_number(spec_code: str, case_key: str) -> str | None:
    spec = get_registry().require(spec_code)
    rule = spec.rule_for_key(case_key)
    return rule.split(":", 1)[1] if rule else None


def _team_key_for(case_key: str) -> str:
    for mapping in FIELD_MAP.values():
        for team_key, case, _label, _kind in mapping:
            if case == case_key:
                return team_key
    return case_key


def _rule_source(point: str) -> dict:
    return {"kind": "regulation", "point": point, "quote": RULE_QUOTES.get(point), "key": None}


def _field_source(case_key: str) -> dict:
    return {"kind": "field", "point": None, "quote": None, "key": _team_key_for(case_key)}


def build_answer(result: PipelineResult, question: str, request_id: str) -> dict:
    """Ответ в контракте команды, собранный нашим движком вопросов."""
    analysis = classify_question(question, result.spec)
    answer = result.single_answer(question, request_id)
    spec_code = result.document_type

    if analysis.intent is Intent.COMPLETENESS:
        if result.validation.status == "document_ok":
            rules = [_rule_number(spec_code, field.key) for field in result.spec.fields[:1]]
            sources = [_rule_source(rules[0])] if rules and rules[0] else []
            for mark in result.spec.marks:
                number = _rule_number(spec_code, mark.key)
                if number and all(item.get("point") != number for item in sources):
                    sources.append(_rule_source(number))
            return {"status": "ok", "text": answer.answer, "sources": sources}
        sources: list[dict] = []
        for key in result.validation.missing_fields:
            number = _rule_number(spec_code, key)
            if number and all(item.get("point") != number for item in sources):
                sources.append(_rule_source(number))
        for key in result.validation.missing_fields:
            sources.append(_field_source(key))
        return {"status": "missing", "text": answer.answer, "sources": sources}

    if analysis.intent is Intent.FINAL_DECISION:
        return {"status": "ok", "text": answer.answer, "sources": [_rule_source("1.3")]}

    if analysis.intent is Intent.FIELD_VALUE and analysis.field_key:
        if answer.answerable:
            number = _rule_number(spec_code, analysis.field_key)
            sources = [_rule_source(number)] if analysis.field_key.endswith("_present") and number else []
            if not sources:
                sources = [_field_source(analysis.field_key)]
            return {"status": "ok", "text": answer.answer, "sources": sources}
        return {"status": "missing", "text": answer.answer, "sources": [_field_source(analysis.field_key)]}

    return {"status": "no_info", "text": answer.answer, "sources": [_rule_source("2.4")]}


# ---------------------------------------------------------------------------
# Полный ответ /api/analyze
# ---------------------------------------------------------------------------


def page_png(result: PipelineResult, max_side: int = 1600) -> bytes | None:
    """Страница, по которой работал OCR, — для превью в интерфейсе."""
    if result.image_rgb is None:
        return None
    image = Image.fromarray(result.image_rgb)
    long_side = max(image.size)
    if long_side > max_side:
        factor = max_side / long_side
        image = image.resize((max(1, int(image.width * factor)), max(1, int(image.height * factor))))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def build_analyze_response(
    result: PipelineResult,
    question: str,
    request_id: str,
    include_page: bool = True,
) -> tuple[dict, bytes | None]:
    """Ответ контракта команды плюс PNG страницы (его отдаёт /api/files)."""
    page = page_png(result) if include_page else None
    lines = [
        {
            "text": line.text,
            "bbox": [line.left, line.top, line.right, line.bottom],
            "conf": round(min(1.0, (sum(word.conf for word in line.words) / max(len(line.words), 1)) / 100.0), 2),
        }
        for line in result.ocr.lines
    ]
    payload = {
        "request_id": request_id,
        "doc_type": {
            "id": result.document_type,
            "title": TYPE_TITLES.get(result.document_type, result.spec.title),
            "auto": result.document_type_source == "auto",
            "confidence": round(result.document_type_confidence, 2)
            if result.document_type_source == "auto"
            else None,
        },
        "image": (
            {
                "url": f"/api/files/{request_id}/page-1",
                "width": result.image_size[0],
                "height": result.image_size[1],
                "rotation_deg": round(result.rotation_deg, 1),
            }
            if page is not None
            else None
        ),
        "fields": build_fields(result),
        "answer": build_answer(result, question, request_id),
        "ocr": {"engine": result.ocr.engine, "lines": lines},
        "timings_ms": {key: round(value, 1) for key, value in result.timings.items()},
    }
    return payload, page


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


# ---------------------------------------------------------------------------
# Положение для раздела /api/regulation
# ---------------------------------------------------------------------------


def regulation_payload(rules_path: Path) -> list[dict]:
    """Разбираем rules.md на разделы и пункты — то, что показывает интерфейс."""
    if not rules_path.is_file():
        return []
    sections: list[dict] = []
    current: dict | None = None
    for raw in rules_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("## "):
            head = line[3:].strip()
            number = head.split(".", 1)[0].strip()
            current = {
                "point": number,
                "title": SECTION_TITLES.get(number, head),
                "text": "",
                "children": [],
            }
            sections.append(current)
            continue
        if current is None or not line:
            continue
        # строка пункта вида «3.1. Обязательные текстовые поля: ...»
        parts = line.split(".", 2)
        looks_like_point = len(parts) >= 3 and parts[0].isdigit() and parts[1].strip().isdigit()
        if looks_like_point:
            point = f"{parts[0]}.{parts[1].strip()}"
            current["children"].append(
                {"point": point, "title": None, "text": parts[2].strip(), "children": []}
            )
        elif current["children"]:
            current["children"][-1]["text"] = f"{current['children'][-1]['text']} {line}".strip()
        else:
            current["text"] = f"{current['text']} {line}".strip()
    return sections


def history_payload(items: list[StoredAnalysis]) -> list[dict]:
    return [
        {
            "request_id": item.request_id,
            "created_at": item.created_at.astimezone(timezone.utc).isoformat(),
            "filename": item.filename,
            "doc_type_title": TYPE_TITLES.get(item.document_type, item.document_type),
            "question": item.question,
            "status": item.answer_status,
        }
        for item in items
    ]
