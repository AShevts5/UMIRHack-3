from fastapi import APIRouter, HTTPException

from app import mocks
from app.schemas import ErrorResponse, RegulationPoint

router = APIRouter(tags=["regulation"])

@router.get("/regulation", response_model=list[RegulationPoint], summary="Всё Положение по пунктам")
async def get_regulation() -> list[RegulationPoint]:
    return mocks.REGULATION

@router.get(
    "/regulation/{point}",
    response_model=RegulationPoint,
    responses={404: {"model": ErrorResponse}},
    summary="Один пункт Положения по номеру, например 3.4",
)
async def get_regulation_point(point: str) -> RegulationPoint:
    item = mocks.find_regulation_point(point)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Пункт {point} не найден")
    return item