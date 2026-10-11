from typing import Annotated

from fastapi import APIRouter, Query

from app import mocks
from app.schemas import HistoryItem

router = APIRouter(tags=["history"])

@router.get("/history", response_model=list[HistoryItem], summary="Последние обращения")
async def get_history(
    limit: Annotated[int, Query(ge=1, le=200, description="Сколько записей вернуть")] = 50,
) -> list[HistoryItem]:
    return mocks.HISTORY[:limit]