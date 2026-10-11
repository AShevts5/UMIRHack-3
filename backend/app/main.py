"""FastAPI-приложение «Онлайн-деканат» (UMIR 3).

Что тут отдаём:

``GET  /api/v1/health``
    Жив ли сервис и есть ли локальный OCR (бинарник Tesseract + русский
    traineddata).
``GET  /api/v1/document-types``
    Типы документов и подписи полей — по этому списку интерфейс строит выбор типа.
``POST /api/v1/analyze``
    multipart ``file`` + ``document_type`` (+ необязательный ``question``): в ответ
    летят распознанный текст, карточка полей, результат предварительной проверки,
    ответ со ссылками на пункты и рамки слов для подсветки.

Всё считаем локально и тем же ``backend.app.services.pipeline``, что и CLI, так что
ответ веба и пакетный ``results.json`` разъехаться не могут.
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
from .guide_api import record_history
from .guide_api import router as guide_router
from .schemas import AnalysisResponse, Answer, HealthResponse
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
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        # мобилка (Capacitor): Android ходит с https://localhost, iOS — с capacitor://localhost
        "https://localhost",
        "http://localhost",
        "capacitor://localhost",
        *[origin for origin in os.environ.get("UMIR_CORS_ORIGINS", "").split(",") if origin],
    ],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["WWW-Authenticate"],
)


@app.middleware("http")
async def security_headers(request, call_next):
    """Базовые заголовки безопасности, по минимуму. Referer наружу — только адрес сайта: тайлам
    OpenStreetMap он нужен по их правилам, а путь страницы им знать ни к чему."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Permissions-Policy", "microphone=(self), geolocation=(self), camera=()")
    return response

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
    _attach_page(response, result, filename, question)
    record_history(request, response, filename, question)
    logger.info(
        "analyze type=%s source=%s fields=%d missing=%d",
        result.document_type,
        result.document_type_source,
        len(result.fields),
        len(result.validation.missing_fields),
    )
    return response


def _attach_page(response: AnalysisResponse, result, filename: str, question: str | None) -> None:
    """Кладём разбор в память процесса: так интерфейс показывает страницу и задаёт
    новые вопросы без повторного OCR. На диск ничего не пишем."""
    from datetime import datetime, timezone

    from .team_api import StoredAnalysis, new_request_id, page_png, store

    try:
        page = page_png(result)
    except Exception:  # noqa: BLE001 - без превью ответ всё равно полезен
        logger.warning("page preview failed", exc_info=True)
        page = None
    analysis_id = new_request_id()
    store.put(
        StoredAnalysis(
            request_id=analysis_id,
            filename=filename,
            created_at=datetime.now(timezone.utc),
            document_type=result.document_type,
            question=question or "",
            answer_status="ok" if response.answer.answerable else "no_info",
            page_png=page,
            result=result,
        )
    )
    response.analysis_id = analysis_id
    if page is not None:
        response.page_image_url = f"/api/files/{analysis_id}/page-1"
        response.page_width, response.page_height = (int(v) for v in result.image_size)
        response.page_enhanced = bool(set(result.enhance_steps) & {"page_crop", "deskew", "flatten_lighting"})
    response.timings_ms = {key: round(float(value), 1) for key, value in result.timings.items()}


@app.post("/api/v1/ask", response_model=Answer)
def ask(
    analysis_id: str = Form(..., description="analysis_id из ответа /api/v1/analyze"),
    question: str = Form(..., min_length=1, max_length=_MAX_QUESTION_LENGTH),
) -> Answer:
    """Ещё вопрос к уже разобранному документу — OCR второй раз не гоняем."""
    from .team_api import store

    item = store.get(analysis_id)
    if item is None or item.result is None:
        raise HTTPException(status_code=404, detail="Разбор не найден или устарел. Загрузите документ ещё раз.")
    return item.result.single_answer(question, "Q1")


@app.exception_handler(DocumentLoadError)
async def document_error_handler(_: Request, exc: DocumentLoadError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})


# ---------------------------------------------------------------------------
# Контракт команды: адреса те же, что были у заглушек, но внутри уже настоящая обработка
# ---------------------------------------------------------------------------
TEAM_ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf"}
TEAM_MAX_FILE_SIZE = 20 * 1024 * 1024


@app.get("/api/doc-types", tags=["team-contract"])
def team_doc_types() -> dict:
    """Справочник типов документов в том формате, который ждёт фронт команды."""
    from .team_api import document_types_payload

    return {"items": document_types_payload()}


@app.post("/api/analyze", tags=["team-contract"])
async def team_analyze(
    file: UploadFile = File(...),
    question: str = Form(..., min_length=1, max_length=1000),
    doc_type: str | None = Form(None),
) -> JSONResponse:
    """Разбор документа: локальный OCR, поля, отметки, правила и ответ на вопрос.

    Раньше этот адрес отдавал заготовленные значения (файл читали, но никак не
    обрабатывали — по сути муляж). Теперь дёргаем тот же пайплайн, что и в пакетном режиме,
    а поля перекладываем в ключи контракта (``full_name``, ``study_period`` и т.д.).
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
    """Страница, которую разбирал OCR, — превью для интерфейса команды."""
    from .team_api import store

    item = store.get(request_id)
    if item is None or item.page_png is None:
        raise HTTPException(status_code=404, detail="Страница не найдена или устарела")
    return Response(content=item.page_png, media_type="image/png")


@app.get("/api/history", tags=["team-contract"])
def team_history(limit: int = 20) -> dict:
    """Последние разборы. Живут только в памяти процесса, на диск не пишем."""
    from .team_api import history_payload, store

    return {"items": history_payload(store.history(max(1, min(limit, 50))))}


@app.get("/api/regulation", tags=["team-contract"])
def team_regulation() -> dict:
    """Весь текст Положения (rules.md) целиком — для раздела «Положение»."""
    from .team_api import regulation_payload

    if settings.rules_path is None:
        return {"items": [], "note": "rules.md не найден; задайте UMIR_RULES"}
    return {"items": regulation_payload(settings.rules_path)}


# Путеводитель по вузу: вход, реестр вузов, помощник (это уже сверх кейса, см. guide_api.py)
app.include_router(guide_router)


# ---------------------------------------------------------------------------
# По желанию раздаём собранный фронт (демо на одном origin)
# ---------------------------------------------------------------------------
if FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(FRONTEND_DIST / "index.html")

    _DIST_ROOT = FRONTEND_DIST.resolve()
    _NO_CACHE = {"sw.js", "manifest.webmanifest", "index.html"}

    @app.get("/{path:path}", include_in_schema=False)
    def frontend_file(path: str) -> Response:
        """Файлы из корня сборки (PWA-манифест, service worker, иконки, картинки
        вузов). На любой незнакомый адрес вне /api отдаём index.html."""
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Не найдено")
        candidate = (_DIST_ROOT / path).resolve()
        if candidate.is_file() and _DIST_ROOT in candidate.parents:
            headers = {"Cache-Control": "no-cache"} if candidate.name in _NO_CACHE else None
            media_type = "application/manifest+json" if candidate.suffix == ".webmanifest" else None
            return FileResponse(candidate, headers=headers, media_type=media_type)
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
