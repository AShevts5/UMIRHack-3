"""Меряем, насколько хорошо распознаём: точность по символам, по страницам и по полям.

Метрики считаем ровно как в бенчмарке проекта PdfOcrDesktop — чтобы числа двух
проектов можно было сравнивать в лоб:

``char_accuracy``
    ``1 - CER`` по склеенному тексту страницы. Обе стороны перед сравнением
    нормализуем так же, как в ``evaluate.py`` кейса: регистр, ``ё``, кавычки,
    пробелы.
``exact_page_rate``
    доля страниц, где распознанный текст после нормализации совпал с эталоном
    целиком, буква в букву.
``mean_confidence``
    средняя уверенность распознанных слов в шкале 0…1.

Ещё две метрики наши собственные, считаем их отдельно, чтобы не мешать в одну кучу
распознавание и разбор:

``field_exact_rate``
    доля значений полей, которые парсер вытащил точно. Как раз это и оценивает
    ``evaluate.py`` кейса.
``pages_per_second``
    скорость распознавания по тем страницам, которые реально прогоняли.

Как запускать:

.. code-block:: bash

    python -m backend.scripts.make_ocr_ground_truth --archive <архив> --output benchmarks/ground_truth.json
    python -m backend.scripts.benchmark_ocr --ground-truth benchmarks/ground_truth.json \
        --root <архив> --output reports/ocr-benchmark.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.config import settings  # noqa: E402
from backend.app.services import ocr as ocr_service  # noqa: E402
from backend.app.services.document_config import get_registry  # noqa: E402
from backend.app.services.loader import load_document  # noqa: E402
from backend.app.services.parse_fields import NOT_FOUND, parse_document  # noqa: E402
from backend.app.utils.textnorm import norm_text  # noqa: E402


def _distance(left: str, right: str) -> int:
    """Старый добрый Левенштейн по кодпойнтам (как в нормализации evaluate.py)."""
    if left == right:
        return 0
    previous = list(range(len(right) + 1))
    for i, char_left in enumerate(left, 1):
        current = [i]
        for j, char_right in enumerate(right, 1):
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (char_left != char_right))
            )
        previous = current
    return previous[-1]


def _field_accuracy(
    actual_text: str,
    field_lines: list[dict],
    parser_fields: dict[str, str | bool],
) -> tuple[int, int, list[dict]]:
    """Режем строку поля на подпись и значение, а значение сверяем с эталоном."""
    folded_lines = [norm_text(line) for line in actual_text.splitlines()]
    exact = 0
    checked = 0
    misses: list[dict] = []
    for item in field_lines:
        checked += 1
        label = norm_text(item["label"])
        variants = [norm_text(variant) for variant in item["variants"]]
        observed = None
        for line in folded_lines:
            if label in line:
                observed = line.split(label, 1)[1].lstrip(": ").strip(" .;·•-")
                break
        hit = observed in variants if observed is not None else False
        if hit:
            exact += 1
        else:
            misses.append(
                {
                    "key": item["key"],
                    "expected": item["value"],
                    "from_text": observed,
                    "from_parser": parser_fields.get(item["key"]),
                }
            )
    return exact, checked, misses


def run_benchmark(
    ground_truth_path: Path,
    root: Path,
    output: Path | None,
    limit: int | None = None,
    verbose: bool = True,
) -> dict:
    payload = json.loads(ground_truth_path.read_text(encoding="utf-8"))
    pages = payload["pages"]
    if limit is not None:
        pages = pages[:limit]
    registry = get_registry()

    page_reports = []
    total_reference_chars = 0
    total_errors = 0
    exact_pages = 0
    field_exact = field_checked = 0
    confidences: list[float] = []
    recognition_seconds = 0.0
    started = time.perf_counter()

    for index, page in enumerate(pages, 1):
        image_path = root / page["image"]
        if not image_path.is_file():
            image_path = root / "documents" / Path(page["image"]).name
        image = load_document(image_path)
        began = time.perf_counter()
        result = ocr_service.run_ocr(image)
        elapsed = time.perf_counter() - began
        recognition_seconds += elapsed

        reference = norm_text(page["page_text"])
        hypothesis = norm_text(result.text)
        errors = _distance(reference, hypothesis)
        accuracy = 1 - errors / max(len(reference), 1)
        is_exact = reference == hypothesis
        exact_pages += int(is_exact)
        total_reference_chars += len(reference)
        total_errors += errors
        confidences.append(float(result.mean_conf) / 100.0)

        spec = registry.require(page["document_type"])
        parsed = parse_document(result, spec)
        parser_fields: dict[str, str | bool] = {}
        for field in spec.fields:
            field_result = parsed.field_results.get(field.key)
            parser_fields[field.key] = field_result.value if field_result and field_result.value else NOT_FOUND
        exact, checked, misses = _field_accuracy(result.text, page["field_lines"], parser_fields)
        field_exact += exact
        field_checked += checked

        page_reports.append(
            {
                "document_id": page["document_id"],
                "document_type": page["document_type"],
                "char_accuracy": round(accuracy, 6),
                "exact_normalized": is_exact,
                "reference_chars": len(reference),
                "errors": errors,
                "mean_confidence": round(float(result.mean_conf) / 100.0, 4),
                "field_exact": exact,
                "field_checked": checked,
                "field_misses": misses,
                "seconds": round(elapsed, 2),
            }
        )
        if verbose:
            flag = "OK " if is_exact else "   "
            print(
                f"{flag}[{index}/{len(pages)}] {page['document_id']:9} {page['document_type'][:9]:9} "
                f"char_acc={accuracy:.4f} conf={result.mean_conf:5.1f} поля {exact}/{checked} {elapsed:.1f}с"
            )

    summary = {
        "pages": len(page_reports),
        "reference_chars": total_reference_chars,
        "char_accuracy": round(1 - total_errors / max(total_reference_chars, 1), 6),
        "exact_page_rate": round(exact_pages / max(len(page_reports), 1), 4),
        "mean_confidence": round(sum(confidences) / max(len(confidences), 1), 4),
        "field_exact_rate": round(field_exact / max(field_checked, 1), 4),
        "field_exact": field_exact,
        "field_checked": field_checked,
        "recognition_seconds": round(recognition_seconds, 2),
        "pages_per_second": round(len(page_reports) / recognition_seconds, 4) if recognition_seconds else None,
        "total_seconds": round(time.perf_counter() - started, 2),
        "engine": "local",
        "languages": ocr_service.resolve_languages(settings.ocr.languages),
        "ocr_settings": {
            "oem": settings.ocr.oem,
            "psm": settings.ocr.psm,
            "target_long_edge": settings.ocr.target_long_edge,
            "binarization": settings.ocr.use_binarization,
        },
    }

    report = {
        "schema_version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "ground_truth": str(ground_truth_path),
        "root": str(root),
        "summary": summary,
        "pages": page_reports,
    }
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ground-truth", default="benchmarks/ground_truth.json")
    parser.add_argument("--root", required=True, help="каталог архива с изображениями")
    parser.add_argument("--output", default="reports/ocr-benchmark.json")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    report = run_benchmark(
        Path(args.ground_truth), Path(args.root), Path(args.output), limit=args.limit, verbose=not args.quiet
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    if not args.quiet:
        misses = [(page["document_id"], miss) for page in report["pages"] for miss in page["field_misses"]]
        if misses:
            print("\nрасхождения по полям:", file=sys.stderr)
            for document_id, miss in misses:
                print(f"  {document_id}.{miss['key']}: эталон={miss['expected']!r} текст={miss['from_text']!r}", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
