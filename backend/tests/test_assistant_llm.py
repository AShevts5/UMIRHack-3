"""Мини-LLM в помощнике: проверяем на заглушке вместо настоящей модели (её в тестах нет)."""

from __future__ import annotations

import pytest

from backend.app.services import assistant, llm


@pytest.fixture()
def fake_llm(monkeypatch):
    calls: list[list[dict]] = []
    replies: list[str | None] = []

    def chat(messages):
        calls.append(messages)
        return replies.pop(0) if replies else None

    monkeypatch.setattr(llm, "enabled", lambda: True)
    monkeypatch.setattr(assistant, "LLM_KINDS", {"faq", "faculty", "rule"})
    monkeypatch.setattr(llm, "chat", chat)
    return calls, replies


def test_generated_answer_replaces_fragment(fake_llm):
    calls, replies = fake_llm
    replies.append("Откройте профиль и включите 2FA, потом отсканируйте QR-код.")
    reply = assistant.answer("Как включить двухфакторную аутентификацию?", "rinh")
    assert reply["generated"] is True
    assert reply["answer"].startswith("Откройте профиль")
    assert reply["sources"]
    # в запрос модели уходят найденные фрагменты и сам вопрос
    prompt = calls[0][-1]["content"]
    assert "Как включить двухфакторную аутентификацию?" in prompt


def test_invented_numbers_fall_back_to_fragment(fake_llm):
    _, replies = fake_llm
    replies.append("Позвоните по телефону 8-800-555-35-35, кабинет 214.")
    reply = assistant.answer("Как включить двухфакторную аутентификацию?", "rinh")
    assert reply["generated"] is False
    assert "555" not in reply["answer"]


def test_weak_match_is_not_rewritten(fake_llm):
    calls, _ = fake_llm
    reply = assistant.answer("Что делать если справка без печати?", "rinh")
    assert reply["generated"] is False
    assert not calls


def test_refusal_does_not_call_model(fake_llm):
    calls, _ = fake_llm
    reply = assistant.answer("Какая погода будет завтра в Москве?")
    assert reply["kind"] == "none" and not calls


def test_model_failure_keeps_fragment(fake_llm):
    reply = assistant.answer("Как включить двухфакторную аутентификацию?", "rinh")  # заглушка вернёт None
    assert reply["generated"] is False and reply["answer"]


def test_status_without_model(monkeypatch, tmp_path):
    monkeypatch.setenv("UMIR_LLM", "1")
    monkeypatch.setattr(llm, "MODEL_PATH", tmp_path / "нет.gguf")
    assert llm.status()["state"] == "no-model"
    assert llm.enabled() is False
    assert llm.chat([{"role": "user", "content": "привет"}]) is None


def test_switched_off_by_env(monkeypatch):
    monkeypatch.setenv("UMIR_LLM", "0")
    assert llm.status() == {"state": "off", "model": llm.MODEL_PATH.name, "enabled": False}


def test_default_kinds_rewrite_only_faculty_cards(fake_llm, monkeypatch):
    calls, replies = fake_llm
    monkeypatch.setattr(assistant, "LLM_KINDS", {"faculty"})
    assert assistant.answer("Как включить двухфакторную аутентификацию?", "rinh")["generated"] is False
    assert not calls
    replies.append("Деканат юридического факультета — в учебном корпусе на ул. Максима Горького.")
    reply = assistant.answer("Где деканат юридического факультета?", "rinh")
    assert reply["kind"] == "faculty" and reply["generated"] is True
