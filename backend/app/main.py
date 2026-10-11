"""FastAPI-приложение «Онлайн-деканат» (UMIR 3).

Что отдаём:

``GET  /api/v1/health``
    Состояние сервиса и доступность локального OCR (бинарник Tesseract + русский
    traineddata).
``GET  /api/v1/document-types``
    Типы документов и подписи полей — по этому списку строится выбор типа в интерфейсе.
``POST /api/v1/analyze``
    multipart ``file`` + ``document_type`` (+ необязательный ``question``): в ответ
    идут распознанный текст, карточка полей, результат предварительной проверки,
    ответ со ссылками на пункты и рамки слов для подсветки.

Считаем всё локально и тем же ``backend.app.services.pipeline``, что и CLI, поэтому
ответ веба и пакетный ``results.json`` разойтись не могут.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
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
    except Exception as exc:  # noqa: BLE001 - трассировку клиенту не отдаём
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
# Контракт команды: те же адреса, что были у заглушек, но с настоящей обработкой
# ---------------------------------------------------------------------------
TEAM_ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf"}
TEAM_MAX_FILE_SIZE = 20 * 1024 * 1024


@app.get("/api/doc-types", tags=["team-contract"])
def team_doc_types() -> dict:
    """Справочник типов документов в формате, который ждёт фронтенд команды."""
    from .team_api import document_types_payload

    return {"items": document_types_payload()}


@app.post("/api/analyze", tags=["team-contract"])
async def team_analyze(
    file: UploadFile = File(...),
    question: str = Form(..., min_length=1, max_length=1000),
    doc_type: str | None = Form(None),
) -> JSONResponse:
    """Разбор документа: локальный OCR, поля, отметки, правила и ответ на вопрос.

    Раньше на этом адресе возвращались заготовленные значения (файл читался, но не
    обрабатывался). Теперь вызывается тот же пайплайн, что и в пакетном режиме, а
    поля переводятся в ключи контракта (``full_name``, ``study_period`` и т.д.).
    """
    import time

    from .team_api import (
        StoredAnalysis,
        build_analyze_response,
        new_request_id,
        resolve_type,
        store,
    )

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in TEAM_ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Файл должен быть в формате JPG, PNG или PDF")
    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="Файл пустой")
    if len(payload) > TEAM_MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="Файл больше 20 МБ")

    requested = resolve_type(doc_type)
    if doc_type and requested is None:
        raise HTTPException(status_code=400, detail=f"Неизвестный тип документа: {doc_type}")

    started = time.perf_counter()
    try:
        result = pipeline.analyze_bytes(payload, file.filename or "document", requested or "auto")
    except DocumentLoadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ocr_service.OcrUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    request_id = new_request_id()
    body, page = build_analyze_response(result, question, request_id)
    body.setdefault("timings_ms", {})["total"] = round((time.perf_counter() - started) * 1000, 1)
    store.put(
        StoredAnalysis(
            request_id=request_id,
            filename=file.filename or "document",
            created_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            document_type=result.document_type,
            question=question,
            answer_status=body["answer"]["status"],
            page_png=page,
            result=result,
        )
    )
    logger.info(
        "team analyze type=%s source=%s fields=%d missing=%d",
        result.document_type,
        result.document_type_source,
        len(result.fields),
        len(result.validation.missing_fields),
    )
    return JSONResponse(content=body)


@app.get("/api/files/{request_id}/{page}", tags=["team-contract"])
def team_file(request_id: str, page: str) -> Response:
    """Страница, по которой работал OCR, — превью для интерфейса команды."""
    from .team_api import store

    item = store.get(request_id)
    if item is None or item.page_png is None:
        raise HTTPException(status_code=404, detail="Страница не найдена или устарела")
    return Response(content=item.page_png, media_type="image/png")


@app.get("/api/history", tags=["team-contract"])
def team_history(limit: int = 20) -> dict:
    """Последние разборы. Хранятся в памяти процесса, на диск не пишутся."""
    from .team_api import history_payload, store

    return {"items": history_payload(store.history(max(1, min(limit, 50))))}


@app.get("/api/regulation", tags=["team-contract"])
def team_regulation() -> dict:
    """Полный текст Положения (rules.md) для раздела «Положение»."""
    from .team_api import regulation_payload

    if settings.rules_path is None:
        return {"items": [], "note": "rules.md не найден; задайте UMIR_RULES"}
    return {"items": regulation_payload(settings.rules_path)}


# ---------------------------------------------------------------------------
# Необязательная раздача собранного фронтенда (демо на одном origin)
# ---------------------------------------------------------------------------
if FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(FRONTEND_DIST / "index.html")


def run() -> None:  # pragma: no cover - точка входа
    import uvicorn

    uvicorn.run(
        "backend.app.main:app",
        host=os.environ.get("UMIR_HOST", "127.0.0.1"),
        port=int(os.environ.get("UMIR_PORT", "8000")),
        reload=False,
    )


if __name__ == "__main__":  # pragma: no cover
    run()
