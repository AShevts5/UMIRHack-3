"""Проверки качества и целостности, которым веб-сервер не нужен.

Гоняем пять независимых проверок по открытому архиву v1.0 и печатаем JSON-отчёт
(``--report`` заодно сохраняет его в файл):

``integrity``
    Копируем каждый документ под случайным именем *и* заново нумеруем
    ``document_id`` (``anon-0001`` …), после чего повторяем batch-прогон. Предсказания
    должны совпасть поле в поле — значит, никакое значение не подтягивается по имени
    файла, пути или id. Сравниваем с экспортированным ``results.json``, а не с
    состоянием в памяти.

``representations``
    Прогоняем PNG и PDF одной и той же страницы и сверяем поля: это два
    представления одного документа, расходиться они не должны.

``auto_type``
    Запускаем pipeline с ``document_type=auto`` и смотрим, определился ли тип.

``markers``
    Сверяем ``signature_present`` / ``seal_present`` с эталоном — так видны ложные
    срабатывания на пустой линии и на одиноком «М.П.».

``limits``
    Подсовываем битый файл, неподдерживаемое расширение и слишком большой payload:
    ждём понятную ошибку, а не traceback.

Usage
-----
.. code-block:: bash

    python -m backend.scripts.verify --root ../online-dean-participants-v1 --report verify.json
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import string
import sys
import tempfile
import time
from pathlib import Path

from ..app.schemas import FIELD_KEYS
from ..app.services.loader import DocumentLoadError, load_document_bytes
from ..app.services.pipeline import Pipeline
from .run_batch import group_questions, resolve_document_path, run_batch


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _predictions_by_id(results_path: Path) -> dict:
    payload = _read(results_path)
    return {item["document_id"]: item for item in payload["documents"]}


# ---------------------------------------------------------------------------
# 1. Целостность: переименованные файлы и новые id
# ---------------------------------------------------------------------------


def check_integrity(root: Path, documents: list[dict], questions: list[dict], workdir: Path) -> dict:
    random_gen = random.Random(20261011)
    anon_dir = workdir / "anon"
    anon_dir.mkdir(parents=True, exist_ok=True)

    anon_documents = []
    id_map: dict[str, str] = {}
    for index, entry in enumerate(documents):
        source = resolve_document_path(entry, root)
        # Копия под случайным именем: от оригинала не остаётся ни имени, ни id.
        stem = "".join(random_gen.choice(string.ascii_lowercase + string.digits) for _ in range(16))
        target = anon_dir / f"{stem}{source.suffix}"
        shutil.copyfile(source, target)
        new_id = f"anon-{index + 1:04d}"
        id_map[entry["document_id"]] = new_id
        anon_documents.append(
            {
                "document_id": new_id,
                "document_type": entry["document_type"],
                "image": target.name,
                "pdf": target.name,
            }
        )

    anon_questions = [
        {"question_id": f"{id_map[item['document_id']]}-Q{number + 1}", "document_id": id_map[item["document_id"]], "question": item["question"]}
        for item in questions
        for number in [int(item["question_id"].rsplit("Q", 1)[1]) - 1]
    ]

    documents_path = workdir / "anon_documents.json"
    questions_path = workdir / "anon_questions.json"
    documents_path.write_text(json.dumps(anon_documents, ensure_ascii=False), encoding="utf-8")
    questions_path.write_text(json.dumps(anon_questions, ensure_ascii=False), encoding="utf-8")

    output = workdir / "anon_results.json"
    summary = run_batch(documents_path, questions_path, anon_dir, output, verbose=False)

    baseline = Pipeline(use_cache=False)
    failures: list[dict] = []
    compared_fields = 0
    compared_answers = 0
    anon_predictions = _predictions_by_id(output)
    grouped = group_questions(anon_questions)

    for entry in documents:
        document_type = entry["document_type"]
        source = resolve_document_path(entry, root)
        reference_result = baseline.analyze_path(source, document_type, document_id=entry["document_id"])
        reference_answers = reference_result.answers(grouped[id_map[entry["document_id"]]])
        anon = anon_predictions.get(id_map[entry["document_id"]])
        if anon is None:
            failures.append({"document": entry["document_id"], "error": "нет предсказания"})
            continue
        for key, value in reference_result.fields.items():
            compared_fields += 1
            if anon["fields"][key] != value:
                failures.append(
                    {
                        "document": entry["document_id"],
                        "field": key,
                        "expected": value,
                        "actual": anon["fields"][key],
                    }
                )
        if anon["validation"] != reference_result.validation.as_dict():
            failures.append({"document": entry["document_id"], "validation": "mismatch"})
        anon_answers = {item["question_id"]: item for item in anon["answers"]}
        for answer in reference_answers:
            compared_answers += 1
            got = anon_answers.get(answer.question_id)
            if got is None or (
                got["answerable"] != answer.answerable
                or got["value"] != answer.value
                or set(got["sources"]) != set(answer.sources)
            ):
                failures.append({"question": answer.question_id, "answer": "mismatch"})

    return {
        "checked_documents": len(documents),
        "checked_fields": compared_fields,
        "checked_answers": compared_answers,
        "failures": failures,
        "passed": not failures,
        "batch": summary,
        "note": "Файлы переименованы случайно, document_id заменены на anon-NNNN.",
    }


# ---------------------------------------------------------------------------
# 2. PNG и PDF должны давать одно и то же
# ---------------------------------------------------------------------------


def check_representations(root: Path, documents: list[dict], questions: list[dict]) -> dict:
    pipeline = Pipeline(use_cache=False)
    grouped = group_questions(questions)
    mismatches: list[dict] = []
    checked = 0
    for entry in documents:
        image_path = root / entry["image"]
        pdf_path = root / entry["pdf"]
        if not image_path.is_file() or not pdf_path.is_file():
            continue
        checked += 1
        from_image = pipeline.analyze_path(image_path, entry["document_type"], entry["document_id"])
        from_pdf = pipeline.analyze_path(pdf_path, entry["document_type"], entry["document_id"])
        text_keys = [key for key in FIELD_KEYS[entry["document_type"]]]
        for key in text_keys:
            if from_image.fields[key] != from_pdf.fields[key]:
                mismatches.append(
                    {
                        "document": entry["document_id"],
                        "field": key,
                        "png": from_image.fields[key],
                        "pdf": from_pdf.fields[key],
                    }
                )
        if from_image.validation.as_dict() != from_pdf.validation.as_dict():
            mismatches.append(
                {
                    "document": entry["document_id"],
                    "validation": "mismatch",
                    "png": from_image.validation.as_dict(),
                    "pdf": from_pdf.validation.as_dict(),
                }
            )
    return {"checked_documents": checked, "mismatches": mismatches, "passed": not mismatches}


# ---------------------------------------------------------------------------
# 3. Автоопределение типа (бонус)
# ---------------------------------------------------------------------------


def check_auto_type(root: Path, documents: list[dict]) -> dict:
    pipeline = Pipeline(use_cache=False)
    failures: list[dict] = []
    checked = 0
    for entry in documents:
        checked += 1
        path = resolve_document_path(entry, root)
        result = pipeline.analyze_bytes(path.read_bytes(), path.name, "auto")
        if result.document_type != entry["document_type"]:
            failures.append(
                {
                    "document": entry["document_id"],
                    "expected": entry["document_type"],
                    "actual": result.document_type,
                    "score": result.document_type_confidence,
                }
            )
    return {
        "checked_documents": checked,
        "failures": failures,
        "accuracy": round(1 - len(failures) / checked, 4) if checked else None,
        "passed": not failures,
    }


# ---------------------------------------------------------------------------
# 4. Качество определения отметок
# ---------------------------------------------------------------------------


def check_markers(root: Path, documents: list[dict], reference: dict) -> dict:
    pipeline = Pipeline(use_cache=False)
    ref_by_id = {item["document_id"]: item["fields"] for item in reference["documents"]}
    errors: list[dict] = []
    checked = 0
    positives = 0
    for entry in documents:
        expected = ref_by_id.get(entry["document_id"])
        if not expected:
            continue
        path = resolve_document_path(entry, root)
        result = pipeline.analyze_path(path, entry["document_type"], entry["document_id"])
        for key in ("signature_present", "seal_present"):
            if key not in expected:
                continue
            checked += 1
            if expected[key]:
                positives += 1
            actual = result.fields.get(key)
            if actual != expected[key]:
                errors.append(
                    {
                        "document": entry["document_id"],
                        "key": key,
                        "expected": expected[key],
                        "actual": actual,
                        "method": result.marks.get(key).method if result.marks.get(key) else None,
                    }
                )
    return {
        "checked_marks": checked,
        "positive_marks": positives,
        "errors": errors,
        "accuracy": round(1 - len(errors) / checked, 4) if checked else None,
        "passed": not errors,
    }


# ---------------------------------------------------------------------------
# 5. Лимиты входа и контроль исключений
# ---------------------------------------------------------------------------


def check_limits(root: Path) -> dict:
    checks: list[dict] = []
    sample = next((path for path in sorted(root.glob("documents/*.png"))), None)

    def expect_error(name: str, payload: bytes, filename: str) -> None:
        try:
            load_document_bytes(payload, filename)
        except DocumentLoadError as exc:
            checks.append({"case": name, "ok": True, "message": str(exc)})
        except Exception as exc:  # noqa: BLE001
            checks.append({"case": name, "ok": False, "message": f"неожиданное исключение: {type(exc).__name__}"})
        else:
            checks.append({"case": name, "ok": False, "message": "ошибка не была обнаружена"})

    expect_error("пустой файл", b"", "empty.png")
    expect_error("битый PNG", b"\x89PNG\r\n\x1a\n not really a png", "broken.png")
    if sample is not None:
        expect_error("неподдерживаемое расширение", sample.read_bytes(), "document.txt")
        expect_error("слишком большой файл", sample.read_bytes() + b"\x00" * (13 * 1024 * 1024), "big.png")
    # многостраничный PDF тоже должен отбиваться
    try:
        import fitz

        document = fitz.open()
        document.new_page()
        document.new_page()
        pdf_bytes = document.tobytes()
        document.close()
        expect_error("многостраничный PDF", pdf_bytes, "two-pages.pdf")
    except ImportError:  # pragma: no cover
        pass
    return {"cases": checks, "passed": all(case["ok"] for case in checks)}


# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", required=True, help="каталог архива (documents.json, questions.json, documents/)")
    parser.add_argument("--report", help="куда записать JSON-отчёт")
    parser.add_argument("--skip", nargs="*", default=[], help="какие проверки пропустить")
    args = parser.parse_args(argv)

    root = Path(args.root)
    documents = _read(root / "documents.json")
    questions = _read(root / "questions.json")
    reference_path = root / "reference_results.json"
    reference = _read(reference_path) if reference_path.is_file() else {"documents": []}

    report: dict = {"root": str(root), "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    with tempfile.TemporaryDirectory(prefix="umir-verify-") as tmp:
        workdir = Path(tmp)
        if "integrity" not in args.skip:
            report["integrity"] = check_integrity(root, documents, questions, workdir)
        if "representations" not in args.skip:
            report["representations"] = check_representations(root, documents, questions)
        if "auto_type" not in args.skip:
            report["auto_type"] = check_auto_type(root, documents)
        if "markers" not in args.skip:
            report["markers"] = check_markers(root, documents, reference)
        if "limits" not in args.skip:
            report["limits"] = check_limits(root)

    report["passed"] = all(
        value.get("passed", True) for key, value in report.items() if isinstance(value, dict) and "passed" in value
    )
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        Path(args.report).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report["passed"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
