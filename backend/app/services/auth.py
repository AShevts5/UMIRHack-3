"""Учётки: регистрация, вход по паролю, 2FA (TOTP) и JWT.

Храним всё в локальном SQLite в папке данных (``UMIR_DATA_DIR``, по умолчанию
``var/`` рядом с проектом). Пароли хешируем scrypt из стандартной библиотеки,
одноразовые коды двухфакторки считаем по RFC 6238 (как в Google Authenticator,
Яндекс Ключе и т.п.), токены у нас JWT HS256.

Никаких внешних сервисов тут не дёргаем; вход через Яндекс (OAuth 2.0) живёт в
``backend.app.services.oauth_yandex`` и включается, только если заданы ключи.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import re
import secrets
import sqlite3
import struct
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import jwt

from ..config import PROJECT_ROOT

logger = logging.getLogger("umir.auth")

ACCESS_TTL_S = int(os.environ.get("UMIR_ACCESS_TTL_S", str(12 * 3600)))
CHALLENGE_TTL_S = 5 * 60
RESET_TTL_S = 30 * 60
TOTP_STEP_S = 30
TOTP_DIGITS = 6
TOTP_ISSUER = "Онлайн-деканат"

_LOGIN_RE = re.compile(r"^[A-Za-z0-9_.\-]{3,32}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AuthError(Exception):
    """Ошибка, которую не стыдно показать пользователю прямо как есть."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def data_dir() -> Path:
    path = Path(os.environ.get("UMIR_DATA_DIR", str(PROJECT_ROOT / "var"))).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _jwt_secret() -> str:
    """Секрет для подписи токенов: берём из окружения или из файла, сгенерённого один раз."""
    env = os.environ.get("UMIR_JWT_SECRET")
    if env:
        return env
    path = data_dir() / "jwt_secret"
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    secret = secrets.token_urlsafe(48)
    path.write_text(secret, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover - Windows
        pass
    return secret


# ---------------------------------------------------------------------------
# Пароли
# ---------------------------------------------------------------------------
_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, **_SCRYPT)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str | None) -> bool:
    if not stored or not stored.startswith("scrypt$"):
        return False
    _, salt_b64, digest_b64 = stored.split("$", 2)
    salt = base64.b64decode(salt_b64)
    expected = base64.b64decode(digest_b64)
    actual = hashlib.scrypt(password.encode("utf-8"), salt=salt, **_SCRYPT)
    return hmac.compare_digest(actual, expected)


# ---------------------------------------------------------------------------
# TOTP (RFC 6238)
# ---------------------------------------------------------------------------
def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _hotp(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % 10**TOTP_DIGITS
    return str(code).zfill(TOTP_DIGITS)


def totp_now(secret: str, at: float | None = None) -> str:
    return _hotp(secret, int((at if at is not None else time.time()) // TOTP_STEP_S))


def verify_totp(secret: str | None, code: str, at: float | None = None) -> bool:
    """Пускаем текущий шаг и по одному соседнему: часы на телефоне бывает что врут."""
    if not secret:
        return False
    code = re.sub(r"\s", "", code or "")
    if not code.isdigit() or len(code) != TOTP_DIGITS:
        return False
    counter = int((at if at is not None else time.time()) // TOTP_STEP_S)
    return any(hmac.compare_digest(_hotp(secret, counter + delta), code) for delta in (-1, 0, 1))


def otpauth_url(secret: str, account: str) -> str:
    label = quote(f"{TOTP_ISSUER}:{account}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(TOTP_ISSUER)}&digits={TOTP_DIGITS}&period={TOTP_STEP_S}"


def qr_svg(text: str) -> str:
    """QR-код для приложения-аутентификатора, рисуем сами локально (segno)."""
    import io

    import segno

    buffer = io.BytesIO()
    segno.make(text, error="m").save(buffer, kind="svg", scale=5, border=2, dark="#131E36", light="#FFFFFF", xmldecl=False)
    return buffer.getvalue().decode("utf-8")


# ---------------------------------------------------------------------------
# Хранилище юзеров и истории обращений
# ---------------------------------------------------------------------------
@dataclass
class User:
    id: int
    login: str | None
    email: str | None
    name: str | None
    yandex_id: str | None
    totp_enabled: bool
    university_id: str | None
    created_at: str
    has_password: bool
    recovery_codes_left: int = 0

    def public(self) -> dict:
        return {
            "id": self.id,
            "login": self.login,
            "email": self.email,
            "name": self.name or self.login or self.email,
            "yandex_linked": bool(self.yandex_id),
            "two_factor_enabled": self.totp_enabled,
            "recovery_codes_left": self.recovery_codes_left,
            "demo": is_demo(self),
            "university_id": self.university_id,
            "created_at": self.created_at,
            "has_password": self.has_password,
        }


_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    login TEXT UNIQUE COLLATE NOCASE,
    email TEXT UNIQUE COLLATE NOCASE,
    name TEXT,
    password_hash TEXT,
    yandex_id TEXT UNIQUE,
    totp_secret TEXT,
    totp_enabled INTEGER NOT NULL DEFAULT 0,
    totp_pending TEXT,
    university_id TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    university_id TEXT,
    filename TEXT,
    document_type TEXT,
    document_type_title TEXT,
    complete INTEGER,
    missing TEXT,
    question TEXT
);
CREATE INDEX IF NOT EXISTS history_user ON history(user_id, created_at);
CREATE TABLE IF NOT EXISTS recovery_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code_hash TEXT NOT NULL,
    used_at TEXT
);
CREATE INDEX IF NOT EXISTS recovery_user ON recovery_codes(user_id);
"""


class AuthStore:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            path = self._path or (data_dir() / "users.sqlite3")
            conn = sqlite3.connect(str(path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.executescript(_SCHEMA)
            self._conn = conn
        return self._conn

    def _row_to_user(self, row: sqlite3.Row | None) -> User | None:
        if row is None:
            return None
        return User(
            id=row["id"],
            login=row["login"],
            email=row["email"],
            name=row["name"],
            yandex_id=row["yandex_id"],
            totp_enabled=bool(row["totp_enabled"]),
            university_id=row["university_id"],
            created_at=row["created_at"],
            has_password=bool(row["password_hash"]),
            recovery_codes_left=self.recovery_left(row["id"]) if row["totp_enabled"] else 0,
        )

    def _one(self, sql: str, *args) -> sqlite3.Row | None:
        with self._lock:
            return self._db().execute(sql, args).fetchone()

    def _exec(self, sql: str, *args) -> int:
        with self._lock:
            conn = self._db()
            cursor = conn.execute(sql, args)
            conn.commit()
            return cursor.lastrowid or 0

    # --- юзеры
    def by_id(self, user_id: int) -> User | None:
        return self._row_to_user(self._one("SELECT * FROM users WHERE id = ?", user_id))

    def by_identifier(self, identifier: str) -> sqlite3.Row | None:
        ident = identifier.strip()
        return self._one("SELECT * FROM users WHERE login = ? OR email = ?", ident, ident)

    def by_yandex(self, yandex_id: str) -> User | None:
        return self._row_to_user(self._one("SELECT * FROM users WHERE yandex_id = ?", yandex_id))

    def raw(self, user_id: int) -> sqlite3.Row | None:
        return self._one("SELECT * FROM users WHERE id = ?", user_id)

    def create(
        self,
        *,
        login: str | None,
        email: str | None,
        name: str | None,
        password: str | None,
        yandex_id: str | None = None,
        university_id: str | None = None,
    ) -> User:
        try:
            user_id = self._exec(
                "INSERT INTO users(login, email, name, password_hash, yandex_id, university_id) VALUES (?,?,?,?,?,?)",
                login,
                email,
                name,
                hash_password(password) if password else None,
                yandex_id,
                university_id,
            )
        except sqlite3.IntegrityError as exc:
            raise AuthError("Пользователь с таким логином или email уже есть.", 409) from exc
        user = self.by_id(user_id)
        assert user is not None
        return user

    def update(self, user_id: int, **fields) -> None:
        if not fields:
            return
        columns = ", ".join(f"{key} = ?" for key in fields)
        self._exec(f"UPDATE users SET {columns} WHERE id = ?", *fields.values(), user_id)

    def set_password(self, user_id: int, password: str) -> None:
        self.update(user_id, password_hash=hash_password(password))

    def delete(self, user_id: int) -> None:
        with self._lock:
            conn = self._db()
            conn.execute("DELETE FROM history WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            conn.commit()

    # --- история обращений
    # --- коды восстановления 2FA (держим только хеши, каждый код одноразовый)
    def recovery_left(self, user_id: int) -> int:
        row = self._one("SELECT COUNT(*) AS n FROM recovery_codes WHERE user_id = ? AND used_at IS NULL", user_id)
        return int(row["n"]) if row else 0

    def replace_recovery(self, user_id: int, hashes: list[str]) -> None:
        with self._lock:
            conn = self._db()
            conn.execute("DELETE FROM recovery_codes WHERE user_id = ?", (user_id,))
            conn.executemany(
                "INSERT INTO recovery_codes (user_id, code_hash) VALUES (?, ?)", [(user_id, h) for h in hashes]
            )
            conn.commit()

    def use_recovery(self, user_id: int, code_hash: str) -> bool:
        with self._lock:
            conn = self._db()
            cursor = conn.execute(
                "UPDATE recovery_codes SET used_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') "
                "WHERE id = (SELECT id FROM recovery_codes WHERE user_id = ? AND code_hash = ? AND used_at IS NULL LIMIT 1)",
                (user_id, code_hash),
            )
            conn.commit()
            return cursor.rowcount == 1

    def clear_history(self, user_id: int) -> int:
        with self._lock:
            conn = self._db()
            cursor = conn.execute("DELETE FROM history WHERE user_id = ?", (user_id,))
            conn.commit()
            return cursor.rowcount

    def add_history(self, user_id: int, **item) -> None:
        self._exec(
            "INSERT INTO history(user_id, university_id, filename, document_type, document_type_title, complete, missing, question)"
            " VALUES (?,?,?,?,?,?,?,?)",
            user_id,
            item.get("university_id"),
            item.get("filename"),
            item.get("document_type"),
            item.get("document_type_title"),
            None if item.get("complete") is None else int(bool(item.get("complete"))),
            item.get("missing"),
            item.get("question"),
        )

    def history(self, user_id: int, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = (
                self._db()
                .execute(
                    "SELECT * FROM history WHERE user_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
                    (user_id, limit),
                )
                .fetchall()
            )
        return [
            {
                "id": row["id"],
                "created_at": row["created_at"],
                "university_id": row["university_id"],
                "filename": row["filename"],
                "document_type": row["document_type"],
                "document_type_title": row["document_type_title"],
                "complete": None if row["complete"] is None else bool(row["complete"]),
                "missing": [item for item in (row["missing"] or "").split("\n") if item],
                "question": row["question"],
            }
            for row in rows
        ]


store = AuthStore()


# ---------------------------------------------------------------------------
# Токены
# ---------------------------------------------------------------------------
def issue_token(user_id: int, typ: str = "access", ttl: int = ACCESS_TTL_S, **extra) -> str:
    now = int(time.time())
    payload = {"sub": str(user_id), "typ": typ, "iat": now, "exp": now + ttl, **extra}
    return jwt.encode(payload, _jwt_secret(), algorithm="HS256")


def read_token(token: str, typ: str = "access") -> dict:
    try:
        payload = jwt.decode(token, _jwt_secret(), algorithms=["HS256"])
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("Сессия истекла, войдите ещё раз.", 401) from exc
    except jwt.PyJWTError as exc:
        raise AuthError("Недействительный токен.", 401) from exc
    if payload.get("typ") != typ:
        raise AuthError("Недействительный токен.", 401)
    return payload


def user_from_token(token: str) -> User:
    payload = read_token(token)
    user = store.by_id(int(payload["sub"]))
    if user is None:
        raise AuthError("Пользователь не найден.", 401)
    return user


def session_payload(user: User) -> dict:
    return {"access_token": issue_token(user.id), "token_type": "bearer", "expires_in": ACCESS_TTL_S, "user": user.public()}


# ---------------------------------------------------------------------------
# Сценарии
# ---------------------------------------------------------------------------
class _Throttle:
    """Самая простая защита от перебора: не больше N неудачных попыток за окно."""

    def __init__(self, limit: int = 8, window_s: int = 300) -> None:
        self.limit = limit
        self.window_s = window_s
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.time()
        with self._lock:
            recent = [t for t in self._fails.get(key, []) if now - t < self.window_s]
            self._fails[key] = recent
            if len(recent) >= self.limit:
                raise AuthError("Слишком много попыток. Подождите несколько минут.", 429)

    def fail(self, key: str) -> None:
        with self._lock:
            self._fails.setdefault(key, []).append(time.time())

    def reset(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)


throttle = _Throttle()


def validate_password(password: str) -> None:
    if len(password) < 8:
        raise AuthError("Пароль должен быть не короче 8 символов.", 422)
    if len(password) > 128:
        raise AuthError("Пароль слишком длинный.", 422)


def register(login: str | None, email: str | None, password: str, name: str | None, university_id: str | None) -> User:
    login = (login or "").strip() or None
    email = (email or "").strip().lower() or None
    if not login and not email:
        raise AuthError("Укажите логин или email.", 422)
    if login and not _LOGIN_RE.match(login):
        raise AuthError("Логин: 3–32 символа, латиница, цифры, точка, дефис или подчёркивание.", 422)
    if email and not _EMAIL_RE.match(email):
        raise AuthError("Похоже, в email опечатка.", 422)
    validate_password(password)
    return store.create(login=login, email=email, name=(name or "").strip() or None, password=password, university_id=university_id)


def login(identifier: str, password: str) -> dict:
    key = identifier.strip().lower()
    throttle.check(key)
    row = store.by_identifier(identifier)
    if row is None or not verify_password(password, row["password_hash"]):
        throttle.fail(key)
        raise AuthError("Неверный логин или пароль.", 401)
    throttle.reset(key)
    if row["totp_enabled"]:
        return {
            "two_factor_required": True,
            "challenge": issue_token(row["id"], typ="2fa", ttl=CHALLENGE_TTL_S),
        }
    user = store.by_id(row["id"])
    assert user is not None
    return session_payload(user)


def login_second_factor(challenge: str, code: str) -> dict:
    payload = read_token(challenge, typ="2fa")
    user_id = int(payload["sub"])
    key = f"2fa:{user_id}"
    throttle.check(key)
    row = store.raw(user_id)
    if row is None or not check_second_factor(row, code):
        throttle.fail(key)
        raise AuthError(
            "Код не подошёл. Проверьте время на телефоне и введите новый код или код восстановления.", 401
        )
    throttle.reset(key)
    user = store.by_id(user_id)
    assert user is not None
    return session_payload(user)


RECOVERY_CODES = 10
_RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # без 0/o и 1/l/i, чтобы никто их не перепутал


def _recovery_hash(user_id: int, code: str) -> str:
    normalized = re.sub(r"[^a-z0-9]", "", code.lower())
    return hashlib.sha256(f"{user_id}:{normalized}".encode()).hexdigest()


def is_recovery_code(code: str) -> bool:
    """Код восстановления выглядит так: ``abcde-fghjk``; код из приложения — 6 цифр."""
    return bool(re.fullmatch(r"[a-z0-9]{5}-?[a-z0-9]{5}", code.strip().lower())) and not code.strip().isdigit()


def new_recovery_codes(user_id: int) -> list[str]:
    """Свежий набор одноразовых кодов, старые сразу дохнут. Показываем только один раз."""
    codes = []
    for _ in range(RECOVERY_CODES):
        raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(10))
        codes.append(f"{raw[:5]}-{raw[5:]}")
    store.replace_recovery(user_id, [_recovery_hash(user_id, code) for code in codes])
    return codes


def check_second_factor(row: sqlite3.Row, code: str) -> bool:
    """Код из приложения или ещё не использованный код восстановления (он после этого сгорает)."""
    code = (code or "").strip()
    if is_recovery_code(code):
        return store.use_recovery(row["id"], _recovery_hash(row["id"], code))
    return verify_totp(row["totp_secret"], code)


def start_totp_setup(user: User) -> dict:
    secret = new_totp_secret()
    store.update(user.id, totp_pending=secret)
    url = otpauth_url(secret, user.email or user.login or f"user{user.id}")
    return {"secret": secret, "otpauth_url": url, "qr_svg": qr_svg(url)}


def enable_totp(user: User, code: str) -> tuple[User, list[str]]:
    row = store.raw(user.id)
    pending = row["totp_pending"] if row else None
    if not pending:
        raise AuthError("Сначала начните настройку двухфакторной аутентификации.", 409)
    if not verify_totp(pending, code):
        raise AuthError("Код не подошёл. Введите код из приложения-аутентификатора.", 422)
    store.update(user.id, totp_secret=pending, totp_pending=None, totp_enabled=1)
    codes = new_recovery_codes(user.id)
    updated = store.by_id(user.id)
    assert updated is not None
    return updated, codes


def regenerate_recovery_codes(user: User, code: str) -> tuple[User, list[str]]:
    row = store.raw(user.id)
    if not row or not row["totp_enabled"]:
        raise AuthError("Двухфакторная аутентификация не включена.", 409)
    key = f"recovery:{user.id}"
    throttle.check(key)
    if not verify_totp(row["totp_secret"], code):
        throttle.fail(key)
        raise AuthError("Нужен действующий код из приложения-аутентификатора.", 422)
    throttle.reset(key)
    codes = new_recovery_codes(user.id)
    updated = store.by_id(user.id)
    assert updated is not None
    return updated, codes


def disable_totp(user: User, code: str) -> User:
    row = store.raw(user.id)
    if not row or not row["totp_enabled"]:
        raise AuthError("Двухфакторная аутентификация не включена.", 409)
    if not check_second_factor(row, code):
        raise AuthError("Код не подошёл.", 422)
    store.update(user.id, totp_secret=None, totp_enabled=0)
    store.replace_recovery(user.id, [])
    updated = store.by_id(user.id)
    assert updated is not None
    return updated


def _reset_fingerprint(row: sqlite3.Row) -> str:
    # как только пароль сменили, токен сброса сразу перестаёт работать
    return hashlib.sha256((row["password_hash"] or "none").encode()).hexdigest()[:16]


def request_password_reset(identifier: str, base_url: str) -> str | None:
    """Готовим ссылку для сброса. Почту мы в проекте не подключали, поэтому
    ссылка просто пишется в лог сервера (пользователю мы так честно и говорим)."""
    row = store.by_identifier(identifier)
    if row is None:
        return None
    token = issue_token(row["id"], typ="reset", ttl=RESET_TTL_S, fp=_reset_fingerprint(row))
    link = f"{base_url.rstrip('/')}/#/reset?token={token}"
    logger.warning("Ссылка для сброса пароля (почта не настроена): %s", link)
    return link


def reset_password(token: str, password: str) -> User:
    payload = read_token(token, typ="reset")
    row = store.raw(int(payload["sub"]))
    if row is None or payload.get("fp") != _reset_fingerprint(row):
        raise AuthError("Ссылка для сброса уже использована или устарела.", 401)
    validate_password(password)
    store.set_password(row["id"], password)
    user = store.by_id(row["id"])
    assert user is not None
    return user


def change_password(user: User, current: str | None, password: str) -> User:
    """Смена пароля из профиля. Если пароля пока нет (заходили только через Яндекс),
    его можно задать без текущего — тогда заработает и вход по логину."""
    row = store.raw(user.id)
    if row is None:
        raise AuthError("Пользователь не найден.", 404)
    key = f"pw:{user.id}"
    throttle.check(key)
    if row["password_hash"] and not verify_password(current or "", row["password_hash"]):
        throttle.fail(key)
        raise AuthError("Текущий пароль указан неверно.", 401)
    throttle.reset(key)
    validate_password(password)
    store.set_password(user.id, password)
    updated = store.by_id(user.id)
    assert updated is not None
    return updated


def _confirm_identity(user: User, password: str | None, code: str | None) -> None:
    """Подтверждаем опасное действие: пароль (если он вообще есть) и код 2FA (если включена)."""
    row = store.raw(user.id)
    if row is None:
        raise AuthError("Пользователь не найден.", 404)
    key = f"confirm:{user.id}"
    throttle.check(key)
    if row["password_hash"] and not verify_password(password or "", row["password_hash"]):
        throttle.fail(key)
        raise AuthError("Пароль указан неверно.", 401)
    if row["totp_enabled"] and not check_second_factor(row, code or ""):
        throttle.fail(key)
        raise AuthError("Нужен действующий код из приложения-аутентификатора.", 401)
    throttle.reset(key)


def delete_account(user: User, password: str | None, code: str | None) -> None:
    _confirm_identity(user, password, code)
    store.delete(user.id)
    logger.info("account %s deleted by owner", user.id)


def export_data(user: User) -> dict:
    """Всё, что мы храним про пользователя (кроме хеша пароля и секрета 2FA)."""
    return {
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "user": user.public(),
        "history": store.history(user.id, limit=10_000),
        "note": "Пароль хранится только в виде хеша scrypt; секрет 2FA и коды восстановления в выгрузку не входят.",
    }


# --- демо-аккаунт для жюри ------------------------------------------------
# Включаем переменной UMIR_DEMO_ACCOUNT (1 или нужный логин). Заходим без пароля,
# одной кнопкой на экране входа. Вешать на него 2FA, ставить пароль, привязывать
# Яндекс ID и удалять его нельзя — иначе он «сломается» для всех следующих гостей.
DEMO_NAME = "Гость жюри"


def demo_login() -> str | None:
    raw = os.environ.get("UMIR_DEMO_ACCOUNT", "").strip()
    if not raw or raw.lower() in {"0", "false", "no", "off"}:
        return None
    return "demo" if raw.lower() in {"1", "true", "yes", "on"} else raw


def is_demo(user: User) -> bool:
    login = demo_login()
    return bool(login and user.login and user.login.lower() == login.lower())


def demo_session() -> dict:
    login = demo_login()
    if login is None:
        raise AuthError("Демо-аккаунт на этом сервере выключен.", 404)
    row = store.by_identifier(login)
    if row is None:
        user = store.create(login=login, email=None, name=DEMO_NAME, password=None)
    else:
        user = store.by_id(row["id"])
        assert user is not None
    return session_payload(user)


def link_yandex(user_id: int, yandex_id: str) -> User:
    other = store.by_yandex(yandex_id)
    if other is not None and other.id != user_id:
        raise AuthError("Этот Яндекс ID уже привязан к другому аккаунту.", 409)
    store.update(user_id, yandex_id=yandex_id)
    updated = store.by_id(user_id)
    if updated is None:
        raise AuthError("Пользователь не найден.", 404)
    return updated


def unlink_yandex(user: User) -> User:
    if not user.has_password:
        raise AuthError("Сначала задайте пароль: иначе после отвязки войти будет нельзя.", 409)
    store.update(user.id, yandex_id=None)
    updated = store.by_id(user.id)
    assert updated is not None
    return updated
