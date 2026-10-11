"""Всякая мелочь из недельного плана: демо-образцы, четвёртый тип через конфиг,
коды восстановления 2FA, демо-аккаунт, Telegram-бот, проверка весов OCR."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.config import PROJECT_ROOT
from backend.app.services import auth
from backend.app.services.document_config import get_registry, load_registry

from .conftest import requires_ocr

DEMO = PROJECT_ROOT / "frontend" / "public" / "demo"
EXAMPLE = PROJECT_ROOT / "examples" / "new-document-type"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("UMIR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UMIR_JWT_SECRET", "test-secret-" + "x" * 32)
    monkeypatch.delenv("UMIR_DEMO_ACCOUNT", raising=False)
    monkeypatch.setattr(auth, "store", auth.AuthStore(tmp_path / "users.sqlite3"))
    monkeypatch.setattr(auth, "throttle", auth._Throttle())
    from backend.app.main import app

    return TestClient(app)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _register(client, login="student1", password="password123"):
    response = client.post("/api/auth/register", json={"login": login, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


# --- четвёртый тип, заводим через конфиг ------------------------------------------------
def test_example_config_extends_case_types_without_changing_them():
    case = get_registry()
    assert set(case.codes()) == {"study_certificate", "academic_leave_application", "internship_certificate"}
    extended = load_registry(EXAMPLE / "document_types.yaml")
    assert set(extended.codes()) == set(case.codes()) | {"work_certificate"}
    spec = extended.require("work_certificate")
    assert spec.required_rules == ("rule:7.1", "rule:7.2", "rule:7.3")
    # три типа кейса тянем из основного конфига как есть
    assert extended.require("study_certificate") == case.require("study_certificate")


def test_example_rules_keep_case_regulation_and_mark_demo_section():
    case = (PROJECT_ROOT / "rules.md").read_text(encoding="utf-8").rstrip("\n")
    example = (EXAMPLE / "rules.md").read_text(encoding="utf-8")
    assert example.startswith(case)
    assert "не входит в Положение кейса" in example and "7.1." in example


@requires_ocr
def test_work_certificate_is_read_by_config_only():
    from backend.app.services.pipeline import Pipeline

    pipe = Pipeline(use_cache=False)
    pipe.registry = load_registry(EXAMPLE / "document_types.yaml")
    result = pipe.analyze_path(EXAMPLE / "work-certificate.png", "auto")
    assert result.document_type == "work_certificate"
    assert result.validation.status == "document_ok"
    assert result.fields["position"] == "младший аналитик"
    assert result.fields["employment_start"] == "2026-07-01"


# --- демо-режим ---------------------------------------------------------------
@requires_ocr
@pytest.mark.parametrize(
    ("name", "doc_type", "missing"),
    [
        ("study-certificate.png", "study_certificate", []),
        ("academic-leave.png", "academic_leave_application", ["application_date", "signature_present"]),
        ("internship-certificate.pdf", "internship_certificate", []),
        ("photo-angle.jpg", "study_certificate", []),
    ],
)
def test_demo_samples_give_the_promised_result(name, doc_type, missing):
    from backend.app.services.pipeline import pipeline

    result = pipeline.analyze_path(DEMO / name, "auto")
    assert result.document_type == doc_type
    assert sorted(result.validation.missing_fields) == missing


@requires_ocr
def test_api_marks_aligned_page_for_before_after_toggle(client):
    def analyze(name: str, mime: str) -> dict:
        response = client.post(
            "/api/v1/analyze", files={"file": (name, (DEMO / name).read_bytes(), mime)}, data={"document_type": "auto"}
        )
        assert response.status_code == 200, response.text
        return response.json()

    assert analyze("photo-angle.jpg", "image/jpeg")["page_enhanced"] is True
    assert analyze("study-certificate.png", "image/png")["page_enhanced"] is False


def test_demo_files_are_ours_not_the_organizers():
    # образцы демо-режима — это синтетика из генератора, страницы открытого набора туда не суём
    for name in ("study-certificate.png", "academic-leave.png", "photo-angle.jpg"):
        assert (DEMO / name).stat().st_size > 10_000
    assert b"%PDF" in (DEMO / "internship-certificate.pdf").read_bytes()[:8]


# --- коды восстановления 2FA ---------------------------------------------------
def _enable_2fa(client, headers):
    setup = client.post("/api/auth/2fa/setup", headers=headers).json()
    enabled = client.post("/api/auth/2fa/enable", headers=headers, json={"code": auth.totp_now(setup["secret"])})
    assert enabled.status_code == 200, enabled.text
    return setup["secret"], enabled.json()


def test_recovery_codes_work_once_and_can_be_renewed(client):
    session = _register(client)
    headers = _auth(session["access_token"])
    secret, enabled = _enable_2fa(client, headers)
    codes = enabled["recovery_codes"]
    assert len(codes) == 10 and len(set(codes)) == 10
    assert enabled["user"]["recovery_codes_left"] == 10
    # в базе лежат только хеши, сами коды не храним
    stored = auth.store._one("SELECT code_hash FROM recovery_codes LIMIT 1")["code_hash"]
    assert stored not in codes and len(stored) == 64

    def second_step(code: str):
        step = client.post("/api/auth/login", json={"login": "student1", "password": "password123"}).json()
        return client.post("/api/auth/2fa/login", json={"challenge": step["challenge"], "code": code})

    ok = second_step(codes[0].upper())  # регистр и дефис роли не играют
    assert ok.status_code == 200 and ok.json()["user"]["recovery_codes_left"] == 9
    assert second_step(codes[0]).status_code == 401  # одноразовый, второй раз не прокатит
    assert second_step("zzzzz-zzzzz").status_code == 401

    renewed = client.post("/api/auth/2fa/recovery", headers=headers, json={"code": auth.totp_now(secret)})
    assert renewed.status_code == 200
    fresh = renewed.json()["recovery_codes"]
    assert renewed.json()["user"]["recovery_codes_left"] == 10
    assert second_step(codes[1]).status_code == 401  # старые коды сгорели
    assert client.post("/api/auth/2fa/recovery", headers=headers, json={"code": fresh[0]}).status_code == 422

    # телефон потеряли — выключаем 2FA кодом восстановления
    off = client.post("/api/auth/2fa/disable", headers=headers, json={"code": fresh[0]})
    assert off.status_code == 200 and off.json()["user"]["two_factor_enabled"] is False
    assert auth.store.recovery_left(session["user"]["id"]) == 0


def test_recovery_codes_not_exported(client):
    session = _register(client)
    headers = _auth(session["access_token"])
    _, enabled = _enable_2fa(client, headers)
    exported = client.get("/api/me/export", headers=headers).text
    assert all(code not in exported for code in enabled["recovery_codes"])


# --- демо-аккаунт ---------------------------------------------------------------
def test_demo_account_is_off_by_default(client):
    assert client.post("/api/auth/demo").status_code == 404
    assert client.get("/api/app-config").json()["auth"]["demo"] is False


def test_demo_account_signs_in_and_cannot_be_taken_over(client, monkeypatch):
    monkeypatch.setenv("UMIR_DEMO_ACCOUNT", "1")
    assert client.get("/api/app-config").json()["auth"]["demo"] is True
    first = client.post("/api/auth/demo")
    assert first.status_code == 200
    user = first.json()["user"]
    assert user["login"] == "demo" and user["demo"] is True
    assert client.post("/api/auth/demo").json()["user"]["id"] == user["id"]  # аккаунт тот же самый

    headers = _auth(first.json()["access_token"])
    assert client.get("/api/me/history", headers=headers).status_code == 200
    assert client.post("/api/auth/2fa/setup", headers=headers).status_code == 403
    assert client.post("/api/auth/password/change", headers=headers, json={"password": "newpassword1"}).status_code == 403
    assert client.post("/api/auth/me/delete", headers=headers, json={}).status_code == 403
    assert client.post("/api/auth/yandex/link", headers=headers).status_code == 403
    # обычных пользователей не зацепило
    other = _register(client, login="student2")
    assert other["user"]["demo"] is False
    assert client.post("/api/auth/2fa/setup", headers=_auth(other["access_token"])).status_code == 200


# --- Telegram-бот -----------------------------------------------------------------
class FakeApi:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.calls: list[tuple[str, dict]] = []

    def call(self, method, **params):
        self.calls.append((method, params))
        return {"file_path": "photos/file_1.png"} if method == "getFile" else True

    def download(self, file_path):
        return self.payload


def test_telegram_bot_answers_document_and_follow_up_question():
    from backend.app.services.pipeline import pipeline
    from backend.scripts.telegram_bot import HELP, Bot

    real = pipeline.analyze_path(DEMO / "academic-leave.png", "academic_leave_application")
    seen = {}

    def analyze(payload, filename, document_type):
        seen.update(payload=payload, filename=filename, document_type=document_type)
        return real

    bot = Bot(FakeApi(b"png-bytes"), analyze=analyze)
    chat = {"id": 42}
    assert bot.handle({"chat": chat, "text": "/start"}) == HELP
    assert "Сначала пришлите" in bot.handle({"chat": chat, "text": "Какая дата?"})

    reply = bot.handle({"chat": chat, "document": {"file_id": "F", "file_name": "scan.png", "mime_type": "image/png"}})
    assert seen == {"payload": b"png-bytes", "filename": "scan.png", "document_type": "auto"}
    assert "Заявление на академический отпуск" in reply and "4.2" in reply

    follow = bot.handle({"chat": chat, "text": "Какова причина отпуска?"})
    assert real.fields["leave_reason"] in follow and "Источники" in follow

    too_big = {"chat": chat, "photo": [{"file_id": "P", "file_size": 30 * 1024 * 1024}]}
    assert "20 МБ" in bot.handle(too_big)


# --- веса OCR -----------------------------------------------------------------------
@requires_ocr
def test_download_models_recognises_installed_weights():
    from backend.app.config import _default_tessdata
    from backend.scripts.download_models import inspect

    state = inspect(_default_tessdata())
    assert state["rus"] in {"ok", "other"}
    assert inspect(Path("/nonexistent")) == {"rus": "missing", "eng": "missing"}
