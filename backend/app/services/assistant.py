"""AI-помощник: ищем ответ локально, без всяких внешних LLM.

По кейсу нельзя никаких внешних OCR/LLM API, всё должно крутиться на ноуте или в Colab.
Так что помощник ничего не «сочиняет», а просто находит ответ в трёх локальных источниках:

1. ``configs/assistant_faq.yaml`` — вопросы про само приложение (вход, 2FA,
   проверка документа, карта…);
2. реестр вузов — факультеты и корпуса, данные с официальных сайтов;
3. Положение кейса (``rules.md``) — пункты, по которым проверяем документы.

Ищем по взвешенному пересечению основ слов (грубый стемминг по первым буквам плюс
IDF). Если ничего не нашлось, помощник так прямо и говорит и подкидывает темы — тот же
принцип честного отказа, что и в ответах по документу (п. 2.4 Положения).
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from ..config import PACKAGE_ROOT, settings
from . import universities

FAQ_PATH = Path(os.environ.get("UMIR_ASSISTANT_FAQ", str(PACKAGE_ROOT / "configs" / "assistant_faq.yaml")))

_WORD = re.compile(r"[a-zа-я0-9]+")
_STOP = set(
    "и в во на по с со к ко о об от до из за для у а но или ли не ни же бы то это как что где когда какой какая какие "
    "каком какую кто мне меня мой моя мои мы вы я он она они его её их есть быть можно нужно надо ли там тут "
    "the a an of to is are".split()
)
_LOCATION_WORDS = {"где", "корпус", "этаж", "кабинет", "аудитор", "деканат", "адрес", "наход", "часы", "телефо", "почт", "email", "контакт", "декан", "работ", "дойти", "найти"}
_RULE_WORDS = {"положен", "пункт", "справк", "заявлен", "практик", "подпис", "печат", "отпуск", "обязат", "докумен", "полей", "поле"}


def _stem(word: str) -> str:
    return word[:6] if len(word) > 6 else word


def tokens(text: str) -> list[str]:
    text = text.lower().replace("ё", "е")
    return [_stem(w) for w in _WORD.findall(text) if w not in _STOP and len(w) > 1]


@dataclass
class Entry:
    kind: str  # faq | faculty | rule
    key: str
    title: str
    answer: str
    terms: list[str]
    sources: list[dict[str, str]] = field(default_factory=list)
    university_id: str | None = None


def _faq_entries() -> list[Entry]:
    if not FAQ_PATH.is_file():
        return []
    data = yaml.safe_load(FAQ_PATH.read_text(encoding="utf-8")) or {}
    entries = []
    for item in data.get("items", []):
        questions = item.get("questions") or []
        terms = tokens(" ".join(questions) + " " + " ".join(item.get("keywords") or []))
        entries.append(
            Entry(
                kind="faq",
                key=item["id"],
                title=questions[0] if questions else item["id"],
                answer=item["answer"].strip(),
                terms=terms,
                sources=[{"label": link["label"], "route": link["route"]} for link in item.get("links") or []],
            )
        )
    return entries


def _value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def faculty_answer(university: dict[str, Any], faculty: dict[str, Any]) -> str:
    buildings = {b["id"]: b for b in university.get("buildings") or []}
    building = buildings.get(faculty.get("building_id") or "")
    parts = [f"«{faculty['name']}» ({university['short_name']})."]
    where = []
    if building:
        where.append(building["name"] + (f", {building['address']}" if building.get("address") else ""))
    if _value(faculty.get("floor")):
        where.append(f"этаж {faculty['floor']}")
    if _value(faculty.get("room")):
        where.append(f"кабинет {faculty['room']}")
    if where:
        parts.append("Деканат: " + "; ".join(where) + ".")
    else:
        parts.append("Где находится деканат, на официальном сайте мы не нашли — уточните на посту охраны или по телефону вуза.")
    if _value(faculty.get("hours")):
        parts.append(f"Часы работы: {faculty['hours']}.")
    contacts = [c for c in (_value(faculty.get("phone")), _value(faculty.get("email"))) if c]
    if contacts:
        parts.append("Контакты: " + ", ".join(contacts) + ".")
    if _value(faculty.get("dean")):
        parts.append(f"Руководитель: {faculty['dean']}.")
    return " ".join(parts)


def _faculty_entries() -> list[Entry]:
    entries = []
    for university in universities.all_universities():
        for faculty in university.get("faculties") or []:
            sources = [{"label": "Источник: " + url.split("//")[-1].split("/")[0], "url": url} for url in (faculty.get("page") and [faculty["page"]]) or (faculty.get("sources") or [])[:1]]
            sources.append({"label": "Показать на карте", "route": f"#/map?u={university['id']}&b={faculty.get('building_id') or ''}"})
            name_terms = tokens(faculty["name"] + " " + " ".join(faculty.get("aliases") or []))
            entries.append(
                Entry(
                    kind="faculty",
                    key=f"{university['id']}:{faculty['id']}",
                    title=faculty["name"],
                    answer=faculty_answer(university, faculty),
                    terms=name_terms + tokens(university["short_name"]),
                    sources=sources,
                    university_id=university["id"],
                )
            )
    return entries


def _rule_entries() -> list[Entry]:
    path = settings.rules_path
    if path is None or not path.is_file():
        return []
    from ..team_api import regulation_payload

    entries = []
    for section in regulation_payload(path):
        for clause in section["children"]:
            entries.append(
                Entry(
                    kind="rule",
                    key=clause["point"],
                    title=f"п. {clause['point']} — {section['title']}",
                    answer=f"По учебному Положению кейса, п. {clause['point']}: {clause['text']}",
                    terms=tokens(section["title"] + " " + clause["text"]),
                    sources=[{"label": f"п. {clause['point']} Положения", "route": f"#/dean?rule={clause['point']}"}],
                )
            )
    return entries


@dataclass
class Index:
    entries: list[Entry]
    idf: dict[str, float]


@lru_cache(maxsize=1)
def index() -> Index:
    entries = _faq_entries() + _faculty_entries() + _rule_entries()
    df: dict[str, int] = {}
    for entry in entries:
        for term in set(entry.terms):
            df[term] = df.get(term, 0) + 1
    total = max(len(entries), 1)
    idf = {term: math.log(1 + total / count) for term, count in df.items()}
    return Index(entries=entries, idf=idf)


def reload() -> None:
    index.cache_clear()


def _score(query: list[str], entry: Entry, idf: dict[str, float]) -> float:
    if not query:
        return 0.0
    terms = set(entry.terms)
    matched = [term for term in set(query) if term in terms]
    if not matched:
        return 0.0
    weight = sum(idf.get(term, 1.0) for term in matched)
    possible = sum(idf.get(term, 1.0) for term in set(query))
    coverage = weight / possible if possible else 0.0
    # чем короче запись, тем ценнее совпадение (а то длинные пункты выигрывали бы всегда)
    focus = len(matched) / math.sqrt(len(terms) or 1)
    return coverage * 0.75 + min(focus, 1.0) * 0.25


def suggestions() -> list[str]:
    data = yaml.safe_load(FAQ_PATH.read_text(encoding="utf-8")) if FAQ_PATH.is_file() else {}
    return list((data or {}).get("suggestions") or [])


def answer(question: str, university_id: str | None = None) -> dict[str, Any]:
    query = tokens(question)
    idx = index()
    wants_location = any(term.startswith(word) or word.startswith(term) for term in query for word in _LOCATION_WORDS)
    wants_rule = any(term.startswith(word) for term in query for word in _RULE_WORDS)
    scored = []
    for entry in idx.entries:
        score = _score(query, entry, idx.idf)
        if not score:
            continue
        if entry.kind == "faculty":
            score += 0.15 if wants_location else -0.05
            if university_id and entry.university_id == university_id:
                score += 0.05
        if entry.kind == "rule" and not wants_rule:
            score -= 0.15
        scored.append((score, entry))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    if not scored or scored[0][0] < 0.34:
        return {
            "answer": (
                "Не нашёл ответа в справке приложения, данных о вузах и Положении. "
                "Я отвечаю только по этим источникам и ничего не придумываю. Попробуйте "
                "переформулировать вопрос или выберите тему ниже; по личным вопросам лучше обратиться в деканат."
            ),
            "kind": "none",
            "confidence": round(scored[0][0], 3) if scored else 0.0,
            "sources": [],
            "suggestions": suggestions()[:4],
        }
    best_score, best = scored[0]
    related = [
        entry.title
        for score, entry in scored[1:6]
        if score > 0.3 and entry.kind == best.kind and entry.key != best.key and entry.university_id == best.university_id
    ][:3]
    return {
        "answer": best.answer,
        "kind": best.kind,
        "title": best.title,
        "confidence": round(min(best_score, 1.0), 3),
        "sources": best.sources,
        "suggestions": related,
    }
