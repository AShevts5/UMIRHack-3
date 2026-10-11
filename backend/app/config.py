from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Онлайн-деканат"
    app_version: str = "0.1.0"
    environment: str = "dev"
    debug: bool = False

    api_prefix: str = "/api"

    cors_origins: list[str] = ["http://localhost:5173"]

    database_url: str = "postgresql+asyncpg://umir:umir@localhost:5432/umir"


@lru_cache
def get_settings() -> Settings:
    return Settings()