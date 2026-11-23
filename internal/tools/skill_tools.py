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


def leer_skill(nombre: str, orquestador: bool = False, hay_propuesta: bool = True) -> dict[str, Any]:
    skills = get_skills()
    skill = skills.get(nombre)
    if skill is None:
        # Las mismas que muestra el índice: sin propuesta vigente, no las que la necesitan
        indice = ", ".join(s.nombre for s in skills.values() if hay_propuesta or not s.requiere_propuesta)
        raise ToolFailure("skill_inexistente", f"No existe la skill {nombre!r}. Las del índice: {indice}")
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
    if duenas := [skill.nombre for skill in skills.values() if id in skill.br]:
        raise ToolFailure(
            "br_no_permitida",
            f"{id!r} es de la skill {' o '.join(duenas)}, que no leíste en este turno: si corresponde a este caso, "
            "leela primero con leer_skill",
        )
    leibles = [f"{br['id']} ({br['titulo']})" for nombre in skills_activas for br in skills[nombre].titulos_br]
    if not leibles:
        raise ToolFailure("br_inexistente", f"No existe la BR {id!r}: leé primero una skill del índice")
    raise ToolFailure("br_inexistente", f"No existe la BR {id!r}. Las de las skills leídas: {', '.join(leibles)}")
