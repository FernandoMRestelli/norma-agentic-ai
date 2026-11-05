"""CA-36: el modelo se elige con LLM_PROVIDER, sin llamar a ningún proveedor."""

import pytest
from langchain_google_genai import ChatGoogleGenerativeAI

from internal.agent.llm import get_chat_model
from internal.config import get_settings


@pytest.fixture
def settings(monkeypatch):
    def configurar(**values):
        for key, value in values.items():
            monkeypatch.setenv(key, value)
        get_settings.cache_clear()

    yield configurar
    get_settings.cache_clear()


def test_gemini_crea_el_modelo_de_langchain(settings):
    settings(LLM_PROVIDER="gemini", LLM_MODEL="gemini-de-prueba", LLM_API_KEY="clave-de-prueba")
    assert isinstance(get_chat_model(), ChatGoogleGenerativeAI)


def test_gemini_sin_clave_dice_que_falta(settings):
    settings(LLM_PROVIDER="gemini", LLM_MODEL="gemini-de-prueba")
    with pytest.raises(ValueError, match="LLM_API_KEY"):
        get_chat_model()


def test_sin_proveedor_dice_que_falta(settings):
    settings()
    with pytest.raises(ValueError, match="LLM_PROVIDER"):
        get_chat_model()


def test_proveedor_no_soportado(settings):
    settings(LLM_PROVIDER="otro")
    with pytest.raises(ValueError, match="no soportado"):
        get_chat_model()
