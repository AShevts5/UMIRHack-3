"""Запускаем ``evaluate.py`` организаторов и сохраняем отчёт с метриками.

Эталон читается **только** здесь, в отдельном контуре оценки: ни один модуль
инференса его не импортирует (в ТЗ запрещено использовать эталон при распознавании).
Сам evaluate.py мы не правим и не копируем к себе — запускаем тот, что лежит в архиве,
путь к которому передали в командной строке.

Example
-------
.. code-block:: bash

    python -m backend.scripts.evaluate_open \
        --reference /path/to/reference_results.json \
        --predictions results.json \
        --report reports/open-set.json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def run_evaluator(reference: Path, predictions: Path, evaluate_py: Path, report: Path | None) -> tuple[int, dict | None, str]:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    command = [
        sys.executable,
        str(evaluate_py),
        "--reference",
        str(reference),
        "--predictions",
        str(predictions),
    ]
    if report is not None:
        command += ["--report", str(report)]
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", env=env)
    payload = None
    stdout = completed.stdout or ""
    try:
        payload = json.loads(stdout[stdout.index("{") :])
    except (ValueError, json.JSONDecodeError):
        payload = None
    if payload is None and report is not None and report.is_file():
        payload = json.loads(report.read_text(encoding="utf-8"))
    return completed.returncode, payload, (completed.stderr or "")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reference", required=True, help="reference_results.json открытого набора")
    parser.add_argument("--predictions", required=True, help="results.json, полученный run_batch")
    parser.add_argument(
        "--evaluate-py",
        help="путь к evaluate.py организаторов (по умолчанию — рядом с reference)",
    )
    parser.add_argument("--report", help="куда записать отчёт evaluate.py")
    args = parser.parse_args(argv)

    reference = Path(args.reference)
    predictions = Path(args.predictions)
    evaluate_py = Path(args.evaluate_py) if args.evaluate_py else reference.parent / "evaluate.py"
    if not evaluate_py.is_file():
        print(f"evaluate.py не найден: {evaluate_py}", file=sys.stderr)
        return 2

    report_path = Path(args.report) if args.report else None
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
    code, payload, stderr = run_evaluator(reference, predictions, evaluate_py, report_path)
    if payload is None:
        print(stderr or "evaluate.py не вернул JSON", file=sys.stderr)
        return code or 1
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if not payload.get("format_valid", False):
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
