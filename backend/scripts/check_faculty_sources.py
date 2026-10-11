"""Сверяем справку по факультетам с официальными страницами-источниками.

Для каждой записи в ``configs/universities/*.yaml`` качаем страницы из ``sources``
(и ``page``) и смотрим, что там правда есть указанные email, телефоны,
кабинет и фамилия руководителя. Сами ничего не чиним: печатаем, что сошлось,
а что нет, — расхождения разбирает живой человек (сайт могли и обновить).

Без интернета не работает, поэтому в pytest его не тащим::

    python -m backend.scripts.check_faculty_sources               # все вузы
    python -m backend.scripts.check_faculty_sources --university rinh --report reports/faculty-sources.json
"""

from __future__ import annotations

import argparse
import base64
import binascii
import html
import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from backend.app.services import universities

_UA = "UMIR-online-dean source check (hackathon project)"
_cache: dict[str, str | None] = {}
_context = ssl.create_default_context()


def _decode(raw: bytes, content_type: str) -> str:
    match = re.search(r"charset=([\w-]+)", content_type or "", re.I) or re.search(
        rb'charset=["\']?([\w-]+)', raw[:4000], re.I
    )
    charsets = []
    if match:
        value = match.group(1)
        charsets.append(value.decode() if isinstance(value, bytes) else value)
    charsets += ["utf-8", "cp1251"]
    for charset in charsets:
        try:
            return raw.decode(charset)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", "replace")


def _hidden_emails(source: str) -> list[str]:
    """sfedu.ru прячет адреса от спам-ботов: ``var addy123='<base64>'; eval(Base64.decode(...))``.
    Раскодируем так же, как браузер, — иначе сверка бы этот email просто не увидела."""
    found = []
    for chunk in re.findall(r"var addy\d+\s*=\s*'([A-Za-z0-9+/=\\\s]+)'", source):
        try:
            decoded = base64.b64decode(re.sub(r"[\\\s]", "", chunk)).decode("utf-8", "replace")
        except (ValueError, binascii.Error):
            continue
        found += re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", decoded)
    return found


def _fetch(url: str, timeout: float, attempts: int = 3) -> str:
    # сайты вузов любят рвать соединение — ждём чуть-чуть и пробуем ещё раз
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(request, timeout=timeout, context=_context) as response:
                return _decode(response.read(), response.headers.get("Content-Type", ""))
        except (ConnectionError, TimeoutError, urllib.error.URLError) as exc:
            if attempt == attempts - 1 or "CERTIFICATE_VERIFY_FAILED" in str(exc):
                raise
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("unreachable")


def page_text(url: str, timeout: float = 20.0) -> str | None:
    """Текст страницы без разметки (кешируем на время прогона); ``None`` — не скачалась."""
    if url in _cache:
        return _cache[url]
    try:
        text = _fetch(url, timeout)
    except Exception as exc:  # noqa: BLE001 - сеть: просто помечаем, что не достучались
        hint = ""
        if "CERTIFICATE_VERIFY_FAILED" in str(exc):
            # sfedu.ru (на 11.10.2026) не отдаёт промежуточный сертификат GlobalSign; браузер его
            # сам докачивает, а Python — нет. Так что подсовываем его через --extra-ca.
            hint = " (неполная цепочка сертификатов: см. --extra-ca)"
        print(f"  ! не скачалась {url}: {exc}{hint}", file=sys.stderr)
        _cache[url] = None
        return None
    text += " " + " ".join(_hidden_emails(text))
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", text)
    text = html.unescape(re.sub(r"(?s)<[^>]+>", " ", text))
    _cache[url] = re.sub(r"\s+", " ", text)
    return _cache[url]


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _phones(value: str) -> list[str]:
    # сравниваем по последним 7 цифрам — тогда «+7 (863) 267-39-67» и «8(863)2673967» совпадут
    found = re.findall(r"(?:\+7|8)?[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}", value)
    return [_digits(item)[-7:] for item in found]


def check_faculty(faculty: dict) -> dict:
    urls = list(dict.fromkeys([*(faculty.get("sources") or []), *([faculty["page"]] if faculty.get("page") else [])]))
    texts = [text for text in (page_text(url) for url in urls) if text]
    joined = " ".join(texts)
    digits = _digits(joined)
    lowered = joined.lower()
    result: dict[str, object] = {"id": faculty["id"], "name": faculty["name"], "pages": len(texts), "of": len(urls)}
    checks: dict[str, bool] = {}
    if not texts:
        result["checks"] = checks
        return result
    for email in re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", faculty.get("email") or ""):
        checks[f"email {email}"] = email.lower() in lowered
    for phone in _phones(faculty.get("phone") or ""):
        checks[f"телефон …{phone[-4:]}"] = phone in digits
    if faculty.get("room"):
        room = str(faculty["room"]).strip()
        checks[f"кабинет {room}"] = re.search(rf"(?<![\d]){re.escape(room)}(?![\d])", joined) is not None
    if faculty.get("dean"):
        surname = str(faculty["dean"]).split()[0]
        checks[f"руководитель {surname}"] = surname.lower() in lowered
    result["checks"] = checks
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--university", help="id вуза (по умолчанию все)")
    parser.add_argument("--report", type=Path, help="куда сохранить отчёт JSON")
    parser.add_argument(
        "--extra-ca",
        type=Path,
        help="PEM с промежуточным сертификатом для сайтов с неполной цепочкой; для sfedu.ru: "
        "http://secure.globalsign.com/cacert/gsgccr6alphasslca2025.crt (DER → PEM через openssl x509 -inform DER)",
    )
    args = parser.parse_args(argv)
    if args.extra_ca:
        _context.load_verify_locations(cafile=str(args.extra_ca))

    report = []
    confirmed = total = unreachable = 0
    for university in universities.all_universities():
        if args.university and university["id"] != args.university:
            continue
        print(f"\n{university['short_name']}")
        for faculty in university["faculties"]:
            item = check_faculty(faculty)
            item["university"] = university["id"]
            report.append(item)
            checks: dict = item["checks"]  # type: ignore[assignment]
            if not item["pages"]:
                unreachable += 1
                print(f"  ? {faculty['name']}: источники не открылись")
                continue
            total += len(checks)
            confirmed += sum(checks.values())
            missed = [name for name, ok in checks.items() if not ok]
            mark = "✓" if not missed else "≠"
            tail = f" — не нашли на странице: {', '.join(missed)}" if missed else ""
            print(f"  {mark} {faculty['name']}: {sum(checks.values())}/{len(checks)}{tail}")
    print(f"\nПодтверждено на источниках: {confirmed} из {total} значений; записей без доступных источников: {unreachable}.")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps({"confirmed": confirmed, "total": total, "unreachable": unreachable, "items": report},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return 0 if confirmed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
