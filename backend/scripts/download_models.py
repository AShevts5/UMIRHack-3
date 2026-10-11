"""Проверяем и качаем веса OCR, чтобы демо не легло без интернета.

Вся наша «модель» — это языковые файлы Tesseract ``rus`` и ``eng``. Метрики на
открытом наборе мы снимали на ``tessdata_fast`` версии 4.1.0: ровно эти файлы ставит
пакет ``tesseract-ocr-rus`` в Debian/Ubuntu и в Docker-образе. Скрипт сверяет
установленные файлы с этой версией по SHA-256 и, если надо, докачивает их в
``models/tessdata`` — сервис сам найдёт эту папку (см. ``config._default_tessdata``).

    python -m backend.scripts.download_models            # проверить и докачать недостающее
    python -m backend.scripts.download_models --check    # только проверить, ничего не качать
    python -m backend.scripts.download_models --force    # скачать заново в models/tessdata

Внешних API тут нет: файлы качаются один раз, а распознаём мы всё локально.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

from ..app.config import PROJECT_ROOT, _default_tessdata, find_tesseract_binary

VERSION = "4.1.0"
URL = "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/{version}/{lang}.traineddata"
MODELS = {
    "rus": "e16e5e036cce1d9ec2b00063cf8b54472625b9e14d893a169e2b0dedeb4df225",
    "eng": "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2",
}
DEFAULT_TARGET = PROJECT_ROOT / "models" / "tessdata"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect(folder: Path | None) -> dict[str, str]:
    """По каждому языку: ``ok`` (та же версия), ``other`` (веса другие) или ``missing``."""
    state = {}
    for lang, expected in MODELS.items():
        path = folder / f"{lang}.traineddata" if folder else None
        if path is None or not path.is_file():
            state[lang] = "missing"
        else:
            state[lang] = "ok" if sha256(path) == expected else "other"
    return state


def download(lang: str, target: Path) -> Path:
    target.mkdir(parents=True, exist_ok=True)
    url = URL.format(version=VERSION, lang=lang)
    with tempfile.NamedTemporaryFile(dir=target, delete=False, suffix=".part") as tmp:
        with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 - фиксированный https-адрес
            shutil.copyfileobj(response, tmp)
    part = Path(tmp.name)
    actual = sha256(part)
    if actual != MODELS[lang]:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"{lang}: контрольная сумма не совпала ({actual[:12]}…), файл не сохранён")
    final = target / f"{lang}.traineddata"
    part.replace(final)
    final.chmod(0o644)
    return final


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="только проверить")
    parser.add_argument("--force", action="store_true", help="скачать в --target, даже если всё уже есть")
    parser.add_argument("--target", default=str(DEFAULT_TARGET))
    args = parser.parse_args(argv)

    binary = find_tesseract_binary()
    print(f"Tesseract: {binary or 'не найден — установите его (см. README, раздел про установку)'}")
    found = _default_tessdata()
    state = inspect(found)
    print(f"tessdata: {found or 'не найдена'}")
    for lang, status in state.items():
        label = {"ok": f"tessdata_fast {VERSION}, как при замерах", "other": "другие веса: метрики могут отличаться",
                 "missing": "нет"}[status]
        print(f"  {lang}: {label}")

    target = Path(args.target)
    need = [lang for lang, status in state.items() if status == "missing"] if not args.force else list(MODELS)
    if args.check or not need:
        if not need:
            print("Скачивать ничего не нужно.")
        ok = binary is not None and all(status != "missing" for status in state.values())
        return 0 if ok else 1

    for lang in need:
        print(f"Скачиваю {lang}.traineddata ({VERSION}) в {target} …")
        try:
            download(lang, target)
        except Exception as exc:  # noqa: BLE001 - сообщаем и выходим с ошибкой
            print(f"  не получилось: {exc}", file=sys.stderr)
            return 1
    # eng может уже стоять в системе, но Tesseract берёт все языки из одной папки, так что качаем оба
    for lang in MODELS:
        if not (target / f"{lang}.traineddata").is_file() and found and (found / f"{lang}.traineddata").is_file():
            shutil.copyfile(found / f"{lang}.traineddata", target / f"{lang}.traineddata")
    if target.resolve() == DEFAULT_TARGET.resolve():
        print(f"Готово. Сервис найдёт эту папку сам, если в системе нет своей tessdata с rus: {target}")
    else:
        print(f"Готово. Укажите сервису папку: TESSDATA_PREFIX={target}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
