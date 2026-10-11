# Проверка, шо сервак жив

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.config import Settings, get_settings

router = APIRouter(tags=["health"])

class HealthResponse(BaseModel):
    status: str
    app: str
    version: str
    environment: str
    time: datetime

@router.get("/health", response_model=HealthResponse, summary="Проверка работы сервиса")
async def health(settings: Settings = Depends(get_settings)) -> HealthResponse:
    return HealthResponse(
        status="ok",
        app=settings.app_name,
        version=settings.app_version,
        environment=settings.environment,
        time=datetime.now(timezone.utc),
    )