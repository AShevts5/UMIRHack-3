"""Тесты путеводителя по вузу: учётки, 2FA, реестр вузов, помощник."""

from __future__ import annotations


import pytest
from fastapi.testclient import TestClient

from backend.app.services import auth, universities
from backend.app.services import assistant


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("UMIR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UMIR_JWT_SECRET", "test-secret-" + "x" * 32)
    monkeypatch.setattr(auth, "store", auth.AuthStore(tmp_path / "users.sqlite3"))
    monkeypatch.setattr(auth, "throttle", auth._Throttle())
    from backend.app.main import app

    return TestClient(app)


def _register(client, login="student1", password="password123"):
    response = client.post("/api/auth/register", json={"login": login, "password": password, "name": "Студент"})
    assert response.status_code == 200, response.text
    return response.json()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_register_login_and_me(client):
    session = _register(client)
    assert session["user"]["login"] == "student1"
    me = client.get("/api/auth/me", headers=_auth(session["access_token"]))
    assert me.status_code == 200 and me.json()["user"]["two_factor_enabled"] is False

    login = client.post("/api/auth/login", json={"login": "STUDENT1", "password": "password123"})
    assert login.status_code == 200 and "access_token" in login.json()

    bad = client.post("/api/auth/login", json={"login": "student1", "password": "wrong-pass"})
    assert bad.status_code == 401


def test_register_rejects_duplicates_and_weak_passwords(client):
    _register(client)
    assert client.post("/api/auth/register", json={"login": "student1", "password": "password123"}).status_code == 409
    assert client.post("/api/auth/register", json={"login": "other", "password": "short"}).status_code == 422
    assert client.post("/api/auth/register", json={"password": "password123"}).status_code == 422


def test_password_is_hashed(client):
    _register(client)
    row = auth.store.by_identifier("student1")
    assert row["password_hash"].startswith("scrypt$") and "password123" not in row["password_hash"]


def test_me_requires_valid_token(client):
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me", headers=_auth("garbage")).status_code == 401
    expired = auth.issue_token(1, ttl=-10)
    assert client.get("/api/auth/me", headers=_auth(expired)).status_code == 401


def test_totp_matches_rfc6238_vector():
    # из RFC 6238, приложение B: секрет «12345678901234567890», SHA1, T=59 -> 94287082 (8 цифр)
    import base64

    secret = base64.b32encode(b"12345678901234567890").decode()
    assert auth.totp_now(secret, at=59).endswith("287082")


def test_two_factor_flow(client):
    session = _register(client)
    headers = _auth(session["access_token"])
    setup = client.post("/api/auth/2fa/setup", headers=headers).json()
    assert setup["otpauth_url"].startswith("otpauth://totp/") and "<svg" in setup["qr_svg"]
    assert client.post("/api/auth/2fa/enable", headers=headers, json={"code": "000000"}).status_code == 422
    code = auth.totp_now(setup["secret"])
    assert client.post("/api/auth/2fa/enable", headers=headers, json={"code": code}).json()["user"]["two_factor_enabled"]

    step = client.post("/api/auth/login", json={"login": "student1", "password": "password123"}).json()
    assert step["two_factor_required"] is True and "access_token" not in step
    assert client.post("/api/auth/2fa/login", json={"challenge": step["challenge"], "code": "123456"}).status_code == 401
    ok = client.post("/api/auth/2fa/login", json={"challenge": step["challenge"], "code": auth.totp_now(setup["secret"])})
    assert ok.status_code == 200 and ok.json()["access_token"]
    # challenge не должен прокатывать как обычный токен доступа
    assert client.get("/api/auth/me", headers=_auth(step["challenge"])).status_code == 401


def test_password_reset_token_is_single_use(client, caplog):
    _register(client)
    response = client.post("/api/auth/password/forgot", json={"login": "student1"})
    assert response.status_code == 200 and response.json()["sent"] is False
    link = auth.request_password_reset("student1", "http://test")
    token = link.split("token=", 1)[1]
    assert client.post("/api/auth/password/reset", json={"token": token, "password": "newpassword1"}).status_code == 200
    assert client.post("/api/auth/password/reset", json={"token": token, "password": "another-pass"}).status_code == 401
    assert client.post("/api/auth/login", json={"login": "student1", "password": "newpassword1"}).status_code == 200


def test_yandex_login_without_keys_is_honest(client, monkeypatch):
    monkeypatch.delenv("UMIR_YANDEX_CLIENT_ID", raising=False)
    monkeypatch.delenv("UMIR_YANDEX_CLIENT_SECRET", raising=False)
    assert client.get("/api/auth/providers").json()["yandex"]["configured"] is False
    response = client.get("/api/auth/yandex/login", follow_redirects=False)
    assert response.status_code in (302, 307) and response.headers["location"].startswith("/#/login?error=")


def test_yandex_login_redirects_to_oauth_with_state(client, monkeypatch):
    monkeypatch.setenv("UMIR_YANDEX_CLIENT_ID", "cid")
    monkeypatch.setenv("UMIR_YANDEX_CLIENT_SECRET", "secret")
    response = client.get("/api/auth/yandex/login", follow_redirects=False)
    location = response.headers["location"]
    assert location.startswith("https://oauth.yandex.ru/authorize?") and "client_id=cid" in location
    assert "redirect_uri=http%3A%2F%2Ftestserver%2Fapi%2Fauth%2Fyandex%2Fcallback" in location
    # без правильного state колбэк никого не пускает
    bad = client.get("/api/auth/yandex/callback?code=x&state=wrong", follow_redirects=False)
    assert "error=" in bad.headers["location"]


def test_yandex_callback_creates_user(client, monkeypatch):
    from backend.app.services import oauth_yandex

    monkeypatch.setenv("UMIR_YANDEX_CLIENT_ID", "cid")
    monkeypatch.setenv("UMIR_YANDEX_CLIENT_SECRET", "secret")
    monkeypatch.setattr(oauth_yandex, "exchange_code", lambda code, base: "token")
    monkeypatch.setattr(
        oauth_yandex, "fetch_profile", lambda token: {"id": "42", "login": "ya", "email": "ya@yandex.ru", "name": "Яна"}
    )
    start = client.get("/api/auth/yandex/login", follow_redirects=False)
    state = start.headers["location"].split("state=", 1)[1].split("&", 1)[0]
    done = client.get(f"/api/auth/yandex/callback?code=abc&state={state}", follow_redirects=False)
    location = done.headers["location"]
    assert location.startswith("/#/auth?token=")
    token = location.split("token=", 1)[1]
    user = client.get("/api/auth/me", headers=_auth(token)).json()["user"]
    assert user["yandex_linked"] and user["email"] == "ya@yandex.ru"


def test_universities_registry(client):
    items = client.get("/api/universities").json()["items"]
    ids = {item["id"] for item in items}
    assert {"rinh", "sfedu"} <= ids
    rinh = client.get("/api/universities/rinh").json()
    assert rinh["sections"]["dean"] == "available" and rinh["regulation_available"]
    assert rinh["hall"]["building_image"].endswith(".svg")
    assert "_regulation_path" not in rinh
    sfedu = client.get("/api/universities/sfedu").json()
    # своего положения у ЮФУ пока нет, проверка идёт по учебному Положению кейса, как у РГЭУ
    assert sfedu["sections"]["dean"] == "available" and sfedu["regulation_available"]
    assert sfedu["regulation_note"]
    assert client.get("/api/universities/nope").status_code == 404


def test_faculty_records_have_sources():
    for university in universities.all_universities():
        assert university["faculties"], university["id"]
        for faculty in university["faculties"]:
            assert faculty.get("sources"), f"{university['id']}:{faculty['id']} без источника"
            for url in faculty["sources"]:
                assert url.startswith("https://") and (
                    "rsue.ru" in url or "sfedu.ru" in url
                ), f"{faculty['id']}: источник не с официального сайта: {url}"


def test_university_choice_saved_in_profile(client):
    session = _register(client)
    headers = _auth(session["access_token"])
    updated = client.patch("/api/auth/me", headers=headers, json={"university_id": "sfedu"}).json()["user"]
    assert updated["university_id"] == "sfedu"
    assert client.patch("/api/auth/me", headers=headers, json={"university_id": "nope"}).status_code == 422


def test_app_config_for_footer(client):
    config = client.get("/api/app-config").json()
    assert config["year"] == 2026 and config["team_name"]
    assert config["assistant"]["greeting"] == "Приветствую Повелителя своего! Чем могу быть полезен?"


@pytest.mark.parametrize(
    ("question", "kind"),
    [
        ("Как включить двухфакторную аутентификацию?", "faq"),
        ("Где деканат юридического факультета?", "faculty"),
        ("Какие поля обязательны в справке о практике по Положению?", "rule"),
        ("Как выбрать свой вуз на карте?", "faq"),
    ],
)
def test_assistant_finds_local_answer(question, kind):
    assert assistant.answer(question, "rinh")["kind"] == kind


def test_assistant_refuses_honestly():
    reply = assistant.answer("Какая погода будет завтра в Москве?")
    assert reply["kind"] == "none" and "ничего не придумываю" in reply["answer"]


def test_assistant_endpoint(client):
    response = client.post("/api/assistant/ask", json={"question": "Где деканат юридического факультета?", "university_id": "rinh"})
    body = response.json()
    assert response.status_code == 200 and body["kind"] == "faculty" and body["sources"]


def test_history_requires_login(client):
    assert client.get("/api/me/history").status_code == 401
    session = _register(client)
    assert client.get("/api/me/history", headers=_auth(session["access_token"])).json() == {"items": []}
    auth.store.add_history(session["user"]["id"], filename="a.png", document_type="study_certificate", complete=False, missing="seal_present")
    items = client.get("/api/me/history", headers=_auth(session["access_token"])).json()["items"]
    assert items[0]["missing"] == ["seal_present"] and items[0]["complete"] is False
    assert items[0]["missing_labels"] and items[0]["missing_labels"][0] != "seal_present"


def test_throttle_blocks_bruteforce(client):
    _register(client)
    for _ in range(8):
        client.post("/api/auth/login", json={"login": "student1", "password": "bad-password"})
    assert client.post("/api/auth/login", json={"login": "student1", "password": "password123"}).status_code == 429


def test_assistant_related_suggestions_stay_in_one_university():
    reply = assistant.answer("Где деканат юридического факультета?", "rinh")
    titles = {f["name"] for u in universities.all_universities() if u["id"] != "rinh" for f in u["faculties"]}
    assert not set(reply["suggestions"]) & titles


def test_change_password_requires_current(client):
    session = _register(client)
    headers = _auth(session["access_token"])
    wrong = client.post("/api/auth/password/change", headers=headers, json={"current_password": "nope-nope", "password": "newpassword1"})
    assert wrong.status_code == 401
    assert client.post("/api/auth/password/change", headers=headers, json={"current_password": "password123", "password": "short"}).status_code == 422
    ok = client.post("/api/auth/password/change", headers=headers, json={"current_password": "password123", "password": "newpassword1"})
    assert ok.status_code == 200
    assert client.post("/api/auth/login", json={"login": "student1", "password": "password123"}).status_code == 401
    assert client.post("/api/auth/login", json={"login": "student1", "password": "newpassword1"}).status_code == 200
    assert client.post("/api/auth/password/change", json={"password": "newpassword2"}).status_code == 401


def test_yandex_only_user_can_set_password(client):
    user = auth.store.create(login="yauser", email=None, name="Яна", password=None, yandex_id="77")
    headers = _auth(auth.issue_token(user.id))
    assert client.get("/api/auth/me", headers=headers).json()["user"]["has_password"] is False
    ok = client.post("/api/auth/password/change", headers=headers, json={"password": "fromyandex1"})
    assert ok.status_code == 200 and ok.json()["user"]["has_password"] is True
    assert client.post("/api/auth/login", json={"login": "yauser", "password": "fromyandex1"}).status_code == 200


def test_clear_history_and_export(client):
    session = _register(client)
    headers = _auth(session["access_token"])
    auth.store.add_history(session["user"]["id"], filename="a.png", document_type="study_certificate", complete=True)
    data = client.get("/api/me/export", headers=headers)
    assert data.status_code == 200 and "attachment" in data.headers["content-disposition"]
    body = data.json()
    assert body["user"]["login"] == "student1" and len(body["history"]) == 1
    assert "password_hash" not in data.text and "totp" not in data.text.replace("two_factor", "")
    assert client.delete("/api/me/history", headers=headers).json() == {"deleted": 1}
    assert client.get("/api/me/history", headers=headers).json() == {"items": []}


def test_delete_account_requires_password_and_code(client):
    session = _register(client)
    headers = _auth(session["access_token"])
    setup = client.post("/api/auth/2fa/setup", headers=headers).json()
    client.post("/api/auth/2fa/enable", headers=headers, json={"code": auth.totp_now(setup["secret"])})
    auth.store.add_history(session["user"]["id"], filename="a.png")
    assert client.post("/api/auth/me/delete", headers=headers, json={"password": "wrong-pass"}).status_code == 401
    assert client.post("/api/auth/me/delete", headers=headers, json={"password": "password123"}).status_code == 401
    ok = client.post(
        "/api/auth/me/delete", headers=headers, json={"password": "password123", "code": auth.totp_now(setup["secret"])}
    )
    assert ok.status_code == 200
    assert client.get("/api/auth/me", headers=headers).status_code == 401
    assert auth.store.history(session["user"]["id"]) == []
    assert client.post("/api/auth/login", json={"login": "student1", "password": "password123"}).status_code == 401


def test_link_yandex_to_existing_account(client, monkeypatch):
    from backend.app.services import oauth_yandex

    monkeypatch.setenv("UMIR_YANDEX_CLIENT_ID", "cid")
    monkeypatch.setenv("UMIR_YANDEX_CLIENT_SECRET", "secret")
    monkeypatch.setattr(oauth_yandex, "exchange_code", lambda code, base: "token")
    monkeypatch.setattr(oauth_yandex, "fetch_profile", lambda token: {"id": "99", "login": "ya", "email": None, "name": "Яна"})
    session = _register(client)
    headers = _auth(session["access_token"])
    url = client.post("/api/auth/yandex/link", headers=headers).json()["url"]
    start = client.get(url, follow_redirects=False)
    state = start.headers["location"].split("state=", 1)[1].split("&", 1)[0]
    done = client.get(f"/api/auth/yandex/callback?code=abc&state={state}", follow_redirects=False)
    assert done.headers["location"] == "/#/profile?linked=1"
    me = client.get("/api/auth/me", headers=headers).json()["user"]
    assert me["yandex_linked"] is True
    # второй аккаунт не должен утащить тот же Яндекс ID
    other = _register(client, login="student2")
    url2 = client.post("/api/auth/yandex/link", headers=_auth(other["access_token"])).json()["url"]
    start2 = client.get(url2, follow_redirects=False)
    state2 = start2.headers["location"].split("state=", 1)[1].split("&", 1)[0]
    clash = client.get(f"/api/auth/yandex/callback?code=abc&state={state2}", follow_redirects=False)
    assert clash.headers["location"].startswith("/#/profile?error=")
    # после привязки обычный вход через Яндекс кидает в первый аккаунт
    start3 = client.get("/api/auth/yandex/login", follow_redirects=False)
    state3 = start3.headers["location"].split("state=", 1)[1].split("&", 1)[0]
    login = client.get(f"/api/auth/yandex/callback?code=abc&state={state3}", follow_redirects=False)
    token = login.headers["location"].split("token=", 1)[1]
    assert client.get("/api/auth/me", headers=_auth(token)).json()["user"]["login"] == "student1"
    # пароль есть — значит, отвязать можно
    assert client.post("/api/auth/yandex/unlink", headers=headers).json()["user"]["yandex_linked"] is False


def test_unlink_yandex_needs_password(client):
    user = auth.store.create(login=None, email=None, name="Яна", password=None, yandex_id="55")
    assert client.post("/api/auth/yandex/unlink", headers=_auth(auth.issue_token(user.id))).status_code == 409


def test_expired_session_is_distinguishable_from_wrong_password(client):
    session = _register(client)
    expired = client.get("/api/auth/me", headers=_auth(auth.issue_token(session["user"]["id"], ttl=-10)))
    assert expired.status_code == 401 and "invalid_token" in expired.headers["www-authenticate"]
    wrong = client.post(
        "/api/auth/password/change",
        headers=_auth(session["access_token"]),
        json={"current_password": "nope-nope", "password": "newpassword1"},
    )
    assert wrong.status_code == 401 and "www-authenticate" not in wrong.headers


def test_security_headers(client):
    response = client.get("/api/app-config")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "microphone=(self)" in response.headers["permissions-policy"]


def test_faculty_source_checker_offline(monkeypatch):
    import base64

    from backend.scripts import check_faculty_sources as checker

    hidden = base64.b64encode(b"var x='<a href=\"mailto:dean@sfedu.ru\">dean@sfedu.ru</a>';").decode()
    page = (
        f"<p>Деканат, ауд. 213, тел. 8(863)2673967</p><script>var addy1='{hidden[:20]}\\\n{hidden[20:]}';</script>"
        "<p>Декан Иванов Иван Иванович</p>"
    )
    monkeypatch.setattr(checker, "_fetch", lambda url, timeout: page)
    checker._cache.clear()
    faculty = {
        "id": "x", "name": "Тест", "room": "213", "dean": "Иванов Иван Иванович",
        "phone": "+7 (863) 267-39-67", "email": "dean@sfedu.ru", "sources": ["https://sfedu.ru/x"],
    }
    assert all(checker.check_faculty(faculty)["checks"].values())
    faculty["email"] = "other@sfedu.ru"
    checker._cache.clear()
    assert checker.check_faculty(faculty)["checks"]["email other@sfedu.ru"] is False
