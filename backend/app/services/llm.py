import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[3]
MODEL_PATH = ROOT / "models" / "llm" / "qwen2.5-1.5b-instruct-q4_k_m.gguf"

_model = None
_lock = threading.Lock()


def enabled() -> bool:
    return MODEL_PATH.is_file()


def _load():
    global _model
    if _model is None:
        from llama_cpp import Llama
        _model = Llama(model_path=str(MODEL_PATH), n_ctx=4096, verbose=False)
    return _model


def chat(messages: list[dict]) -> str | None:
    try:
        with _lock:
            out = _load().create_chat_completion(
                messages=messages,
                temperature=0.2,
                max_tokens=300,
            )
        return out["choices"][0]["message"]["content"].strip() or None
    except Exception as exc:
        logger.warning("LLM не сработала: %s", exc)
        return None