"""Мини-LLM для AI-помощника: Qwen2.5-1.5B-Instruct (GGUF) через llama.cpp, всё локально.

Модель качается один раз командой ``python -m backend.scripts.download_llm`` в
``models/llm``. Нет файла, нет пакета llama-cpp-python или выключено через
``UMIR_LLM=0`` — помощник молча отвечает как раньше, найденным фрагментом.

Настройки через env:
  UMIR_LLM=0             выключить генерацию совсем
  UMIR_LLM_MODEL=путь    свой .gguf вместо стандартного
  UMIR_LLM_TIMEOUT=25    сколько секунд даём на один ответ, дальше бросаем и отвечаем без модели
  UMIR_LLM_THREADS=4     потоки CPU (по умолчанию все ядра)
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

from ..config import PROJECT_ROOT

logger = logging.getLogger(__name__)

FILENAME = "qwen2.5-1.5b-instruct-q4_k_m.gguf"
MODEL_PATH = Path(os.environ.get("UMIR_LLM_MODEL") or PROJECT_ROOT / "models" / "llm" / FILENAME)
TIMEOUT = float(os.environ.get("UMIR_LLM_TIMEOUT", "25"))
MAX_TOKENS = 220

_model = None
_broken = False  # модель не загрузилась один раз — больше не пробуем, чтобы не тормозить каждый вопрос
_lock = threading.Lock()


def _switched_off() -> bool:
    return os.environ.get("UMIR_LLM", "1").lower() in {"0", "false", "no", "off"}


def _package_installed() -> bool:
    try:
        import llama_cpp  # noqa: F401
    except Exception:
        return False
    return True


def enabled() -> bool:
    return not _switched_off() and not _broken and MODEL_PATH.is_file() and _package_installed()


def status() -> dict:
    """Для /api/v1/health: видно, работает ли генерация и почему нет."""
    if _switched_off():
        state = "off"
    elif not MODEL_PATH.is_file():
        state = "no-model"
    elif not _package_installed():
        state = "no-package"
    elif _broken:
        state = "error"
    else:
        state = "ready" if _model is not None else "available"
    return {"state": state, "model": MODEL_PATH.name, "enabled": state in {"ready", "available"}}


def _load():
    global _model, _broken
    if _model is None:
        from llama_cpp import Llama

        threads = int(os.environ.get("UMIR_LLM_THREADS") or os.cpu_count() or 4)
        started = time.monotonic()
        try:
            _model = Llama(model_path=str(MODEL_PATH), n_ctx=4096, n_threads=threads, verbose=False)
        except Exception:
            _broken = True
            raise
        logger.info("LLM загружена за %.1f с (%s, потоков %d)", time.monotonic() - started, MODEL_PATH.name, threads)
    return _model


def warm_up() -> None:
    """Грузим модель заранее в фоне, чтобы первый вопрос не ждал лишние секунды."""
    if not enabled():
        return

    def _run() -> None:
        try:
            with _lock:
                _load()
        except Exception as exc:
            logger.warning("LLM не загрузилась: %s", exc)

    threading.Thread(target=_run, name="llm-warm-up", daemon=True).start()


def chat(messages: list[dict]) -> str | None:
    """Ответ модели или None, если модели нет, она упала или не уложилась в TIMEOUT."""
    if not enabled():
        return None
    try:
        with _lock:
            model = _load()
            deadline = time.monotonic() + TIMEOUT
            parts: list[str] = []
            finish = None
            # идём потоком, чтобы можно было бросить генерацию, если она затянулась
            for chunk in model.create_chat_completion(
                messages=messages, temperature=0.2, max_tokens=MAX_TOKENS, stream=True
            ):
                choice = chunk["choices"][0]
                parts.append(choice.get("delta", {}).get("content") or "")
                finish = choice.get("finish_reason") or finish
                if time.monotonic() > deadline:
                    logger.info("LLM не уложилась в %.0f с, отвечаем фрагментом", TIMEOUT)
                    return None
        if finish == "length":
            # обрезанный на полуслове ответ хуже, чем готовый фрагмент из справки
            return None
        return "".join(parts).strip() or None
    except Exception as exc:
        logger.warning("LLM не сработала: %s", exc)
        return None
