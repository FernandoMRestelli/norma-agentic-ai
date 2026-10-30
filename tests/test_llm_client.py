import json

import httpx
import pytest

from backend.agent.llm_client import GeminiClient, LLMError, ToolCall, get_llm_client
from backend.config import get_settings

SEARCH_TOOL = {
    "name": "search_products",
    "description": "Busca productos en el catálogo",
    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
}


def gemini_with(response: httpx.Response | dict, requests: list | None = None) -> GeminiClient:
    """GeminiClient contra una API falsa que devuelve `response` y guarda cada request en `requests`."""

    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        return response if isinstance(response, httpx.Response) else httpx.Response(200, json=response)

    return GeminiClient(
        "clave-de-prueba", "gemini-de-prueba", http=httpx.Client(transport=httpx.MockTransport(handler))
    )


def test_gemini_traduce_tools_historial_y_respuesta():
    """CA-35: tools, ToolCall con id y signature, y resultado de la tool, ida y vuelta."""
    requests = []
    respuesta = {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [
                        {"text": "pensando", "thought": True},
                        {
                            "functionCall": {"id": "c2", "name": "check_stock", "args": {"sku": "SH-PAN-400"}},
                            "thoughtSignature": "firma-2",
                        },
                    ],
                }
            }
        ]
    }
    historial = [
        {"role": "user", "content": "Quiero 5 shampoo pantene 400"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                ToolCall(id="c1", name="search_products", arguments={"query": "pantene"}, signature="firma-1")
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "name": "search_products", "content": {"ok": True, "data": {}}},
    ]

    result = gemini_with(respuesta, requests).chat("instrucciones", historial, tools=[SEARCH_TOOL])

    assert result.text is None  # las partes de pensamiento no son texto para el cliente
    assert result.tool_calls == [
        ToolCall(id="c2", name="check_stock", arguments={"sku": "SH-PAN-400"}, signature="firma-2")
    ]

    (request,) = requests
    assert request.url.path == "/v1beta/models/gemini-de-prueba:generateContent"
    assert request.headers["x-goog-api-key"] == "clave-de-prueba"
    assert "clave-de-prueba" not in str(request.url)
    body = json.loads(request.content)
    assert body["systemInstruction"] == {"parts": [{"text": "instrucciones"}]}
    assert body["tools"] == [
        {
            "functionDeclarations": [
                {
                    "name": "search_products",
                    "description": "Busca productos en el catálogo",
                    "parametersJsonSchema": SEARCH_TOOL["parameters"],
                }
            ]
        }
    ]
    assert body["contents"] == [
        {"role": "user", "parts": [{"text": "Quiero 5 shampoo pantene 400"}]},
        {
            "role": "model",
            "parts": [
                {
                    "functionCall": {"name": "search_products", "args": {"query": "pantene"}, "id": "c1"},
                    "thoughtSignature": "firma-1",
                }
            ],
        },
        {
            "role": "user",
            "parts": [
                {"functionResponse": {"name": "search_products", "response": {"ok": True, "data": {}}, "id": "c1"}}
            ],
        },
    ]


def test_gemini_agrupa_resultados_de_tools_de_la_misma_iteracion():
    requests = []
    historial = [
        {"role": "user", "content": "hola"},
        {"role": "assistant", "tool_calls": [{"name": "a", "arguments": {}}, {"name": "b", "arguments": {}}]},
        {"role": "tool", "name": "a", "content": {"ok": True}},
        {"role": "tool", "name": "b", "content": {"ok": True}},
    ]
    gemini_with({"candidates": [{"content": {"parts": [{"text": "listo"}]}}]}, requests).chat("s", historial)

    contents = json.loads(requests[0].content)["contents"]
    assert len(contents) == 3
    assert [part["functionResponse"]["name"] for part in contents[2]["parts"]] == ["a", "b"]


def test_gemini_devuelve_texto():
    result = gemini_with(
        {"candidates": [{"content": {"parts": [{"text": "Hola, "}, {"text": "¿qué necesitás?"}]}}]}
    ).chat("s", [{"role": "user", "content": "hola"}])
    assert result.text == "Hola, ¿qué necesitás?"
    assert result.tool_calls == []


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(429, json={"error": {"message": "Quota exceeded"}}),
        httpx.Response(500, text="error"),
        httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}),
    ],
)
def test_gemini_convierte_fallas_en_llm_error(response):
    with pytest.raises(LLMError):
        gemini_with(response).chat("s", [{"role": "user", "content": "hola"}])


def test_gemini_sin_conexion_es_llm_error():
    def handler(request):
        raise httpx.ConnectError("sin red")

    client = GeminiClient("k", "m", http=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(LLMError):
        client.chat("s", [{"role": "user", "content": "hola"}])


@pytest.fixture
def llm_env(monkeypatch):
    def configurar(**variables):
        for nombre, valor in variables.items():
            monkeypatch.setenv(nombre, valor)
        get_settings.cache_clear()

    yield configurar
    get_settings.cache_clear()


def test_get_llm_client_gemini(llm_env):
    """CA-36: LLM_PROVIDER=gemini con modelo y clave devuelve GeminiClient."""
    llm_env(LLM_PROVIDER="gemini", LLM_MODEL="gemini-2.5-flash", LLM_API_KEY="clave")
    client = get_llm_client()
    assert isinstance(client, GeminiClient)
    assert client.model == "gemini-2.5-flash"


@pytest.mark.parametrize(
    ("variables", "mensaje"),
    [
        ({}, "LLM_PROVIDER"),
        ({"LLM_PROVIDER": "gemini", "LLM_MODEL": "gemini-2.5-flash"}, "LLM_API_KEY"),
        ({"LLM_PROVIDER": "otro"}, "no soportado"),
    ],
)
def test_get_llm_client_errores_de_configuracion(llm_env, variables, mensaje):
    """CA-36: sin proveedor, sin clave o con un proveedor desconocido, el error dice qué falta."""
    llm_env(**variables)
    with pytest.raises(ValueError, match=mensaje):
        get_llm_client()
