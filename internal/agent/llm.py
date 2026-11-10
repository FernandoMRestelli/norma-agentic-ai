"""Crea el modelo de LangChain según LLM_PROVIDER. Es el único lugar que conoce al proveedor (RNF-09)."""

from langchain_core.language_models.chat_models import BaseChatModel

from internal.config import get_settings

TIMEOUT_SECONDS = 30  # RNF-06
MAX_RETRIES = 1  # spec del agente §6

# base_url de OpenRouter: expone una API compatible con OpenAI, así que se usa ChatOpenAI apuntando acá.
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def get_chat_model() -> BaseChatModel:
    settings = get_settings()
    match settings.llm_provider:
        case "gemini":
            # Import local: cada proveedor trae su propio paquete y no queremos
            # pagar el import si no se usa (ni romper si no está instalado).
            from langchain_google_genai import ChatGoogleGenerativeAI

            if not settings.llm_model or not settings.llm_api_key:
                raise ValueError("Para usar Gemini hacen falta LLM_MODEL y LLM_API_KEY en .env")
            return ChatGoogleGenerativeAI(
                model=settings.llm_model,
                api_key=settings.llm_api_key,
                timeout=TIMEOUT_SECONDS,
                max_retries=MAX_RETRIES,
            )
        case "groq":
            from langchain_groq import ChatGroq

            if not settings.llm_model or not settings.llm_api_key:
                raise ValueError("Para usar Groq hacen falta LLM_MODEL y LLM_API_KEY en .env")
            return ChatGroq(
                model=settings.llm_model,
                api_key=settings.llm_api_key,
                timeout=TIMEOUT_SECONDS,
                max_retries=MAX_RETRIES,
            )
        case "openrouter":
            from langchain_openai import ChatOpenAI

            if not settings.llm_model or not settings.llm_api_key:
                raise ValueError("Para usar OpenRouter hacen falta LLM_MODEL y LLM_API_KEY en .env")
            return ChatOpenAI(
                model=settings.llm_model,
                api_key=settings.llm_api_key,
                base_url=OPENROUTER_BASE_URL,
                timeout=TIMEOUT_SECONDS,
                max_retries=MAX_RETRIES,
            )
        case None | "":
            raise ValueError("Falta LLM_PROVIDER en .env (opciones: gemini, groq, openrouter)")
        case other:
            raise ValueError(f"Proveedor de LLM no soportado: {other}")
