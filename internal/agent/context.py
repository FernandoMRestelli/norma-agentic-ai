"""Arma lo que recibe el LLM en cada iteración (spec del agente §8)."""

from pathlib import Path

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage

from internal.agent.state import EstadoNorma
from internal.schemas.order_schema import OrderDraft
from internal.services.skill_service import get_skills

SYSTEM_PROMPT_PATH = Path(__file__).with_name("system_prompt.md")
MAX_HISTORY = 20


def load_system_prompt() -> str:
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


def _skills_index() -> str:
    lines = [f"- `{skill.nombre}`: {skill.cuando}" for skill in get_skills().values()]
    return "## Índice de skills\n\n" + "\n".join(lines)


def _current_state(state: EstadoNorma) -> str:
    name = " ".join((state.get("customer_name") or "").split()) or "desconocido"
    lines = ["## Estado actual", "", f"Nombre del cliente según su perfil: {name}"]
    if propuesta := state.get("propuesta"):
        draft = OrderDraft.model_validate(propuesta)
        items = "; ".join(
            f"{item.quantity} x {' '.join(filter(None, (item.product_name, item.presentation)))} ({item.sku})"
            for item in draft.items
        )
        lines.append(f"Propuesta vigente: {items}")
    else:
        lines.append("Propuesta vigente: ninguna")
    return "\n".join(lines)


def _previous_history(messages: list[AnyMessage]) -> list[AnyMessage]:
    """De los turnos anteriores solo queda la charla: las lecturas y tools valían para su turno."""
    history: list[AnyMessage] = []
    for message in messages:
        if isinstance(message, HumanMessage):
            history.append(message)
        elif isinstance(message, AIMessage) and not message.tool_calls and message.text.strip():
            history.append(AIMessage(content=message.text))
    history = history[-MAX_HISTORY:]
    while history and not isinstance(history[0], HumanMessage):  # el modelo espera empezar con el cliente
        history.pop(0)
    return history


def build_messages(state: EstadoNorma) -> list[AnyMessage]:
    start = state.get("inicio_turno", 0)
    system = "\n\n".join((load_system_prompt(), _skills_index(), _current_state(state)))
    return [SystemMessage(content=system), *_previous_history(state["messages"][:start]), *state["messages"][start:]]
