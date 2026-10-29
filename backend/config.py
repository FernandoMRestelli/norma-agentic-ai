"""Configuración central leída desde variables de entorno (.env)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    app_version: str = "dev"  # la imagen de cada release la fija con el número del tag

    # LLM: el proveedor se define más adelante (ver backend/agent/llm_client.py)
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None

    # Google Sheets
    google_credentials_file: str | None = None
    catalog_sheet_id: str | None = None
    orders_sheet_id: str | None = None

    # WhatsApp Cloud API
    whatsapp_verify_token: str | None = None
    whatsapp_app_secret: str | None = None  # valida la firma de los POST de Meta
    whatsapp_access_token: str | None = None
    whatsapp_phone_number_id: str | None = None

    # Base interna
    database_url: str = "sqlite:///./data/norma.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
