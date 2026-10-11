"""Реестр вузов: ``backend/app/configs/universities/*.yaml``.

Один файл = один вуз: название, логотип, координаты, корпуса, картинки «холла»,
файл положения и список доступных разделов. Хочешь новый вуз — просто кидаешь
новый YAML (и, если хочется, картинки в ``frontend/public/universities/<id>/``)
— код трогать не надо, ровно как с типами документов в ``document_types.yaml``.

Справочку (факультеты, кабинеты, часы работы) берём только с официальных
сайтов вузов, у каждой записи есть ``sources``. Чего на сайте нет — оставляем пустым, не выдумываем.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from ..config import PACKAGE_ROOT, PROJECT_ROOT, settings

UNIVERSITIES_DIR = Path(os.environ.get("UMIR_UNIVERSITIES_DIR", str(PACKAGE_ROOT / "configs" / "universities")))
APP_CONFIG = Path(os.environ.get("UMIR_APP_CONFIG", str(PACKAGE_ROOT / "configs" / "app.yaml")))

SECTION_KEYS = ("dean", "faculties", "map", "history")
SECTION_STATES = ("available", "soon", "off")


class UniversityConfigError(ValueError):
    pass


def _resolve_regulation(raw: str | None) -> Path | None:
    if not raw:
        return None
    if raw == "case-rules":
        # учебное Положение из кейса (rules.md) — то самое, по которому идёт проверка документов
        return settings.rules_path
    path = Path(raw)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path if path.is_file() else None


def _validate(data: dict[str, Any], source: Path) -> dict[str, Any]:
    for key in ("id", "name", "short_name", "coords"):
        if not data.get(key):
            raise UniversityConfigError(f"{source.name}: не заполнено поле '{key}'")
    if source.stem != data["id"]:
        raise UniversityConfigError(f"{source.name}: id '{data['id']}' должен совпадать с именем файла")
    sections = {key: "off" for key in SECTION_KEYS}
    for key, value in (data.get("sections") or {}).items():
        if key not in SECTION_KEYS or value not in SECTION_STATES:
            raise UniversityConfigError(f"{source.name}: раздел '{key}: {value}' не поддерживается")
        sections[key] = value
    regulation = _resolve_regulation(data.get("regulation"))
    if sections["dean"] == "available" and regulation is None:
        # без положения документы проверять не по чему, так что честно пишем «скоро»
        sections["dean"] = "soon"
    buildings = data.get("buildings") or []
    building_ids = {b.get("id") for b in buildings}
    for building in buildings:
        if not building.get("id") or not building.get("name"):
            raise UniversityConfigError(f"{source.name}: у корпуса нет id или name")
    faculties = data.get("faculties") or []
    for faculty in faculties:
        if not faculty.get("id") or not faculty.get("name"):
            raise UniversityConfigError(f"{source.name}: у факультета нет id или name")
        if faculty.get("building_id") and faculty["building_id"] not in building_ids:
            raise UniversityConfigError(
                f"{source.name}: факультет {faculty['id']} ссылается на неизвестный корпус {faculty['building_id']}"
            )
    return {
        **data,
        "sections": sections,
        "regulation_available": regulation is not None,
        "_regulation_path": regulation,
        "buildings": buildings,
        "faculties": faculties,
    }


@lru_cache(maxsize=1)
def _load_all() -> dict[str, dict[str, Any]]:
    universities: dict[str, dict[str, Any]] = {}
    if not UNIVERSITIES_DIR.is_dir():
        return universities
    for path in sorted(UNIVERSITIES_DIR.glob("*.yaml")):
        with path.open(encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        item = _validate(data, path)
        universities[item["id"]] = item
    return universities


def reload() -> None:
    _load_all.cache_clear()
    app_config.cache_clear()


def all_universities() -> list[dict[str, Any]]:
    return list(_load_all().values())


def get(university_id: str) -> dict[str, Any] | None:
    return _load_all().get(university_id)


def regulation_path(university_id: str) -> Path | None:
    item = get(university_id)
    return item["_regulation_path"] if item else None


def summary(item: dict[str, Any]) -> dict[str, Any]:
    """Короткая карточка вуза для карты и списка."""
    return {
        "id": item["id"],
        "name": item["name"],
        "short_name": item["short_name"],
        "city": item.get("city"),
        "site": item.get("site"),
        "logo": item.get("logo"),
        "coords": item["coords"],
        "sections": item["sections"],
        "buildings": [_public_building(b) for b in item["buildings"]],
        "faculty_count": len(item["faculties"]),
    }


def _public_building(building: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": building["id"],
        "name": building["name"],
        "address": building.get("address"),
        "coords": building.get("coords"),
        "sources": building.get("sources") or [],
    }


def detail(item: dict[str, Any]) -> dict[str, Any]:
    public = {key: value for key, value in item.items() if not key.startswith("_")}
    public["buildings"] = [_public_building(b) for b in item["buildings"]]
    return public


@lru_cache(maxsize=1)
def app_config() -> dict[str, Any]:
    data: dict[str, Any] = {}
    if APP_CONFIG.is_file():
        with APP_CONFIG.open(encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    overrides = {
        "team_name": os.environ.get("UMIR_TEAM_NAME"),
        "repo_url": os.environ.get("UMIR_REPO_URL"),
        "version": os.environ.get("UMIR_APP_VERSION"),
        "default_university": os.environ.get("UMIR_DEFAULT_UNIVERSITY"),
    }
    data.update({key: value for key, value in overrides.items() if value})
    data.setdefault("year", 2026)
    data.setdefault("assistant", {})
    return data
