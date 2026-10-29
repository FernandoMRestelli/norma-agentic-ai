"""Interfaz neutral para el LLM: el agente depende de esto, no de un proveedor concreto.

Cuando se elija proveedor, se agrega una clase que implemente `LLMClient`
(p. ej. `OpenAIClient`, `ClaudeClient`) y se selecciona según `LLM_PROVIDER`.
"""

from typing import Any, Dict, List, Optional, Protocol

from pydantic import BaseModel


class ToolCall(BaseModel):
    name: str
    arguments: Dict[str, Any]


class LLMResponse(BaseModel):
    text: Optional[str] = None
    tool_calls: List[ToolCall] = []


class LLMClient(Protocol):
    def chat(
        self,
        system: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> LLMResponse: ...


def get_llm_client() -> LLMClient:
    # TODO: devolver la implementación según get_settings().llm_provider
    raise NotImplementedError("Proveedor de LLM todavía no definido")
