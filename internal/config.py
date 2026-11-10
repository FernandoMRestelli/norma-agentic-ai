"""Configuración central leída desde variables de entorno (.env)."""

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    app_version: str = "dev"  # la imagen de cada release la fija con el número del tag

    # LLM de texto (ver internal/agent/llm.py). Proveedores free tier: gemini, groq, openrouter
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None

    # LLM para audio e imagen (modelo pago, a definir): se lee pero todavía no se usa
    llm_vision_provider: str | None = None
    llm_vision_model: str | None = None
    llm_vision_api_key: str | None = None

    # Google Sheets / Catálogo local
    google_credentials_file: str | None = None
    catalog_sheet_id: str | None = None
    orders_sheet_id: str | None = None
    catalog_file: str = "data_example/catalogo.csv"

    # WhatsApp Cloud API
    whatsapp_verify_token: str | None = None
    whatsapp_app_secret: str | None = None  # valida la firma de los POST de Meta
    whatsapp_access_token: str | None = None
    whatsapp_phone_number_id: str | None = None

    # Telegram: canal de prueba de la beta (docs/spec/canal-telegram.md)
    telegram_bot_token: str | None = None
    telegram_webhook_secret: str | None = None  # header X-Telegram-Bot-Api-Secret-Token
    telegram_allowed_user_ids: str = ""  # ids numéricos separados por coma

    # Operador / Dashboard (autenticación de endpoints de gestión de órdenes)
    operator_token: str | None = None

    # Base interna
    database_url: str = "sqlite:///./data/norma.db"
    checkpoint_db: str = "data/checkpoints.db"  # estado del grafo del agente

    # Trazas del agente en MLflow (internal/agent/observabilidad.py). Apagado: no se importa mlflow
    mlflow_enabled: bool = False
    mlflow_tracking_uri: str = "http://localhost:5001"  # en macOS el 5000 lo ocupa AirPlay
    mlflow_experiment: str = "norma-agente"
    # Guarda el texto de los mensajes y el prompt en la traza. Solo en desarrollo y con datos simulados (RNF-03)
    mlflow_trace_text: bool = False

    @model_validator(mode="after")
    def _texto_solo_en_desarrollo(self) -> "Settings":
        if self.mlflow_trace_text and self.app_env != "development":
            raise ValueError("MLFLOW_TRACE_TEXT solo se puede prender con APP_ENV=development (RNF-03)")
        return self

    @property
    def telegram_allowed_ids(self) -> set[int]:
        return {int(part) for part in self.telegram_allowed_user_ids.replace(" ", "").split(",") if part}


@lru_cache
def get_settings() -> Settings:
    return Settings()
