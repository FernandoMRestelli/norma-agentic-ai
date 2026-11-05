"""Crea el modelo de LangChain según LLM_PROVIDER. Es el único lugar que conoce al proveedor (RNF-09)."""

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI

from internal.config import get_settings

TIMEOUT_SECONDS = 30  # RNF-06
MAX_RETRIES = 1  # spec del agente §6


def get_chat_model() -> BaseChatModel:
    settings = get_settings()
    match settings.llm_provider:
        case "gemini":
            if not settings.llm_model or not settings.llm_api_key:
                raise ValueError("Para usar Gemini hacen falta LLM_MODEL y LLM_API_KEY en .env")
            return ChatGoogleGenerativeAI(
                model=settings.llm_model,
                api_key=settings.llm_api_key,
                timeout=TIMEOUT_SECONDS,
                max_retries=MAX_RETRIES,
            )
        case None | "":
            raise ValueError("Falta LLM_PROVIDER en .env (por ahora: gemini)")
        case other:
            raise ValueError(f"Proveedor de LLM no soportado: {other}")
