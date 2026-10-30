"""Configuración central leída desde variables de entorno (.env)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    app_version: str = "dev"  # la imagen de cada release la fija con el número del tag

    # LLM de texto (ver backend/agent/llm_client.py). En la beta: gemini, free tier
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None

    # LLM para audio e imagen (modelo pago, a definir): se lee pero todavía no se usa
    llm_vision_provider: str | None = None
    llm_vision_model: str | None = None
    llm_vision_api_key: str | None = None

    # Google Sheets
    google_credentials_file: str | None = None
    catalog_sheet_id: str | None = None
    orders_sheet_id: str | None = None

    # WhatsApp Cloud API
    whatsapp_verify_token: str | None = None
    whatsapp_app_secret: str | None = None  # valida la firma de los POST de Meta
    whatsapp_access_token: str | None = None
    whatsapp_phone_number_id: str | None = None

    # Telegram: canal de prueba de la beta (docs/spec/canal-telegram.md)
    telegram_bot_token: str | None = None
    telegram_webhook_secret: str | None = None  # header X-Telegram-Bot-Api-Secret-Token
    telegram_allowed_user_ids: str = ""  # ids numéricos separados por coma

    # Base interna
    database_url: str = "sqlite:///./data/norma.db"

    @property
    def telegram_allowed_ids(self) -> set[int]:
        return {int(part) for part in self.telegram_allowed_user_ids.replace(" ", "").split(",") if part}


@lru_cache
def get_settings() -> Settings:
    return Settings()
