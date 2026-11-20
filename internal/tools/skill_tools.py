"""Tools para leer skills y BR. Solo comparan lo que manda el LLM contra el índice cargado al arrancar."""

from typing import Any

from internal.schemas.agent_schema import ToolFailure
from internal.services.skill_service import get_skills

DELEGAR = (
    "Esta skill se delega: el trabajo de buscar y resolver lo hace su sub-loop, con su propio contexto. Llamá a "
    "`delegar_skill` con un encargo corto y los renglones que tiene que resolver; su nota vuelve como resultado. "
    "Una consulta corta (un producto, un precio) resolvela vos con las tools de catálogo. Con la nota, respondé "
    "así:"
)


def instrucciones_orquestador(nombre: str) -> str:
    """Lo que ve el orquestador de una skill `sub_loop`: cómo delegarla y cómo responder con su nota (§4.6)."""
    skill = get_skills()[nombre]
    return f"{DELEGAR}\n\n{skill.respuesta}"


def leer_skill(nombre: str, orquestador: bool = False) -> dict[str, Any]:
    skill = get_skills().get(nombre)
    if skill is None:
        raise ToolFailure("skill_inexistente", f"No existe la skill {nombre!r}: elegí una del índice")
    delegada = orquestador and skill.modo == "sub_loop"
    result = {
        "nombre": skill.nombre,
        "instrucciones": instrucciones_orquestador(nombre) if delegada else skill.instrucciones,
        "tools": skill.tools_inline,
        "br": skill.titulos_br,
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
