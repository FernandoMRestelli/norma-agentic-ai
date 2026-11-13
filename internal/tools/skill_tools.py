"""Tools para leer skills y BR. Solo comparan lo que manda el LLM contra el índice cargado al arrancar."""

from typing import Any

from internal.schemas.agent_schema import ToolFailure
from internal.services.skill_service import get_skills


def leer_skill(nombre: str) -> dict[str, Any]:
    skill = get_skills().get(nombre)
    if skill is None:
        raise ToolFailure("skill_inexistente", f"No existe la skill {nombre!r}: elegí una del índice")
    result = {
        "nombre": skill.nombre,
        "instrucciones": skill.instrucciones,
        "tools": skill.tools,
        "br": sorted(skill.br),
    }
    if skill.datos_negocio is not None:  # null es un dato que el negocio no confirmó (RF-57)
        result["datos_negocio"] = skill.datos_negocio
    return result


def leer_br(id: str, skills_activas: list[str]) -> dict[str, Any]:
    """Devuelve una BR si la permite alguna skill leída en este turno."""
    skills = get_skills()
    for nombre in skills_activas:
        if id in skills[nombre].br:
            return {"id": id, "texto": skills[nombre].br[id]}
    if any(id in skill.br for skill in skills.values()):
        raise ToolFailure(
            "br_no_permitida", f"Ninguna skill leída en este turno permite {id!r}: leé primero la skill que la nombra"
        )
    raise ToolFailure("br_inexistente", f"No existe la BR {id!r}: leé solo las BR que nombra la skill")
