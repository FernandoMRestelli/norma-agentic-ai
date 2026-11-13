"""Único lugar que conoce la lista de tools: esquemas, permisos y datos inyectados (spec del agente §4.4)."""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import BaseModel, ValidationError

from internal.agent import guardrails
from internal.agent.state import Resultado
from internal.schemas.agent_schema import (
    ArmarPedidoInput,
    BuscarRenglonesInput,
    CheckStockInput,
    ExplorarCatalogoInput,
    GetProductInput,
    LeerBrInput,
    LeerSkillInput,
    RespondToCustomerInput,
    SearchProductsInput,
    ToolFailure,
)
from internal.schemas.catalog_schema import Product
from internal.schemas.order_schema import Channel, OrderDraft
from internal.services import order_service, validation_service
from internal.services.skill_service import SkillError, get_skills
from internal.tools import catalog_tools, inventory_tools, order_tools, reply_tools, skill_tools

logger = logging.getLogger(__name__)


@dataclass
class TurnContext:
    """Lo que las tools leen y cambian durante una iteración. El LLM nunca ve estos datos."""

    customer_id: str
    channel: Channel
    disponibles: list[str]
    customer_name: str | None = None  # van al borrador y a la orden (RF-45, RF-46)
    conversation_id: int | None = None
    skills_activas: list[str] = field(default_factory=list)
    br_entregadas: list[str] = field(default_factory=list)  # BR que ya vio el LLM en este turno (RF-63)
    renglones: list[dict[str, Any]] = field(default_factory=list)  # renglones numerados del pedido (RF-67)
    coberturas_fallidas: int = 0  # respuestas del turno rechazadas por dejar renglones sin cuenta (RF-68)
    propuesta: OrderDraft | None = None
    resultado: Resultado = "continuar"
    respuesta: str | None = None
    kind: str | None = None
    options: list[str] = field(default_factory=list)
    order_id: str | None = None
    turno: guardrails.ContextoTurno = field(default_factory=guardrails.ContextoTurno)  # para validar la salida


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    run: Callable[[Any, TurnContext], dict[str, Any]]


# Ni el número exacto de stock ni el stock mínimo son para el cliente: el LLM solo ve si hay o no stock
_SOLO_INTERNOS = {"stock", "min_stock", "is_low_stock"}


def _explorar_catalogo(data: ExplorarCatalogoInput, _: TurnContext) -> dict[str, Any]:
    return catalog_tools.explorar_catalogo(data.brand, data.category, data.limit)


def _al_llm(product: Product) -> dict[str, Any]:
    return {**product.model_dump(mode="json", exclude=_SOLO_INTERNOS), "hay_stock": product.stock > 0}


def _search_products(data: SearchProductsInput, _: TurnContext) -> dict[str, Any]:
    resultado = catalog_tools.buscar_productos(data.query, data.limit)
    return {
        "matches": [_al_llm(match) for match in resultado.matches],
        "sugerencias": [_al_llm(sugerencia) for sugerencia in resultado.sugerencias],
        "corregido": resultado.corregido,
        "total": resultado.total,
        "difieren_en": resultado.difieren_en,
    }


def _buscar_renglones(data: BuscarRenglonesInput, ctx: TurnContext) -> dict[str, Any]:
    resultados = catalog_tools.buscar_renglones([renglon.model_dump() for renglon in data.renglones])
    # Lo que la búsqueda dice de cada renglón queda en el estado: el tono va a la nota de la línea (RF-69) y la
    # coincidencia aproximada se guarda con el pedido
    numerados = {renglon["n"]: renglon for renglon in ctx.renglones}
    for buscado, resultado in zip(data.renglones, resultados, strict=True):
        if (renglon := numerados.get(buscado.n)) is not None:
            renglon["coincidencia"] = "aproximada" if resultado["estado"] == "aproximado" else "exacta"
            if buscado.tono:
                renglon["tono"] = buscado.tono
    return {"renglones": resultados}


def _get_product(data: GetProductInput, _: TurnContext) -> dict[str, Any]:
    product = catalog_tools.get_product(data.sku)
    return {
        "product": (
_al_llm(product) if product else None
        )
    }


def _check_stock(data: CheckStockInput, _: TurnContext) -> dict[str, Any]:
    return inventory_tools.check_stock(data.sku, data.quantity).model_dump(mode="json")


def _leer_skill(data: LeerSkillInput, ctx: TurnContext) -> dict[str, Any]:
    skill = skill_tools.leer_skill(data.nombre)
    if data.nombre not in ctx.skills_activas:
        ctx.skills_activas.append(data.nombre)
    return skill


def _leer_br(data: LeerBrInput, ctx: TurnContext) -> dict[str, Any]:
    regla = skill_tools.leer_br(data.id, ctx.skills_activas)
    if data.id not in ctx.br_entregadas:
        ctx.br_entregadas.append(data.id)
    return regla


# Después de esta cantidad de respuestas con renglones sin cuenta, la siguiente se acepta y lo que falta queda pendiente
MAX_COBERTURAS_FALLIDAS = 2


def _respond_to_customer(data: RespondToCustomerInput, ctx: TurnContext) -> dict[str, Any]:
    data = reply_tools.con_tonos(data, ctx.renglones)
    propuesta = reply_tools.respond_to_customer(
        data, ctx.customer_id, ctx.channel, ctx.propuesta, ctx.customer_name, ctx.conversation_id
    )
    # Con un pedido numerado, `options` sale de los renglones que se preguntan
    options = data.options or list(dict.fromkeys(sku for p in data.pendientes for sku in p.opciones))
    renglones = reply_tools.cubrir_renglones(
        data, ctx.renglones, tolerar=ctx.coberturas_fallidas >= MAX_COBERTURAS_FALLIDAS
    )
    message = data.message
    # Lo que escribió el LLM pasa por los guardrails antes de llegar al cliente; la vigente no cambia si falla
    rechazos = guardrails.validar_salida(message, data.kind.value, propuesta, options, ctx.turno, ctx.propuesta)
    if rechazos:
        ctx.turno.rechazos += 1
        if ctx.turno.rechazos <= guardrails.MAX_RECHAZOS:
            raise guardrails.rechazar(rechazos)
        logger.warning(
            "Guardrail: intentos agotados, sale el mensaje fijo (reglas=%s)", ",".join(r.regla for r in rechazos)
        )
        message = guardrails.mensaje_seguro(propuesta)
    respuesta = message
    if propuesta:
        # El detalle sale del catálogo, no del LLM: así entra un pedido de muchas líneas y los precios son exactos
        respuesta = f"{message}\n\n{validation_service.build_proposal_detail(propuesta)}"
    ctx.respuesta = respuesta
    ctx.kind = data.kind.value
    ctx.options = options
    ctx.renglones = renglones
    ctx.resultado = "pausar"
    # Recién acá la respuesta quedó aceptada: si algo antes la rechaza, `drafts` y la vigente no cambian (RF-46).
    # Todo control de la salida tiene que ir antes de este punto.
    if propuesta:
        order_service.record_draft(propuesta)
    elif ctx.propuesta:
        order_service.discard_draft(ctx.propuesta)
    ctx.propuesta = propuesta
    return {"enviado": True}


def _armar_pedido(_: ArmarPedidoInput, ctx: TurnContext) -> dict[str, Any]:
    order = order_tools.armar_pedido(ctx.propuesta)
    ctx.propuesta = None
    ctx.order_id = order.order_id
    ctx.respuesta = validation_service.build_order_message(order)
    ctx.kind = "pedido"
    ctx.resultado = "final"
    return {"order_id": order.order_id}


TOOLS: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in (
        ToolSpec(
            "explorar_catalogo",
            "Explora el catálogo por marcas o categorías cuando el cliente hace preguntas abiertas "
            "(por ejemplo: qué marcas de shampoo hay, qué productos tiene una marca, o qué líneas se trabajan). "
            "Devuelve resúmenes compactos agrupados por rubro o listas de marcas.",
            ExplorarCatalogoInput,
            _explorar_catalogo,
        ),
        ToolSpec(
            "search_products",
            "Busca en el catálogo por producto, marca, medida o tono, tolerando errores de tipeo y abreviaturas. "
            "`matches` son las coincidencias confiables, con precio, disponibilidad (hay_stock: bool) y `score` "
            "(de 0 a 1); `aproximado: true` quiere decir que algo no coincidió exacto y conviene confirmarlo. "
            "Si no hay matches, `sugerencias` trae los productos más parecidos, todos aproximados. `corregido` dice "
            "qué palabras se interpretaron como otra. Los números tienen que coincidir: 400 no trae 340. "
            "Si `matches` y `sugerencias` están vacías, no hay nada parecido. `total` dice cuántos productos "
            "coinciden igual de bien aunque veas menos, y `difieren_en` (marca, medida o variante) qué preguntar.",
            SearchProductsInput,
            _search_products,
        ),
        ToolSpec(
            "buscar_renglones",
            "Busca todos los renglones de un pedido en una sola llamada: usala cuando el pedido viene como lista. "
            "Por cada renglón devuelve `estado`, el `total` de coincidencias y hasta 3 `candidatos`. "
            "`unico`: es ese producto y alcanza el stock, va a la propuesta. `varias`: hay más de uno y `difieren_en` "
            "dice en qué (marca, medida o variante), preguntale. `aproximado`: se parece pero no es seguro, "
            "confirmalo. `sin_stock`: `falta` dice cuánto queda. `no_encontrado`: no hay nada parecido.",
            BuscarRenglonesInput,
            _buscar_renglones,
        ),
        ToolSpec(
            "get_product",
            "Devuelve un producto por su SKU exacto, o null si no existe.",
            GetProductInput,
            _get_product,
        ),
        ToolSpec(
            "check_stock",
            "Indica cuántas unidades hay de un SKU y si alcanzan para una cantidad.",
            CheckStockInput,
            _check_stock,
        ),
        ToolSpec(
            "leer_skill",
            "Lee una skill del índice: sus instrucciones, sus tools y sus BR. Habilita esas tools.",
            LeerSkillInput,
            _leer_skill,
        ),
        ToolSpec("leer_br", "Lee una regla de negocio de una skill ya leída.", LeerBrInput, _leer_br),
        ToolSpec(
            "respond_to_customer",
            "Le responde al cliente y termina el turno. Con `propuesta`, ese pedido queda como propuesta vigente. "
            "Sin ella, la vigente deja de valer.",
            RespondToCustomerInput,
            _respond_to_customer,
        ),
        ToolSpec(
            "armar_pedido",
            "Registra la propuesta vigente y termina el turno. El sistema le responde al cliente con el detalle y el "
            "número. Usala solo si el cliente aceptó la propuesta sin cambios.",
            ArmarPedidoInput,
            _armar_pedido,
        ),
    )
}

# Disponibles desde el inicio del turno: sin consultar el catálogo no se puede elegir una skill
BASE_TOOLS = (
    "leer_skill", "leer_br", "search_products", "buscar_renglones", "get_product", "check_stock", "explorar_catalogo"
)


def available(skills_activas: list[str]) -> list[str]:
    skills = get_skills()
    names = list(BASE_TOOLS)
    for nombre in skills_activas:
        names.extend(tool for tool in skills[nombre].tools if tool not in names)
    return names


def schemas(names: list[str]) -> list[dict[str, Any]]:
    result = []
    for name in names:
        spec = TOOLS[name]
        tool = convert_to_openai_tool(spec.input_model)
        tool["function"].update(name=spec.name, description=spec.description)
        result.append(tool)
    return result


def validate_skills() -> None:
    """Falla al arrancar si una skill nombra una tool que no existe (CA-40)."""
    for skill in get_skills().values():
        unknown = [tool for tool in skill.tools if tool not in TOOLS]
        if unknown:
            raise SkillError(f"La skill {skill.nombre} nombra tools inexistentes: {', '.join(unknown)}")


# Cada error le dice al LLM qué hacer: el system prompt solo le pide seguir `error.message`
_CATALOGO_NO_DISPONIBLE = (
    "No se pudo consultar el catálogo: decile al cliente que no pudiste consultarlo y que pruebe en unos minutos. "
    "No propongas nada"
)
_ERROR_INTERNO = (
    "La tool falló: decile al cliente que tuviste un problema y que pruebe en unos minutos. No propongas nada"
)


def _failure(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message, "details": details}}


_BR_POR_ESTADO = {"varias": "BR-03", "aproximado": "BR-09", "sin_stock": "BR-01", "no_encontrado": "BR-04"}


def _brs_que_dispara(name: str, result: dict[str, Any]) -> list[str]:
    """Las BR que corresponden al resultado de una tool: cada señal lleva a una sola (RF-63)."""
    if not result["ok"]:
        return ["BR-01"] if result["error"]["code"] == "stock_insuficiente" else []
    data = result["data"]
    if name == "check_stock":
        return [] if data["is_enough"] else ["BR-01"]
    if name == "buscar_renglones":
        estados = {renglon["estado"] for renglon in data["renglones"]}
        return [br for estado, br in _BR_POR_ESTADO.items() if estado in estados]
    if name != "search_products":
        return []
    matches, sugerencias = data["matches"], data["sugerencias"]
    if sugerencias or any(match.get("aproximado") for match in matches):
        return ["BR-09"]
    if not matches:
        return ["BR-04"]
    if len(matches) > 1:
        return ["BR-03"]
    return [] if matches[0]["hay_stock"] else ["BR-01"]


def _adjuntar_br(name: str, result: dict[str, Any], ctx: TurnContext) -> dict[str, Any]:
    """Suma al resultado el texto de las BR que dispara, una vez por turno y solo si una skill activa las permite.

    Sin una skill leída no se adjunta nada: el agente las lee con `leer_br`, como siempre.
    """
    reglas = []
    for br in _brs_que_dispara(name, result):
        if br in ctx.br_entregadas:
            continue
        try:
            reglas.append(skill_tools.leer_br(br, ctx.skills_activas))
        except ToolFailure:
            continue
        ctx.br_entregadas.append(br)
    return {**result, "reglas": reglas} if reglas else result


def execute(name: str, arguments: dict[str, Any], ctx: TurnContext) -> dict[str, Any]:
    """Ejecuta una tool y devuelve el sobre común, con la BR que dispara el resultado si corresponde (RF-63)."""
    return _adjuntar_br(name, _run(name, arguments, ctx), ctx)


def _run(name: str, arguments: dict[str, Any], ctx: TurnContext) -> dict[str, Any]:
    """Ejecuta una tool y devuelve el sobre común. Un error vuelve al LLM para que lo corrija."""
    if name not in ctx.disponibles or name not in TOOLS:
        return _failure(
            "tool_no_permitida", f"La tool {name!r} no está disponible: leé con leer_skill la skill que la permite"
        )
    spec = TOOLS[name]
    try:
        data = spec.input_model.model_validate(arguments)
        return {"ok": True, "data": spec.run(data, ctx)}
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_input=False, include_context=False)
        return _failure(
            "argumentos_invalidos",
            "Los argumentos no cumplen el esquema: corregilos según `errores` y volvé a llamar a la tool",
            errores=errors,
        )
    except ToolFailure as exc:
        return _failure(exc.code, exc.message, **exc.details)
    except OSError:
        logger.exception("Tool %s: no se pudo leer el catálogo", name)
        return _failure("catalogo_no_disponible", _CATALOGO_NO_DISPONIBLE)
    except Exception:
        logger.exception("Tool %s: error inesperado", name)
        return _failure("error_interno", _ERROR_INTERNO)
