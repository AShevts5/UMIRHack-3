"""Shared pytest fixtures for the UMIR 3 test-suite.

The open case archive is expected next to the repository (as shipped by the
organizer).  Tests that need it are skipped when it is absent, so the suite stays
runnable on a fresh clone; tests that only need synthetic data always run.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ARCHIVE_ENV = "UMIR_ARCHIVE"
ARCHIVE_CANDIDATES = (
    Path(os.environ.get(ARCHIVE_ENV)) if os.environ.get(ARCHIVE_ENV) else None,
    PROJECT_ROOT.parent / "online-dean-participants-v1 (2)",
    PROJECT_ROOT.parent / "online-dean-participants-v1",
    PROJECT_ROOT / "data" / "online-dean-participants-v1",
)


def find_archive() -> Path | None:
    for candidate in ARCHIVE_CANDIDATES:
        if candidate and (candidate / "documents.json").is_file():
            return candidate
    return None


ARCHIVE = find_archive()

requires_archive = pytest.mark.skipif(ARCHIVE is None, reason="открытый архив кейса не найден")
requires_ocr = pytest.mark.skipif(
    shutil.which("tesseract") is None
    and not Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe").exists()
    and not Path(r"C:\msys64\ucrt64\bin\tesseract.exe").exists()
    and not os.environ.get("TESSERACT_CMD"),
    reason="локальный Tesseract не установлен",
)


@pytest.fixture(scope="session")
def archive() -> Path:
    assert ARCHIVE is not None
    return ARCHIVE


@pytest.fixture(scope="session")
def documents(archive: Path) -> list[dict]:
    return json.loads((archive / "documents.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def questions(archive: Path) -> list[dict]:
    return json.loads((archive / "questions.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def reference(archive: Path) -> dict:
    return json.loads((archive / "reference_results.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def registry():
    from backend.app.services.document_config import get_registry

    return get_registry()
