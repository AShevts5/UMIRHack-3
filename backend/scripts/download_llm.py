"""Скачивает веса мини-LLM для AI-помощника в 'models/llm'.

Модель — Qwen2.5-1.5B-Instruct в формате GGUF с квантованием Q4_K_M (около 1 ГБ,
лицензия Apache 2.0). Хорошо понимает русский и быстро работает на обычном CPU.
Файл качается один раз, дальше модель работает полностью локально через llama.cpp.

"""

from __future__ import annotations

import argparse
import shutil
import sys
import urllib.request
from pathlib import Path

from ..app.config import PROJECT_ROOT

REPO = "Qwen/Qwen2.5-1.5B-Instruct-GGUF"
FILENAME = "qwen2.5-1.5b-instruct-q4_k_m.gguf"
URL = f"https://huggingface.co/{REPO}/resolve/main/{FILENAME}"
DEFAULT_TARGET = PROJECT_ROOT / "models" / "llm"


def download(target: Path) -> Path:
    target.mkdir(parents=True, exist_ok=True)
    final = target / FILENAME
    partial = final.with_suffix(".part")
    print(f"Качаю {URL}")
    with urllib.request.urlopen(URL) as response, partial.open("wb") as out:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        while chunk := response.read(1 << 20):
            out.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r  {done / total:6.1%}  ({done >> 20} из {total >> 20} МБ)", end="", flush=True)
    print()
    shutil.move(partial, final)
    return final


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="скачать заново, даже если файл уже есть")
    parser.add_argument("--target", default=str(DEFAULT_TARGET))
    args = parser.parse_args(argv)

    path = Path(args.target) / FILENAME
    if path.is_file() and not args.force:
        print(f"Модель уже на месте: {path}")
        return 0
    path = download(Path(args.target))
    print(f"Готово: {path} ({path.stat().st_size >> 20} МБ)")
    return 0


if __name__ == "__main__":
    sys.exit(main())