"""FastAPI application for «Онлайн-деканат» (UMIR 3).

Endpoints
---------
``GET  /api/v1/health``
    Service status and local OCR availability (Tesseract binary + rus traineddata).
``GET  /api/v1/document-types``
    Types and field labels known to the service (drives the UI selector).
``POST /api/v1/analyze``
    multipart ``file`` + ``document_type`` (+ optional ``question``); returns
    recognised text, the field card, the pre-check result, the answer with sources
    and word boxes for highlighting.

All processing is local: the same ``backend.app.services.pipeline`` used by the CLI
produces the numbers, so the web result and the batch ``results.json`` can never
diverge.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .schemas import AnalysisResponse, HealthResponse
from .services import ocr as ocr_service
from .services.document_config import get_registry
from .services.loader import DocumentLoadError
from .services.pipeline import build_api_response, pipeline
from .services.question_engine import default_completeness_question

logger = logging.getLogger("umir.api")

app = FastAPI(
    title="Онлайн-деканат — предварительная проверка студенческих документов",
    description=(
        "Локальный OCR + извлечение полей + проверка комплектности по вымышленному "
        "Положению университета «Меридиан» (rules.md). Решение принимает сотрудник деканата."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:4173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
_MAX_QUESTION_LENGTH = 500


@app.get("/api/v1/health", response_model=HealthResponse)
def health() -> HealthResponse:
    ocr_info = ocr_service.ocr_health()
    registry = get_registry()
    return HealthResponse(
        status="ok" if ocr_info.get("available") else "degraded",
        ocr=ocr_info,
        document_types=registry.titles(),
    )


@app.get("/api/v1/document-types")
def document_types() -> dict:
    registry = get_registry()
    return {
        "document_types": [
            {
                "code": spec.code,
                "title": spec.title,
                "fields": [
                    {"key": field.key, "label": field.label, "rule": spec.rule_for_field(field.key)}
                    for field in spec.fields
                ],
                "marks": [
                    {"key": mark.key, "label": mark.label, "rule": mark.rule} for mark in spec.marks
                ],
                "required_rules": list(spec.required_rules),
            }
            for spec in (registry.require(code) for code in registry.codes())
        ],
        "default_question": default_completeness_question(),
        "limits": {
            "max_upload_bytes": settings.limits.max_upload_bytes,
            "max_pdf_pages": settings.limits.max_pdf_pages,
            "allowed_suffixes": list(settings.limits.allowed_suffixes),
        },
    }


@app.post("/api/v1/analyze", response_model=AnalysisResponse)
async def analyze(
    request: Request,
    file: UploadFile = File(..., description="JPG/PNG или одностраничный PDF"),
    document_type: str = Form("auto", description="код типа документа или auto"),
    question: str | None = Form(None, description="вопрос на русском языке"),
    include_boxes: bool = Form(True),
) -> AnalysisResponse:
    registry = get_registry()
    if document_type not in ("auto", "", None) and registry.get(document_type) is None:
        raise HTTPException(
            status_code=422,
            detail=f"Неизвестный тип документа '{document_type}'. Доступны: {', '.join(registry.codes())}",
        )
    if question is not None and len(question) > _MAX_QUESTION_LENGTH:
        raise HTTPException(status_code=422, detail="Вопрос слишком длинный (максимум 500 символов).")

    filename = file.filename or "document.png"
    payload = await file.read()
    try:
        result = pipeline.analyze_bytes(payload, filename, document_type or "auto")
    except DocumentLoadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ocr_service.OcrUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - never leak a traceback to the client
        logger.exception("analyze failed: %s", type(exc).__name__)
        raise HTTPException(status_code=500, detail="Внутренняя ошибка обработки документа.") from exc

    response = build_api_response(
        result,
        question=question,
        include_boxes=include_boxes,
    )
    logger.info(
        "analyze type=%s source=%s fields=%d missing=%d",
        result.document_type,
        result.document_type_source,
        len(result.fields),
        len(result.validation.missing_fields),
    )
    return response


@app.exception_handler(DocumentLoadError)
async def document_error_handler(_: Request, exc: DocumentLoadError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})


# ---------------------------------------------------------------------------
# Optional static hosting of the built frontend (single-origin demo)
# ---------------------------------------------------------------------------
if FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(FRONTEND_DIST / "index.html")


def run() -> None:  # pragma: no cover - entry point
    import uvicorn

    uvicorn.run(
        "backend.app.main:app",
        host=os.environ.get("UMIR_HOST", "127.0.0.1"),
        port=int(os.environ.get("UMIR_PORT", "8000")),
        reload=False,
    )


if __name__ == "__main__":  # pragma: no cover
    run()
