"""Configuración central leída desde variables de entorno (.env)."""

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"

    # LLM: el proveedor se define más adelante (ver backend/agent/llm_client.py)
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None
    llm_api_key: Optional[str] = None

    # Google Sheets
    google_credentials_file: Optional[str] = None
    catalog_sheet_id: Optional[str] = None
    orders_sheet_id: Optional[str] = None

    # WhatsApp Cloud API
    whatsapp_verify_token: Optional[str] = None
    whatsapp_access_token: Optional[str] = None
    whatsapp_phone_number_id: Optional[str] = None

    # Base interna
    database_url: str = "sqlite:///./norma.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
