
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile

from app import mocks
from app.schemas import AnalyzeResponse, AnswerStatus, ErrorResponse

router = APIRouter(tags=["analyze"])

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf"}
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 МБ


@router.post(
    "/analyze",
    response_model=AnalyzeResponse,
    responses={400: {"model": ErrorResponse}, 413: {"model": ErrorResponse}},
    summary="Проанализировать документ и ответить на вопрос",
)
async def analyze(
    file: Annotated[UploadFile, File(description="Фото или скан документа: JPG, PNG или PDF")],
    question: Annotated[str, Form(min_length=1, max_length=1000, description="Вопрос пользователя")],
    doc_type: Annotated[
        str | None,
        Form(description="id типа документа из /api/doc-types; пусто — определить автоматически"),
    ] = None,
    mock: Annotated[
        AnswerStatus | None,
        Query(description="ТОЛЬКО ДЛЯ ОТЛАДКИ: принудительно вернуть ok, missing или no_info"),
    ] = None,
) -> AnalyzeResponse:
    # Проверка формата файла
    extension = Path(file.filename or "").suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Файл должен быть в формате JPG, PNG или PDF")

    # Проверка размера
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Файл пустой")
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="Файл больше 20 МБ")

    # проверка типа документа (пустая строка = «определить автоматически»)
    doc_type = doc_type or None
    if doc_type is not None and doc_type not in mocks.DOC_TYPE_TITLES:
        raise HTTPException(status_code=400, detail=f"Неизвестный тип документа: {doc_type}")

    # Сборка ответа
    request_id = uuid.uuid4().hex[:12]
    status = mock or mocks.pick_mock_status(question)
    response = mocks.build_analyze_response(request_id, doc_type, status)

    mocks.add_history(response, filename=file.filename or "document", question=question)
    return response