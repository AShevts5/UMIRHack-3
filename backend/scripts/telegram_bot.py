"""Telegram-бот: тонкий клиент к тому же пайплайну, что и веб-морда.

Кидаешь боту фото или файл документа (PNG, JPG, PDF), в подписи можно задать
вопрос. Бот распознаёт документ локально, отвечает текстом и ссылается на пункты
Положения. Дальше сообщения без файла считаем вопросами к последнему
документу в этом чате.

    UMIR_TELEGRAM_TOKEN=123:abc python -m backend.scripts.telegram_bot

Токен берём у @BotFather. Telegram тут просто канал доставки: распознавание,
поля и ответы считаются на этой машине, никаких внешних OCR и LLM. Документы мы не
храним: в памяти лежит только разбор последнего документа в каждом чате
(не больше 200 чатов), чтобы было чем отвечать на уточняющие вопросы.

Лишних зависимостей сверх requirements.txt не тащим: Bot API дёргаем через urllib.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
import urllib.request
from collections import OrderedDict
from typing import Any, Callable

from ..app.services.document_config import DocumentTypeSpec
from ..app.services.pipeline import PipelineResult, pipeline
from ..app.services.question_engine import default_completeness_question

logger = logging.getLogger("umir.telegram")

API = "https://api.telegram.org"
MAX_FILE_BYTES = 20 * 1024 * 1024  # больше Bot API всё равно скачать не даст
MAX_CHATS = 200
SUFFIX_BY_MIME = {"image/png": ".png", "image/jpeg": ".jpg", "application/pdf": ".pdf"}

HELP = (
    "Здравствуйте! Я проверяю студенческие документы по Положению: справку об обучении, "
    "заявление на академический отпуск и справку о практике.\n\n"
    "Пришлите фото или файл документа (PNG, JPG или PDF). В подписи можно сразу задать вопрос, "
    "например «Какая дата выдачи?». Потом можно задавать вопросы обычными сообщениями.\n\n"
    "Проверка предварительная: окончательное решение принимает сотрудник деканата. "
    "Распознавание идёт локально, документы не сохраняются."
)


class TelegramApi:
    """Минималка для Bot API: вызвать метод и скачать файл, больше ничего."""

    def __init__(self, token: str, base: str = API) -> None:
        self._token = token
        self._base = base

    def call(self, method: str, **params: Any) -> Any:
        data = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None}).encode()
        request = urllib.request.Request(f"{self._base}/bot{self._token}/{method}", data=data)
        timeout = int(params.get("timeout") or 0) + 15
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - адрес Bot API
            payload = json.loads(response.read().decode("utf-8"))
        if not payload.get("ok"):
            raise RuntimeError(payload.get("description") or f"{method} failed")
        return payload["result"]

    def download(self, file_path: str) -> bytes:
        url = f"{self._base}/file/bot{self._token}/{file_path}"
        with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 - адрес Bot API
            return response.read()


def format_result(result: PipelineResult, question: str) -> str:
    """Собираем текст ответа: тип, итог проверки по Положению и ответ на вопрос."""
    lines = [f"📄 {result.spec.title}"]
    if result.document_type_source == "auto":
        lines[0] += f" (определено автоматически, {round(result.document_type_confidence * 100)}%)"
    lines.append(result.validation_message())
    if question.strip() != default_completeness_question():
        answer = result.single_answer(question)
        lines += ["", f"❓ {question}", answer.answer]
        if answer.sources:
            lines.append("Источники: " + ", ".join(_source_label(result.spec, item) for item in answer.sources))
    return "\n".join(lines)


def _source_label(spec: DocumentTypeSpec, source: str) -> str:
    kind, _, value = source.partition(":")
    if kind == "rule":
        return f"п. {value} Положения"
    item = spec.field(value) or spec.mark(value)
    return f"поле «{item.label if item else value}»"


class Bot:
    def __init__(self, api: TelegramApi, analyze: Callable[..., PipelineResult] | None = None) -> None:
        self.api = api
        self.analyze = analyze or pipeline.analyze_bytes
        self.last: OrderedDict[int, PipelineResult] = OrderedDict()
        self.offset: int | None = None

    # -- разбираем одно сообщение ---------------------------------------
    def handle(self, message: dict) -> str | None:
        chat_id = message["chat"]["id"]
        text = (message.get("caption") or message.get("text") or "").strip()
        attachment = self._attachment(message)
        if attachment is None:
            if not text or text.startswith("/start") or text.startswith("/help"):
                return HELP
            previous = self.last.get(chat_id)
            if previous is None:
                return "Сначала пришлите фото или файл документа, а потом задавайте вопросы о нём."
            answer = previous.single_answer(text)
            reply = answer.answer
            if answer.sources:
                reply += "\nИсточники: " + ", ".join(_source_label(previous.spec, item) for item in answer.sources)
            return reply

        file_id, filename, size = attachment
        if size and size > MAX_FILE_BYTES:
            return "Файл больше 20 МБ: Telegram не даёт боту его скачать. Сожмите снимок и пришлите снова."
        try:
            info = self.api.call("getFile", file_id=file_id)
            payload = self.api.download(info["file_path"])
            result = self.analyze(payload, filename, "auto")
        except ValueError as exc:  # файл не читается, формат не тот или тип не поняли
            return f"Не получилось проверить документ: {exc}"
        except Exception:  # noqa: BLE001 - сеть или сбой распознавания: отвечаем по-человечески
            logger.exception("telegram: analysis failed")
            return "Сервис проверки сейчас не смог обработать файл. Попробуйте ещё раз чуть позже."
        self._remember(chat_id, result)
        return format_result(result, text or default_completeness_question())

    def _attachment(self, message: dict) -> tuple[str, str, int] | None:
        photos = message.get("photo") or []
        if photos:
            biggest = max(photos, key=lambda item: item.get("file_size") or item.get("width", 0) * item.get("height", 0))
            return biggest["file_id"], "telegram-photo.jpg", int(biggest.get("file_size") or 0)
        document = message.get("document")
        if document:
            suffix = SUFFIX_BY_MIME.get(document.get("mime_type", ""))
            name = document.get("file_name") or f"document{suffix or ''}"
            if suffix and not name.lower().endswith(suffix):
                name += suffix
            return document["file_id"], name, int(document.get("file_size") or 0)
        return None

    def _remember(self, chat_id: int, result: PipelineResult) -> None:
        self.last[chat_id] = result
        self.last.move_to_end(chat_id)
        while len(self.last) > MAX_CHATS:
            self.last.popitem(last=False)

    # -- крутим long polling --------------------------------------------
    def poll_once(self, timeout: int = 30) -> int:
        updates = self.api.call("getUpdates", offset=self.offset, timeout=timeout, allowed_updates='["message"]')
        for update in updates:
            self.offset = update["update_id"] + 1
            message = update.get("message")
            if not message:
                continue
            if self._attachment(message):
                self.api.call("sendChatAction", chat_id=message["chat"]["id"], action="typing")
            reply = self.handle(message)
            if reply:
                self.api.call("sendMessage", chat_id=message["chat"]["id"], text=reply,
                              reply_to_message_id=message.get("message_id"))
        return len(updates)

    def run(self) -> None:
        me = self.api.call("getMe")
        print(f"Бот @{me.get('username')} запущен. Остановить: Ctrl+C.")
        while True:
            try:
                self.poll_once()
            except KeyboardInterrupt:
                raise
            except Exception:  # noqa: BLE001 - обрыв сети не должен ронять бота
                logger.exception("telegram: polling failed, retry in 5 s")
                time.sleep(5)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    token = os.environ.get("UMIR_TELEGRAM_TOKEN", "").strip()
    if not token:
        print("Задайте токен бота: UMIR_TELEGRAM_TOKEN=... (его выдаёт @BotFather).")
        return 2
    try:
        Bot(TelegramApi(token)).run()
    except KeyboardInterrupt:
        print("Остановлено.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
