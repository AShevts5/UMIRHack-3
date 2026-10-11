"""API нашего путеводителя: аккаунты, реестр вузов, AI-помощник.

Это всё мы накрутили сверх кейса, к ``main.app`` цепляется одним ``include_router``.
Ручки проверки документов (``/api/v1/*``) от входа никак не зависят: жюри и пакетный
прогон гоняют всё как раньше, а если пользователь вошёл, его проверки ещё и падают в
«Историю обращений».

``GET  /api/app-config``                     футер, версия, тексты помощника
``GET  /api/universities``                   вузы для карты
``GET  /api/universities/{id}``              вуз целиком: холл, корпуса, факультеты
``POST /api/auth/register`` · ``/login`` · ``/2fa/login``
``GET  /api/auth/me`` · ``PATCH /api/auth/me``
``POST /api/auth/2fa/setup`` · ``/2fa/enable`` · ``/2fa/disable`` · ``/2fa/recovery`` (новые коды восстановления)
``POST /api/auth/password/forgot`` · ``/password/reset`` · ``/password/change``
``POST /api/auth/me/delete``                 удалить аккаунт (пароль и код 2FA)
``GET  /api/auth/providers`` · ``/api/auth/yandex/login`` · ``/api/auth/yandex/callback``
``POST /api/auth/demo``                      вход в демо-аккаунт (если включён UMIR_DEMO_ACCOUNT)
``POST /api/auth/yandex/link`` · ``/yandex/unlink``  привязать Яндекс ID к аккаунту
``GET  /api/me/history`` · ``DELETE``         история обращений вошедшего пользователя
``GET  /api/me/export``                      все свои данные одним JSON
``POST /api/assistant/ask``                  ответ помощника (локальный поиск)
"""

from __future__ import annotations

import logging
import secrets
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from .services import assistant, auth, oauth_yandex, universities
from .services.document_config import get_registry

logger = logging.getLogger("umir.guide")

router = APIRouter(tags=["guide"])

_STATE_COOKIE = "umir_oauth_state"
_LINK_COOKIE = "umir_oauth_link"
_LINK_TTL_S = 600


# ---------------------------------------------------------------------------
# Пользователь из заголовка Authorization
# ---------------------------------------------------------------------------
def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization") or ""
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return None


def optional_user(request: Request) -> auth.User | None:
    token = _bearer(request)
    if not token:
        return None
    try:
        return auth.user_from_token(token)
    except auth.AuthError:
        return None


def require_user(request: Request) -> auth.User:
    token = _bearer(request)
    if not token:
        raise HTTPException(status_code=401, detail="Нужно войти в приложение.")
    try:
        return auth.user_from_token(token)
    except auth.AuthError as exc:
        # по этому заголовку фронт понимает, что сессия протухла, а не пароль кривой
        raise HTTPException(
            status_code=exc.status, detail=str(exc), headers={"WWW-Authenticate": 'Bearer error="invalid_token"'}
        ) from exc


def require_own_account(user: auth.User = Depends(require_user)) -> auth.User:
    """То же, что require_user, только без общего демо-аккаунта: его себе «забирать» нельзя."""
    if auth.is_demo(user):
        raise HTTPException(status_code=403, detail="В демо-аккаунте это недоступно: им пользуются все гости.")
    return user


def _fail(exc: auth.AuthError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=str(exc))


def _base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


# ---------------------------------------------------------------------------
# Конфиг приложения и вузы
# ---------------------------------------------------------------------------
@router.get("/api/app-config")
def app_config() -> dict[str, Any]:
    config = universities.app_config()
    return {
        "team_name": config.get("team_name"),
        "year": config.get("year"),
        "repo_url": config.get("repo_url"),
        "version": config.get("version"),
        "default_university": config.get("default_university"),
        "assistant": {**config.get("assistant", {}), "suggestions": assistant.suggestions()},
        "auth": {"yandex": oauth_yandex.configured(), "demo": auth.demo_login() is not None},
    }


@router.get("/api/universities")
def list_universities(q: str | None = None) -> dict[str, Any]:
    items = universities.all_universities()
    if q:
        needle = q.strip().lower()
        items = [
            item
            for item in items
            if needle in item["name"].lower() or needle in item["short_name"].lower() or needle in (item.get("city") or "").lower()
        ]
    return {"items": [universities.summary(item) for item in items]}


@router.get("/api/universities/{university_id}")
def get_university(university_id: str) -> dict[str, Any]:
    item = universities.get(university_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Вуз не найден")
    return universities.detail(item)


# ---------------------------------------------------------------------------
# Учётки
# ---------------------------------------------------------------------------
class RegisterIn(BaseModel):
    login: str | None = Field(None, max_length=64)
    email: str | None = Field(None, max_length=254)
    name: str | None = Field(None, max_length=120)
    password: str = Field(..., max_length=128)
    university_id: str | None = None


class LoginIn(BaseModel):
    login: str = Field(..., min_length=1, max_length=254)
    password: str = Field(..., max_length=128)


class SecondFactorIn(BaseModel):
    challenge: str
    code: str = Field(..., max_length=12)


class CodeIn(BaseModel):
    code: str = Field(..., max_length=12)


class ProfileIn(BaseModel):
    name: str | None = Field(None, max_length=120)
    university_id: str | None = None


class ForgotIn(BaseModel):
    login: str = Field(..., min_length=1, max_length=254)


class ResetIn(BaseModel):
    token: str
    password: str = Field(..., max_length=128)


class ConfirmIn(BaseModel):
    password: str | None = Field(None, max_length=128)
    code: str | None = Field(None, max_length=12)


class ChangePasswordIn(BaseModel):
    current_password: str | None = Field(None, max_length=128)
    password: str = Field(..., max_length=128)


@router.post("/api/auth/register")
def register(body: RegisterIn) -> dict[str, Any]:
    if body.university_id and universities.get(body.university_id) is None:
        raise HTTPException(status_code=422, detail="Неизвестный вуз")
    try:
        user = auth.register(body.login, body.email, body.password, body.name, body.university_id)
    except auth.AuthError as exc:
        raise _fail(exc) from exc
    return auth.session_payload(user)


@router.post("/api/auth/login")
def login(body: LoginIn) -> dict[str, Any]:
    try:
        return auth.login(body.login, body.password)
    except auth.AuthError as exc:
        raise _fail(exc) from exc


@router.post("/api/auth/2fa/login")
def login_second_factor(body: SecondFactorIn) -> dict[str, Any]:
    try:
        return auth.login_second_factor(body.challenge, body.code)
    except auth.AuthError as exc:
        raise _fail(exc) from exc


@router.get("/api/auth/me")
def me(user: auth.User = Depends(require_user)) -> dict[str, Any]:
    return {"user": user.public()}


@router.patch("/api/auth/me")
def update_me(body: ProfileIn, user: auth.User = Depends(require_user)) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    if body.university_id is not None:
        if universities.get(body.university_id) is None:
            raise HTTPException(status_code=422, detail="Неизвестный вуз")
        fields["university_id"] = body.university_id
    if body.name is not None:
        fields["name"] = body.name.strip() or None
    auth.store.update(user.id, **fields)
    updated = auth.store.by_id(user.id)
    assert updated is not None
    return {"user": updated.public()}


@router.post("/api/auth/2fa/setup")
def totp_setup(user: auth.User = Depends(require_own_account)) -> dict[str, Any]:
    return auth.start_totp_setup(user)


@router.post("/api/auth/2fa/enable")
def totp_enable(body: CodeIn, user: auth.User = Depends(require_own_account)) -> dict[str, Any]:
    try:
        updated, codes = auth.enable_totp(user, body.code)
    except auth.AuthError as exc:
        raise _fail(exc) from exc
    # коды восстановления показываем ровно один раз, в базе лежат только их хеши
    return {"user": updated.public(), "recovery_codes": codes}


@router.post("/api/auth/2fa/recovery")
def totp_recovery(body: CodeIn, user: auth.User = Depends(require_own_account)) -> dict[str, Any]:
    try:
        updated, codes = auth.regenerate_recovery_codes(user, body.code)
    except auth.AuthError as exc:
        raise _fail(exc) from exc
    return {"user": updated.public(), "recovery_codes": codes}


@router.post("/api/auth/2fa/disable")
def totp_disable(body: CodeIn, user: auth.User = Depends(require_user)) -> dict[str, Any]:
    try:
        return {"user": auth.disable_totp(user, body.code).public()}
    except auth.AuthError as exc:
        raise _fail(exc) from exc


@router.post("/api/auth/password/forgot")
def forgot_password(body: ForgotIn, request: Request) -> dict[str, Any]:
    auth.request_password_reset(body.login, oauth_yandex.public_url(_base_url(request)))
    # отвечаем одинаково, есть такой юзер или нет, чтобы базу не палить
    return {
        "sent": False,
        "message": (
            "Если такой пользователь есть, ссылка для сброса готова. Почтовый сервер в учебной "
            "версии не подключён, поэтому ссылка записана в журнал сервера — попросите её у администратора."
        ),
    }


@router.post("/api/auth/password/reset")
def reset_password(body: ResetIn) -> dict[str, Any]:
    try:
        user = auth.reset_password(body.token, body.password)
    except auth.AuthError as exc:
        raise _fail(exc) from exc
    return auth.session_payload(user)


@router.post("/api/auth/password/change")
def change_password(body: ChangePasswordIn, user: auth.User = Depends(require_own_account)) -> dict[str, Any]:
    try:
        return {"user": auth.change_password(user, body.current_password, body.password).public()}
    except auth.AuthError as exc:
        raise _fail(exc) from exc


@router.post("/api/auth/me/delete")
def delete_me(body: ConfirmIn, user: auth.User = Depends(require_own_account)) -> dict[str, Any]:
    try:
        auth.delete_account(user, body.password, body.code)
    except auth.AuthError as exc:
        raise _fail(exc) from exc
    return {"deleted": True}


@router.get("/api/me/export")
def export_me(user: auth.User = Depends(require_user)) -> JSONResponse:
    return JSONResponse(
        auth.export_data(user),
        headers={"Content-Disposition": 'attachment; filename="umir-my-data.json"'},
    )


# ---------------------------------------------------------------------------
# Вход через Яндекс
# ---------------------------------------------------------------------------
@router.get("/api/auth/providers")
def providers() -> dict[str, Any]:
    return {
        "yandex": {"configured": oauth_yandex.configured(), "login_url": "/api/auth/yandex/login"},
        "demo": {"enabled": auth.demo_login() is not None},
    }


@router.post("/api/auth/demo")
def demo_login() -> dict[str, Any]:
    try:
        return auth.demo_session()
    except auth.AuthError as exc:
        raise _fail(exc) from exc


@router.post("/api/auth/yandex/link")
def yandex_link_start(user: auth.User = Depends(require_own_account)) -> dict[str, Any]:
    if not oauth_yandex.configured():
        raise HTTPException(status_code=409, detail="Вход через Яндекс не настроен на этом сервере.")
    token = auth.issue_token(user.id, typ="link", ttl=_LINK_TTL_S)
    return {"url": "/api/auth/yandex/login?" + urlencode({"link": token})}


@router.post("/api/auth/yandex/unlink")
def yandex_unlink(user: auth.User = Depends(require_user)) -> dict[str, Any]:
    try:
        return {"user": auth.unlink_yandex(user).public()}
    except auth.AuthError as exc:
        raise _fail(exc) from exc


@router.get("/api/auth/yandex/login")
def yandex_login(request: Request, link: str | None = None) -> RedirectResponse:
    if not oauth_yandex.configured():
        return RedirectResponse(url="/#/login?" + urlencode({"error": "Вход через Яндекс не настроен на этом сервере."}))
    state = oauth_yandex.new_state()
    response = RedirectResponse(url=oauth_yandex.authorize_url(state, _base_url(request)))
    response.set_cookie(_STATE_COOKIE, state, max_age=600, httponly=True, samesite="lax", path="/api/auth/yandex")
    if link:
        # Привязка к уже вошедшему юзеру: при переходе браузер заголовок Authorization
        # не передаст, поэтому профиль выдаёт короткий токен привязки. Костыль, но работает.
        try:
            auth.read_token(link, typ="link")
        except auth.AuthError:
            return RedirectResponse(url="/#/profile?" + urlencode({"error": "Ссылка привязки устарела, начните ещё раз."}))
        response.set_cookie(_LINK_COOKIE, link, max_age=_LINK_TTL_S, httponly=True, samesite="lax", path="/api/auth/yandex")
    else:
        response.delete_cookie(_LINK_COOKIE, path="/api/auth/yandex")
    return response


@router.get("/api/auth/yandex/callback")
def yandex_callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None):
    def back(**params: str) -> RedirectResponse:
        response = RedirectResponse(url="/#/login?" + urlencode(params))
        response.delete_cookie(_STATE_COOKIE, path="/api/auth/yandex")
        return response

    if error:
        return back(error="Вход через Яндекс отменён.")
    expected = request.cookies.get(_STATE_COOKIE)
    if not code or not state or not expected or not secrets.compare_digest(state, expected):
        return back(error="Сессия входа через Яндекс устарела. Попробуйте ещё раз.")
    try:
        token = oauth_yandex.exchange_code(code, _base_url(request))
        profile = oauth_yandex.fetch_profile(token)
    except oauth_yandex.YandexOAuthError as exc:
        logger.warning("yandex oauth failed: %s", exc)
        return back(error=f"Яндекс ID: {exc}")

    link_token = request.cookies.get(_LINK_COOKIE)
    if link_token:
        def to_profile(**params: str) -> RedirectResponse:
            response = RedirectResponse(url="/#/profile?" + urlencode(params))
            response.delete_cookie(_STATE_COOKIE, path="/api/auth/yandex")
            response.delete_cookie(_LINK_COOKIE, path="/api/auth/yandex")
            return response

        try:
            owner = int(auth.read_token(link_token, typ="link")["sub"])
            auth.link_yandex(owner, profile["id"])
        except auth.AuthError as exc:
            return to_profile(error=str(exc))
        return to_profile(linked="1")

    user = auth.store.by_yandex(profile["id"])
    if user is None and profile.get("email"):
        row = auth.store.by_identifier(profile["email"])
        if row is not None:
            auth.store.update(row["id"], yandex_id=profile["id"])
            user = auth.store.by_id(row["id"])
    if user is None:
        try:
            user = auth.store.create(
                login=None, email=profile.get("email"), name=profile.get("name"), password=None, yandex_id=profile["id"]
            )
        except auth.AuthError:
            user = auth.store.create(login=None, email=None, name=profile.get("name"), password=None, yandex_id=profile["id"])
    if user.totp_enabled:
        return back(challenge=auth.issue_token(user.id, typ="2fa", ttl=auth.CHALLENGE_TTL_S))
    # токен кладём во фрагмент адреса (#...), так он не светится в логах сервера и в Referer
    response = RedirectResponse(url="/#/auth?" + urlencode({"token": auth.issue_token(user.id)}))
    response.delete_cookie(_STATE_COOKIE, path="/api/auth/yandex")
    return response


# ---------------------------------------------------------------------------
# История обращений
# ---------------------------------------------------------------------------
@router.delete("/api/me/history")
def clear_my_history(user: auth.User = Depends(require_user)) -> dict[str, Any]:
    return {"deleted": auth.store.clear_history(user.id)}


@router.get("/api/me/history")
def my_history(limit: int = 50, user: auth.User = Depends(require_user)) -> dict[str, Any]:
    items = auth.store.history(user.id, max(1, min(limit, 200)))
    for item in items:
        item["missing_labels"] = [_field_label(item["document_type"], key) for key in item["missing"]]
    return {"items": items}


def _field_label(document_type: str | None, key: str) -> str:
    """Человеческое название поля из document_types.yaml вместо ключа (``seal_present`` → «Печать»)."""
    try:
        spec = get_registry().get(document_type or "")
    except Exception:  # noqa: BLE001 - подпись в истории не должна ломать ответ
        spec = None
    if spec is not None:
        found = spec.field(key) or spec.mark(key)
        if found is not None:
            return found.label
    return key


def record_history(request: Request, response: Any, filename: str, question: str | None) -> None:
    """Пишем итог проверки в историю, если пользователь залогинен. Сам файл и
    распознанный текст не храним — только имя файла, тип и итог."""
    user = optional_user(request)
    if user is None:
        return
    try:
        validation = response.validation
        auth.store.add_history(
            user.id,
            university_id=request.headers.get("x-university") or user.university_id,
            filename=filename,
            document_type=response.document_type,
            document_type_title=getattr(response, "document_type_title", None),
            complete=validation.status == "document_ok",
            missing="\n".join(getattr(validation, "missing_fields", []) or []),
            question=question,
        )
    except Exception:  # noqa: BLE001 - история не должна ломать проверку
        logger.warning("history record failed", exc_info=True)


# ---------------------------------------------------------------------------
# AI-помощник
# ---------------------------------------------------------------------------
class AskIn(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    university_id: str | None = None


@router.post("/api/assistant/ask")
def assistant_ask(body: AskIn) -> JSONResponse:
    return JSONResponse(content=assistant.answer(body.question, body.university_id))
