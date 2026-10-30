"""Interfaz neutral para el LLM: el agente depende de esto, no de un proveedor concreto.

Cada proveedor es una clase que implementa `LLMClient` y se elige según `LLM_PROVIDER`.
El formato neutral de mensajes y tools está en docs/spec/canal-telegram.md §4.3:

- tool: {"name", "description", "parameters"} con `parameters` en JSON Schema
- {"role": "user", "content": str}
- {"role": "assistant", "content": str | None, "tool_calls": [ToolCall]}
- {"role": "tool", "tool_call_id": str | None, "name": str, "content": dict}
"""

from typing import Any, Protocol

import httpx
from pydantic import BaseModel

from backend.config import get_settings

TIMEOUT_SECONDS = 30  # RNF-06


class ToolCall(BaseModel):
    name: str
    arguments: dict[str, Any]
    id: str | None = None  # empareja el resultado de la tool con la llamada
    signature: str | None = None  # dato opaco del proveedor: se devuelve tal cual en el historial


class LLMResponse(BaseModel):
    text: str | None = None
    tool_calls: list[ToolCall] = []


class LLMError(RuntimeError):
    """El proveedor falló o no respondió a tiempo. El reintento lo decide el agente."""


class LLMClient(Protocol):
    def chat(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse: ...


GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class GeminiClient:
    """Gemini por la API REST `generateContent`. La API key va en un header, nunca en la URL (RNF-14)."""

    def __init__(self, api_key: str, model: str, http: httpx.Client | None = None):
        self.model = model
        self.http = http or httpx.Client(timeout=TIMEOUT_SECONDS)
        self._headers = {"x-goog-api-key": api_key}

    def chat(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        body: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": _to_gemini_contents(messages),
        }
        if tools:
            declarations = [
                {"name": t["name"], "description": t["description"], "parametersJsonSchema": t["parameters"]}
                for t in tools
            ]
            body["tools"] = [{"functionDeclarations": declarations}]

        try:
            response = self.http.post(GEMINI_URL.format(model=self.model), headers=self._headers, json=body)
        except httpx.HTTPError as exc:
            raise LLMError(f"No se pudo llamar a Gemini ({type(exc).__name__})") from exc
        if response.is_error:
            detail = response.json().get("error", {}).get("message", "") if _is_json(response) else ""
            raise LLMError(f"Gemini respondió {response.status_code}: {detail}".rstrip(": "))
        return _from_gemini(response.json())


def _to_gemini_contents(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    contents: list[dict[str, Any]] = []
    for message in messages:
        match message["role"]:
            case "user":
                contents.append({"role": "user", "parts": [{"text": message["content"]}]})
            case "assistant":
                parts: list[dict[str, Any]] = []
                if message.get("content"):
                    parts.append({"text": message["content"]})
                for raw in message.get("tool_calls", []):
                    call = ToolCall.model_validate(raw)
                    function_call: dict[str, Any] = {"name": call.name, "args": call.arguments}
                    if call.id:
                        function_call["id"] = call.id
                    part: dict[str, Any] = {"functionCall": function_call}
                    if call.signature:
                        part["thoughtSignature"] = call.signature
                    parts.append(part)
                contents.append({"role": "model", "parts": parts})
            case "tool":
                function_response: dict[str, Any] = {"name": message["name"], "response": message["content"]}
                if message.get("tool_call_id"):
                    function_response["id"] = message["tool_call_id"]
                part = {"functionResponse": function_response}
                # Los resultados de varias tools llamadas en la misma iteración van juntos en un solo turno
                previous = contents[-1] if contents else None
                if previous and previous["role"] == "user" and "functionResponse" in previous["parts"][0]:
                    previous["parts"].append(part)
                else:
                    contents.append({"role": "user", "parts": [part]})
            case other:
                raise ValueError(f"Rol de mensaje desconocido: {other}")
    return contents


def _from_gemini(data: dict[str, Any]) -> LLMResponse:
    candidates = data.get("candidates") or []
    if not candidates:
        reason = data.get("promptFeedback", {}).get("blockReason", "sin candidatos")
        raise LLMError(f"Gemini no devolvió respuesta ({reason})")

    texts: list[str] = []
    tool_calls: list[ToolCall] = []
    for part in candidates[0].get("content", {}).get("parts", []):
        if "functionCall" in part:
            call = part["functionCall"]
            tool_calls.append(
                ToolCall(
                    name=call["name"],
                    arguments=call.get("args") or {},
                    id=call.get("id"),
                    signature=part.get("thoughtSignature"),
                )
            )
        elif "text" in part and not part.get("thought"):
            texts.append(part["text"])
    return LLMResponse(text="".join(texts) or None, tool_calls=tool_calls)


def _is_json(response: httpx.Response) -> bool:
    return response.headers.get("content-type", "").startswith("application/json")


def get_llm_client() -> LLMClient:
    settings = get_settings()
    match settings.llm_provider:
        case "gemini":
            if not settings.llm_model or not settings.llm_api_key:
                raise ValueError("Para usar Gemini hacen falta LLM_MODEL y LLM_API_KEY en .env")
            return GeminiClient(settings.llm_api_key, settings.llm_model)
        case None | "":
            raise ValueError("Falta LLM_PROVIDER en .env (por ahora: gemini)")
        case other:
            raise ValueError(f"Proveedor de LLM no soportado: {other}")
