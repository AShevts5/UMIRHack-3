from fastapi import APIRouter

from app import mocks
from app.schemas import DocTypeInfo

router = APIRouter(tags=["doc-types"])


@router.get("/doc-types", response_model=list[DocTypeInfo], summary="Список типов документов")
async def list_doc_types() -> list[DocTypeInfo]:
    return mocks.DOC_TYPES