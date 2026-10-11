"""Batch flow: ``documents.json`` + ``questions.json`` -> ``results.json``.

Exactly the same pipeline as the web API is used for every document (technical
task, flow B).  The exporter resolves the image/PDF path of each manifest entry,
runs OCR once per document, answers all questions of that ``document_id`` and
writes a ``results.json`` that satisfies ``results.schema.json``.

Example
-------
.. code-block:: bash

    python -m backend.scripts.run_batch \
        --documents /path/to/documents.json \
        --questions /path/to/questions.json \
        --root /path/to/archive \
        --output results.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

from pydantic import ValidationError

from ..app.schemas import DocumentResult, ResultsFile
from ..app.services.document_config import get_registry
from ..app.services.loader import DocumentLoadError
from ..app.services.parse_fields import NOT_FOUND
from ..app.services.pipeline import Pipeline
from ..app.services.question_engine import answer_document
from ..app.services.rules_engine import validate


def _read_json(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def resolve_document_path(entry: dict, root: Path, prefer: str = "image") -> Path:
    """Find the file of a manifest entry.

    ``prefer`` chooses the primary representation (``image`` for PNG/JPG), and the
    other one is used as a fallback so that the batch flow also works with an
    archive that only ships PDFs.
    """
    order = [prefer] + [key for key in ("image", "pdf") if key != prefer]
    candidates: list[Path] = []
    for key in order:
        raw = entry.get(key)
        if not raw:
            continue
        candidate = Path(raw)
        candidates.append(root / candidate)
        candidates.append(root / candidate.name)
        candidates.append(root / "documents" / candidate.name)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    tried = ", ".join(str(item) for item in candidates)
    raise FileNotFoundError(f"Не найден файл документа {entry.get('document_id')}: искали {tried}")


def group_questions(questions: list[dict]) -> dict[str, list[tuple[str, str]]]:
    grouped: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for item in questions:
        grouped[item["document_id"]].append((item["question_id"], item["question"]))
    return grouped


def fallback_payload(document_id: str, document_type: str, questions: list[tuple[str, str]]) -> dict:
    """Schema-valid record for a page that could not be processed at all.

    One unreadable page must not cost the whole results.json: every field is
    «не найдено», the answers come from the same engine (so questions about the
    Положение itself are still answered correctly).
    """
    spec = get_registry().require(document_type)
    fields: dict[str, str | bool] = {field_spec.key: NOT_FOUND for field_spec in spec.fields}
    fields.update({mark_spec.key: False for mark_spec in spec.marks})
    validation = validate(spec, fields)
    answers = answer_document(spec, fields, validation, questions)
    return {
        "document_id": document_id,
        "document_type": document_type,
        "fields": fields,
        "validation": validation.as_dict(),
        "answers": [answer.model_dump() for answer in answers],
    }


def run_batch(
    documents_path: Path,
    questions_path: Path,
    root: Path,
    output_path: Path,
    prefer: str = "image",
    limit: int | None = None,
    verbose: bool = True,
) -> dict:
    documents = _read_json(documents_path)
    questions = _read_json(questions_path)
    if isinstance(documents, dict):
        documents = documents.get("documents", [])
    grouped = group_questions(questions)

    pipeline = Pipeline()
    results: list[DocumentResult] = []
    failures: list[dict] = []
    started = time.time()

    for index, entry in enumerate(documents):
        if limit is not None and index >= limit:
            break
        document_id = entry["document_id"]
        document_type = entry["document_type"]
        try:
            path = resolve_document_path(entry, root, prefer=prefer)
        except FileNotFoundError as exc:
            failures.append({"document_id": document_id, "error": str(exc)})
            if verbose:
                print(f"[{index + 1}] {document_id}: файл не найден", file=sys.stderr)
            results.append(DocumentResult(**fallback_payload(document_id, document_type, grouped.get(document_id, []))))
            continue
        result = None
        for attempt in (1, 2):  # a Tesseract timeout under load is worth one retry
            try:
                result = pipeline.analyze_path(path, document_type, document_id=document_id)
                break
            except Exception as exc:  # noqa: BLE001 - one bad page must not stop the batch
                error = str(exc)
                if isinstance(exc, (DocumentLoadError, ValueError)):
                    break
        if result is None:
            failures.append({"document_id": document_id, "error": error})
            if verbose:
                print(f"[{index + 1}] {document_id}: {error}", file=sys.stderr)
            results.append(DocumentResult(**fallback_payload(document_id, document_type, grouped.get(document_id, []))))
            continue

        answers = result.answers(grouped.get(document_id, []))
        payload = {
            "document_id": document_id,
            "document_type": document_type,
            "fields": result.fields,
            "validation": result.validation.as_dict(),
            "answers": [answer.model_dump() for answer in answers],
        }
        try:
            results.append(DocumentResult(**payload))
        except ValidationError as exc:  # pragma: no cover - contract guard
            failures.append({"document_id": document_id, "error": f"contract: {exc}"})
            if verbose:
                print(f"[{index + 1}] {document_id}: нарушение контракта: {exc}", file=sys.stderr)
            continue
        if verbose:
            status = result.validation.status
            print(
                f"[{index + 1}/{len(documents)}] {document_id} {document_type} "
                f"status={status} missing={len(result.validation.missing_fields)} "
                f"answers={len(answers)}"
            )

    results_file = ResultsFile(documents=results)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(results_file.model_dump(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    expected_documents = len(documents) if limit is None else min(limit, len(documents))
    summary = {
        "documents": len(results),
        "expected_documents": expected_documents,
        "questions": sum(len(item.answers) for item in results),
        "expected_questions": sum(
            len(grouped.get(item["document_id"], []))
            for item in (documents[:expected_documents])
        ),
        "failures": failures,
        "seconds": round(time.time() - started, 2),
        "ocr": dict(pipeline.stats),
        "output": str(output_path),
    }
    if verbose:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--documents", required=True, help="путь к documents.json")
    parser.add_argument("--questions", required=True, help="путь к questions.json")
    parser.add_argument("--root", required=True, help="каталог архива с PNG/PDF")
    parser.add_argument("--output", required=True, help="путь к results.json")
    parser.add_argument(
        "--prefer",
        default="image",
        choices=["image", "pdf"],
        help="какое представление использовать по умолчанию (image|pdf)",
    )
    parser.add_argument("--limit", type=int, default=None, help="обработать только первые N документов")
    parser.add_argument("--quiet", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = run_batch(
        documents_path=Path(args.documents),
        questions_path=Path(args.questions),
        root=Path(args.root),
        output_path=Path(args.output),
        prefer=args.prefer,
        limit=args.limit,
        verbose=not args.quiet,
    )
    return 0 if not summary["failures"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
