"""Cross-platform task runner: ``python -m backend.scripts.tasks <task>``.

Mirrors the usual Makefile targets so the project is reproducible on Windows without
GNU make:

``install``      create the virtual environment and install backend requirements
``test``         run the unit/integration test-suite (pytest, no OCR needed for most)
``batch``        run the batch flow on the open archive -> results.json
``evaluate``     call the organizer ``evaluate.py`` on those predictions
``verify``       renamed-files integrity, PNG/PDF equivalence, auto-type, limits
``ocr-quality``  OCR character accuracy / exact page rate benchmark (ground truth)
``degraded``     build the «плохие снимки» package (rotate/shadow/glare/lowres/mix)
``degraded-evaluate`` batch-run that package and score it with ``evaluate.py``
``api``          start the FastAPI server locally
``demo``         batch + evaluate + verify in one command
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _run(command: list[str], **kwargs) -> int:
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    print("$", " ".join(command), flush=True)
    return subprocess.call(command, cwd=str(PROJECT_ROOT), env=env, **kwargs)


def _python() -> str:
    return sys.executable


def _archive() -> Path:
    candidates = [
        Path(os.environ["UMIR_ARCHIVE"]) if os.environ.get("UMIR_ARCHIVE") else None,
        PROJECT_ROOT.parent / "online-dean-participants-v1 (2)",
        PROJECT_ROOT.parent / "online-dean-participants-v1",
    ]
    for candidate in candidates:
        if candidate and (candidate / "documents.json").is_file():
            return candidate
    raise SystemExit("Открытый архив кейса не найден. Задайте UMIR_ARCHIVE=/path/to/archive")


def task_install(_: argparse.Namespace) -> int:
    venv_python = PROJECT_ROOT / ".venv" / ("Scripts" if os.name == "nt" else "bin") / "python"
    if not venv_python.exists():
        code = _run([_python(), "-m", "venv", str(PROJECT_ROOT / ".venv")])
        if code:
            return code
    return _run([str(venv_python), "-m", "pip", "install", "-r", "requirements.txt"])


def task_test(args: argparse.Namespace) -> int:
    command = [_python(), "-m", "pytest", "-q", "--basetemp=.pytest_tmp"]
    if args.extra:
        command += args.extra
    return _run(command)


def task_batch(args: argparse.Namespace) -> int:
    archive = Path(args.root) if args.root else _archive()
    output = Path(args.output) if args.output else PROJECT_ROOT / "results.json"
    return _run(
        [
            _python(),
            "-m",
            "backend.scripts.run_batch",
            "--documents",
            str(archive / "documents.json"),
            "--questions",
            str(archive / "questions.json"),
            "--root",
            str(archive),
            "--output",
            str(output),
            "--prefer",
            args.prefer,
        ]
    )


def task_evaluate(args: argparse.Namespace) -> int:
    archive = Path(args.root) if args.root else _archive()
    predictions = Path(args.output) if args.output else PROJECT_ROOT / "results.json"
    report = PROJECT_ROOT / "reports" / "open-set.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    return _run(
        [
            _python(),
            "-m",
            "backend.scripts.evaluate_open",
            "--reference",
            str(archive / "reference_results.json"),
            "--predictions",
            str(predictions),
            "--report",
            str(report),
        ]
    )


def task_verify(args: argparse.Namespace) -> int:
    archive = Path(args.root) if args.root else _archive()
    report = PROJECT_ROOT / "reports" / "verify.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    return _run(
        [
            _python(),
            "-m",
            "backend.scripts.verify",
            "--root",
            str(archive),
            "--report",
            str(report),
        ]
    )


def task_degraded(args: argparse.Namespace) -> int:
    """Build the «плохие снимки» benchmark package from the open archive."""
    archive = Path(args.root) if args.root else _archive()
    out = PROJECT_ROOT / "reports" / "degraded"
    effect = getattr(args, "effect", "mix")
    return _run(
        [
            _python(),
            "-m",
            "backend.scripts.make_degraded",
            "--src",
            str(archive),
            "--out",
            str(out),
            "--effect",
            effect,
        ]
    )


def task_degraded_evaluate(args: argparse.Namespace) -> int:
    """Batch-run the degraded package and score it with the organizer evaluator."""
    archive = Path(args.root) if args.root else _archive()
    out = PROJECT_ROOT / "reports" / "degraded"
    predictions = PROJECT_ROOT / "reports" / "degraded_results.json"
    report = PROJECT_ROOT / "reports" / "degraded-open-set.json"
    if not (out / "documents.json").is_file():
        code = task_degraded(args)
        if code:
            return code
    code = _run(
        [
            _python(),
            "-m",
            "backend.scripts.run_batch",
            "--documents",
            str(out / "documents.json"),
            "--questions",
            str(out / "questions.json"),
            "--root",
            str(out),
            "--output",
            str(predictions),
        ]
    )
    if code:
        return code
    return _run(
        [
            _python(),
            "-m",
            "backend.scripts.evaluate_open",
            "--reference",
            str(out / "reference_results.json"),
            "--predictions",
            str(predictions),
            "--evaluate-py",
            str(archive / "evaluate.py"),
            "--report",
            str(report),
        ]
    )


def task_api(_: argparse.Namespace) -> int:
    return _run(
        [
            _python(),
            "-m",
            "uvicorn",
            "backend.app.main:app",
            "--host",
            os.environ.get("UMIR_HOST", "127.0.0.1"),
            "--port",
            os.environ.get("UMIR_PORT", "8000"),
        ]
    )


def task_ocr_quality(args: argparse.Namespace) -> int:
    """Rebuild the ground truth and measure OCR character accuracy on the open set."""
    archive = Path(args.root) if args.root else _archive()
    ground_truth = PROJECT_ROOT / "benchmarks" / "ground_truth.json"
    report = PROJECT_ROOT / "reports" / "ocr-benchmark.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    code = _run(
        [
            _python(),
            "-m",
            "backend.scripts.make_ocr_ground_truth",
            "--archive",
            str(archive),
            "--output",
            str(ground_truth),
        ]
    )
    if code:
        return code
    return _run(
        [
            _python(),
            "-m",
            "backend.scripts.benchmark_ocr",
            "--ground-truth",
            str(ground_truth),
            "--root",
            str(archive),
            "--output",
            str(report),
        ]
    )


def task_demo(args: argparse.Namespace) -> int:
    for task in (task_batch, task_evaluate, task_verify):
        code = task(args)
        if code:
            return code
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", help="каталог открытого архива")
    parser.add_argument("--output", help="куда писать results.json")
    parser.add_argument("--prefer", default="image", choices=["image", "pdf"])
    subparsers = parser.add_subparsers(dest="task", required=True)
    for name, handler in (
        ("install", task_install),
        ("test", task_test),
        ("batch", task_batch),
        ("evaluate", task_evaluate),
        ("verify", task_verify),
        ("ocr-quality", task_ocr_quality),
        ("degraded", task_degraded),
        ("degraded-evaluate", task_degraded_evaluate),
        ("api", task_api),
        ("demo", task_demo),
    ):
        sub = subparsers.add_parser(name)
        sub.set_defaults(handler=handler)
        if name == "test":
            sub.add_argument("extra", nargs="*", help="дополнительные аргументы pytest")
        if name == "degraded":
            sub.add_argument(
                "--effect",
                default="mix",
                choices=["rotate", "shadow", "glare", "lowres", "mix"],
                help="какой дефект имитировать (по умолчанию mix)",
            )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
