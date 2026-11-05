"""Estado del grafo del agente. Se guarda en el checkpointer: todo tiene que ser serializable."""

from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

Resultado = Literal["continuar", "pausar", "final"]


class EstadoNorma(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    customer_id: str
    customer_name: str | None
    channel: str
    inicio_turno: int  # posición en `messages` del mensaje que abrió el turno
    skills_activas: list[str]  # skills leídas en este turno: dan los permisos
    tools_disponibles: list[str]
    iteraciones: int  # llamadas al LLM en este turno
    fallas: int  # en toda la conversación
    falla_turno: bool
    propuesta: dict[str, Any] | None  # OrderDraft vigente, ya validado por el backend
    respuesta: str | None  # lo que va a ver el cliente
    kind: str | None
    options: list[str]
    order_id: str | None
    resultado: Resultado
