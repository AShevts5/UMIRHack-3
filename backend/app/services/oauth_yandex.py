"""Вход через Яндекс ID (OAuth 2.0, authorization code).

Включаем через переменные окружения:

``UMIR_YANDEX_CLIENT_ID``, ``UMIR_YANDEX_CLIENT_SECRET``
    ключи приложения, берём на https://oauth.yandex.ru (права: «Доступ к логину, имени и
    фамилии», «Доступ к адресу электронной почты»);
``UMIR_PUBLIC_URL``
    внешний адрес сервиса, если он не совпадает с тем, откуда пришёл запрос
    (например ``https://dean.example.ru``). Redirect URI в настройках приложения
    Яндекса ставим такой: ``<адрес>/api/auth/yandex/callback``.
``UMIR_YANDEX_REDIRECT_URI``
    если в Яндексе уже прописан другой Redirect URI (у нас на проде это просто
    ``https://dekanatonline.ru``), пишем его сюда. Яндекс вернёт юзера на главную с
    ``?code=...&state=...``, а главная сама перекинет это на callback (см. main.py).

Если ключей нет, кнопка «Войти через Яндекс» просто пишет, что вход не настроен, и
наружу ничего не шлёт.
"""

from __future__ import annotations

import json
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request

AUTHORIZE_URL = "https://oauth.yandex.ru/authorize"
TOKEN_URL = "https://oauth.yandex.ru/token"
INFO_URL = "https://login.yandex.ru/info?format=json"
CALLBACK_PATH = "/api/auth/yandex/callback"


class YandexOAuthError(Exception):
    pass


def client_id() -> str | None:
    return os.environ.get("UMIR_YANDEX_CLIENT_ID") or None


def client_secret() -> str | None:
    return os.environ.get("UMIR_YANDEX_CLIENT_SECRET") or None


def configured() -> bool:
    return bool(client_id() and client_secret())


def public_url(request_base: str) -> str:
    return (os.environ.get("UMIR_PUBLIC_URL") or request_base).rstrip("/")


def redirect_uri(request_base: str) -> str:
    # Яндекс сверяет redirect_uri с настройками приложения строго, поэтому если он задан руками, берём как есть
    return os.environ.get("UMIR_YANDEX_REDIRECT_URI") or public_url(request_base) + CALLBACK_PATH


def is_return_to_root(query: dict) -> bool:
    """Яндекс вернул юзера на главную (Redirect URI = корень сайта), а не на callback."""
    return bool(query.get("state") and (query.get("code") or query.get("error")))


def new_state() -> str:
    return secrets.token_urlsafe(24)


def authorize_url(state: str, request_base: str) -> str:
    query = {
        "response_type": "code",
        "client_id": client_id() or "",
        "redirect_uri": redirect_uri(request_base),
        "state": state,
        "force_confirm": "no",
    }
    return AUTHORIZE_URL + "?" + urllib.parse.urlencode(query)


def _post_form(url: str, data: dict) -> dict:
    body = urllib.parse.urlencode(data).encode()
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - фиксированный https-адрес
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("error_description")
        except Exception:  # noqa: BLE001
            detail = None
        raise YandexOAuthError(detail or f"Яндекс ответил {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise YandexOAuthError("Не удалось связаться с oauth.yandex.ru") from exc


def exchange_code(code: str, request_base: str) -> str:
    payload = _post_form(
        TOKEN_URL,
        {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id() or "",
            "client_secret": client_secret() or "",
            "redirect_uri": redirect_uri(request_base),
        },
    )
    token = payload.get("access_token")
    if not token:
        raise YandexOAuthError("Яндекс не выдал токен доступа")
    return token


def fetch_profile(access_token: str) -> dict:
    request = urllib.request.Request(INFO_URL, headers={"Authorization": f"OAuth {access_token}"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            info = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError) as exc:
        raise YandexOAuthError("Не удалось получить профиль Яндекс ID") from exc
    if not info.get("id"):
        raise YandexOAuthError("В профиле Яндекс ID нет идентификатора")
    return {
        "id": str(info["id"]),
        "login": info.get("login"),
        "email": info.get("default_email") or next(iter(info.get("emails") or []), None),
        "name": info.get("real_name") or info.get("display_name") or info.get("login"),
    }
