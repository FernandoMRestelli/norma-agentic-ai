"""Único lugar que conoce la lista de tools: esquemas, permisos y datos inyectados (spec del agente §4.4)."""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import BaseModel, ValidationError

from internal.agent import guardrails, nota
from internal.agent.state import Resultado
from internal.config import get_settings
from internal.schemas.agent_schema import (
    ArmarPedidoInput,
    BuscarRenglonesInput,
    CheckStockInput,
    DelegarSkillInput,
    DiscardOrderDraftInput,
    EntregarNotaInput,
    EscalateToHumanInput,
    ExplorarCatalogoInput,
    GetProductInput,
    LeerBrInput,
    LeerSkillInput,
    PedidosAnterioresInput,
    RespondToCustomerInput,
    SearchProductsInput,
    ToolFailure,
)
from internal.schemas.catalog_schema import Product
from internal.schemas.order_schema import Channel, OrderDraft
from internal.services import order_service, validation_service
from internal.services.skill_service import TOOL_RESPUESTA, Skill, SkillError, get_skills
from internal.tools import catalog_tools, inventory_tools, order_tools, reply_tools, skill_tools

logger = logging.getLogger(__name__)


def orquestador_activo() -> bool:
    """El turno corre con orquestador y sub-loops (AGENT_ORQUESTADOR, RF-95)."""
    return get_settings().agent_orquestador


class FallaSubLoop(Exception):
    """Falló el LLM dentro de un sub-loop: es una falla del turno, como una del orquestador (§7)."""


# Corre el sub-loop de una skill y devuelve su entrada para `notas_turno`. La arma el grafo, que tiene el modelo
Delegar = Callable[[Skill, str, list[int], "TurnContext"], dict[str, Any]]


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
    origen: str = "texto"  # texto, audio o imagen: queda en la orden (RF-79)
    comprados: frozenset[str] | None = None  # SKU que el cliente ya pidió; se lee de la base la primera vez (RF-73)
    coberturas_fallidas: int = 0  # respuestas del turno rechazadas por dejar renglones sin cuenta (RF-68)
    propuesta: OrderDraft | None = None
    resultado: Resultado = "continuar"
    respuesta: str | None = None
    kind: str | None = None
    options: list[str] = field(default_factory=list)
    order_id: str | None = None
    derivation_reason: str | None = None
    descuento_autorizado: float | None = None
    condicion_comercial: str | None = None
    turno: guardrails.ContextoTurno = field(default_factory=guardrails.ContextoTurno)  # para validar la salida
    orquestador: bool = False  # con el orquestador, `leer_skill` de una skill sub_loop dice cómo delegarla
    notas_turno: list[dict[str, Any]] = field(default_factory=list)  # la nota de búsqueda y las de los sub-loops
    iteraciones_sub_loop: int = 0  # llamadas al LLM de los sub-loops en este turno (RF-93)
    delegar: Delegar | None = None


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    run: Callable[[Any, TurnContext], dict[str, Any]]


# Ni el número exacto de stock ni el stock mínimo son para el cliente: el LLM solo ve si hay o no stock
_SOLO_INTERNOS = {"stock", "min_stock", "is_low_stock", "comprado_antes"}


def _explorar_catalogo(data: ExplorarCatalogoInput, _: TurnContext) -> dict[str, Any]:
    return catalog_tools.explorar_catalogo(data.brand, data.category, data.limit)


def _al_llm(product: Product) -> dict[str, Any]:
    datos = {**product.model_dump(mode="json", exclude=_SOLO_INTERNOS), "hay_stock": product.stock > 0}
    # Solo cuando es cierto: es una pista para preguntar («¿el de 900, como la otra vez?»), no un dato de cada producto
    return {**datos, "comprado_antes": True} if getattr(product, "comprado_antes", False) else datos


def _comprados(ctx: TurnContext) -> frozenset[str]:
    """Lo que el cliente ya compró desempata sus búsquedas. Si la base falla, se busca sin esa ayuda."""
    if ctx.comprados is None:
        try:
            ctx.comprados = order_service.skus_comprados(ctx.customer_id)
        except Exception:
            logger.warning("No se pudo leer el historial del cliente para ordenar la búsqueda", exc_info=True)
            ctx.comprados = frozenset()
    return ctx.comprados


def _pedidos_anteriores(data: PedidosAnterioresInput, ctx: TurnContext) -> dict[str, Any]:
    return {"pedidos": order_tools.pedidos_anteriores(ctx.customer_id, data.limite)}


def _search_products(data: SearchProductsInput, ctx: TurnContext) -> dict[str, Any]:
    resultado = catalog_tools.buscar_productos(data.query, data.limit, _comprados(ctx))
    salida = {
        "matches": [_al_llm(match) for match in resultado.matches],
        "sugerencias": [_al_llm(sugerencia) for sugerencia in resultado.sugerencias],
        "corregido": resultado.corregido,
        "total": resultado.total,
        "difieren_en": resultado.difieren_en,
    }
    if hint := catalog_tools.hint_busqueda(resultado):
        salida["_hint"] = hint  # por qué no hubo una coincidencia segura, para no volver a buscar a ciegas
    return salida


def _buscar_renglones(data: BuscarRenglonesInput, ctx: TurnContext) -> dict[str, Any]:
    resultados = catalog_tools.buscar_renglones(
        [renglon.model_dump() for renglon in data.renglones], _comprados(ctx)
    )
    anotar_busqueda(ctx.renglones, [renglon.model_dump() for renglon in data.renglones], resultados)
    return {"renglones": resultados}


def anotar_busqueda(
    renglones: list[dict[str, Any]], buscados: list[dict[str, Any]], resultados: list[dict[str, Any]]
) -> None:
    """Lo que la búsqueda dice de cada renglón queda en el estado: el tono va a la nota de la línea (RF-69) y la
    coincidencia aproximada se guarda con el pedido. La usan `buscar_renglones` y la precarga (RF-86)."""
    numerados = {renglon["n"]: renglon for renglon in renglones}
    for buscado, resultado in zip(buscados, resultados, strict=True):
        if (renglon := numerados.get(buscado["n"])) is not None:
            renglon["coincidencia"] = "aproximada" if resultado["estado"] == "aproximado" else "exacta"
            if buscado.get("tono"):
                renglon["tono"] = buscado["tono"]


def _get_product(data: GetProductInput, _: TurnContext) -> dict[str, Any]:
    product = catalog_tools.get_product(data.sku)
    if product is None:
        return {
            "product": None,
            "_hint": f"No existe un producto con el SKU {data.sku}. Los SKU salen de una búsqueda: buscalo con "
            "search_products por tipo, marca y medida; no pruebes SKU de memoria.",
        }
    return {"product": _al_llm(product)}


def _check_stock(data: CheckStockInput, _: TurnContext) -> dict[str, Any]:
    return inventory_tools.check_stock(data.sku, data.quantity).model_dump(mode="json")


def _leer_skill(data: LeerSkillInput, ctx: TurnContext) -> dict[str, Any]:
    skill = skill_tools.leer_skill(data.nombre, ctx.orquestador)
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
        data, ctx.customer_id, ctx.channel, ctx.propuesta, ctx.customer_name, ctx.conversation_id, ctx.renglones,
        ctx.origen,
    )
    # Con un pedido numerado, `options` sale de los renglones que se preguntan
    options = data.options or list(dict.fromkeys(sku for p in data.pendientes for sku in p.opciones))
    renglones = reply_tools.cubrir_renglones(
        data, ctx.renglones, tolerar=ctx.coberturas_fallidas >= MAX_COBERTURAS_FALLIDAS
    )
    if propuesta:
        # Lo que quedó afuera viaja con la propuesta: si el cliente la acepta, la orden lo guarda (RF-76)
        propuesta = propuesta.model_copy(
            update={
                "leftovers": reply_tools.fuera_del_pedido(renglones),
                "discount_percentage": ctx.descuento_autorizado,
            }
        )
    message = data.message
    # Lo que escribió el LLM pasa por los guardrails antes de llegar al cliente; la vigente no cambia si falla
    rechazos = guardrails.validar_salida(
        message, data.kind.value, propuesta, options, ctx.turno, ctx.propuesta,
        pendientes=[pendiente.renglon for pendiente in data.pendientes],
        descuento_autorizado=ctx.descuento_autorizado,
    )  # fmt: skip
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


def _delegar_skill(data: DelegarSkillInput, ctx: TurnContext) -> dict[str, Any]:
    """Corre el sub-loop de la skill y devuelve su nota validada, sin los intentos (§4.2, RF-89 a RF-91)."""
    skill = get_skills().get(data.skill)
    if skill is None:
        raise ToolFailure("skill_inexistente", f"No existe la skill {data.skill!r}: elegí una del índice")
    if skill.modo != "sub_loop":
        raise ToolFailure(
            "skill_no_delegable",
            f"La skill {data.skill!r} no se delega: leela con `leer_skill` y seguí sus instrucciones vos",
        )
    existentes = {renglon["n"] for renglon in ctx.renglones}
    if desconocidos := sorted({n for n in data.renglones if n not in existentes}):
        raise ToolFailure(
            "renglon_inexistente",
            f"No existen los renglones {desconocidos}: usá los números del estado actual",
            renglones=desconocidos,
        )
    if ctx.delegar is None:
        raise ToolFailure("delegar_no_disponible", "En este turno no se puede delegar: resolvelo con tus tools")
    entrada = ctx.delegar(skill, data.encargo, list(dict.fromkeys(data.renglones)), ctx)
    ctx.notas_turno.append(entrada)
    guardrails.sumar_notas(ctx.turno, [entrada])  # lo que encontró el sub-loop puede ir en la respuesta
    # El tono que resolvió el sub-loop va a la nota de la línea, como el de `buscar_renglones` (RF-69)
    numerados = {renglon["n"]: renglon for renglon in ctx.renglones}
    for resuelto in entrada["nota"]["resueltos"]:
        if resuelto.get("tono") and resuelto.get("renglon") in numerados:
            numerados[resuelto["renglon"]]["tono"] = resuelto["tono"]
    if skill.nombre not in ctx.skills_activas:  # sus BR y su respuesta al cliente quedan habilitadas
        ctx.skills_activas.append(skill.nombre)
    return nota.para_orquestador(entrada)


def _entregar_nota(_: EntregarNotaInput, __: TurnContext) -> dict[str, Any]:
    """La ejecuta el sub-loop, que la valida (internal/agent/sub_loop.py). Acá solo está su esquema."""
    raise ToolFailure("tool_no_permitida", "`entregar_nota` solo se usa dentro de un sub-loop")


PRECIO_CAMBIADO = (
    "Desde que te armé el pedido cambió el precio de algún producto. Te lo paso con los precios de hoy. "
    "¿Lo confirmo así?"
)


def _armar_pedido(_: ArmarPedidoInput, ctx: TurnContext) -> dict[str, Any]:
    try:
        order = order_tools.armar_pedido(ctx.propuesta)
    except order_service.PriceChangedError as exc:
        # No se registra con un total que el cliente no vio: la misma propuesta sale de nuevo con los precios de
        # hoy, armada por el sistema, y el cliente la confirma otra vez (RF-72)
        nueva = order_service.actualizar_precios(ctx.propuesta)
        order_service.record_draft(nueva)
        ctx.propuesta = nueva
        ctx.respuesta = f"{PRECIO_CAMBIADO}\n\n{validation_service.build_proposal_detail(nueva)}"
        ctx.kind = "propuesta"
        ctx.resultado = "pausar"
        return {"precio_cambiado": True, "lineas": exc.lines}
    ctx.propuesta = None
    ctx.order_id = order.order_id
    ctx.respuesta = validation_service.build_order_message(order)
    ctx.kind = "pedido"
    ctx.resultado = "final"
    return {"order_id": order.order_id}


def _discard_order_draft(data: DiscardOrderDraftInput, ctx: TurnContext) -> dict[str, Any]:
    mensaje, _ = reply_tools.discard_order_draft(data.reason, ctx.propuesta, data.message)
    ctx.propuesta = None
    ctx.respuesta = mensaje
    ctx.kind = "descartado"
    ctx.resultado = "final"
    return {"descartado": True, "motivo": data.reason}


def _escalate_to_human(data: EscalateToHumanInput, ctx: TurnContext) -> dict[str, Any]:
    mensaje = reply_tools.escalate_to_human(data.reason, data.message)
    ctx.respuesta = mensaje
    ctx.kind = "derivada"
    ctx.derivation_reason = data.reason
    ctx.resultado = "final"
    return {"derivada": True, "motivo": data.reason}


TOOLS: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in (
        ToolSpec(
            "explorar_catalogo",
            "Explora el catálogo por marcas o categorías cuando el cliente hace preguntas abiertas "
            "(por ejemplo: qué marcas de shampoo hay, qué productos tiene una marca, o qué líneas se trabajan). "
            "Devuelve resúmenes compactos agrupados por rubro o listas de marcas. Si la marca o la categoría no "
            "existe, `_hint` trae las más parecidas del catálogo.",
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
            "coinciden igual de bien aunque veas menos, y `difieren_en` (marca, medida o variante) qué preguntar. "
            "Cuando no hay una coincidencia segura, `_hint` explica por qué (una palabra que no existe en el "
            "catálogo, una medida que no hay y cuáles sí) y qué hacer: seguilo antes de volver a buscar.",
            SearchProductsInput,
            _search_products,
        ),
        ToolSpec(
            "buscar_renglones",
            "Busca todos los renglones de un pedido en una sola llamada: usala cuando el pedido viene como lista. "
            "Por cada renglón devuelve `estado`, el `total` de coincidencias y hasta 3 `candidatos`. "
            "`unico`: es ese producto y alcanza el stock, va a la propuesta. `varias`: hay más de uno y `difieren_en` "
            "dice en qué (marca, medida o variante), preguntale. `aproximado`: se parece pero no es seguro, "
            "confirmalo. `sin_stock`: `falta` dice cuánto queda. `no_encontrado`: no hay nada parecido. Un renglón "
            "sin coincidencia segura trae `_hint` con el motivo y qué hacer: no lo vuelvas a buscar igual.",
            BuscarRenglonesInput,
            _buscar_renglones,
        ),
        ToolSpec(
            "pedidos_anteriores",
            "Devuelve los últimos pedidos de este cliente, con sus líneas, sin precios. Usala cuando pide «lo de "
            "siempre» o «lo mismo que la otra vez», o para saber qué medida o variante suele llevar.",
            PedidosAnterioresInput,
            _pedidos_anteriores,
        ),
        ToolSpec(
            "get_product",
            "Devuelve un producto por su SKU exacto, o null y un `_hint` si no existe.",
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
            "delegar_skill",
            "Le encarga a una skill que se delega (las marcadas así en el índice) que resuelva unos renglones en su "
            "propio sub-loop, con su contexto. Devuelve su nota: `resueltos` (con SKU y cantidad), `pendientes` (con "
            "la pregunta y sus opciones) y `afuera`, más `observaciones` para vos. El cliente no ve la nota: con ella "
            "le respondés vos.",
            DelegarSkillInput,
            _delegar_skill,
        ),
        ToolSpec(
            "entregar_nota",
            "Termina tu trabajo y le entrega al orquestador lo que resolviste. Cada renglón del encargo va en un solo "
            "lugar: `resueltos`, `pendientes` o `afuera`. Los SKU tienen que haber salido de una búsqueda.",
            EntregarNotaInput,
            _entregar_nota,
        ),
        ToolSpec(
            "armar_pedido",
            "Registra la propuesta vigente y termina el turno. El sistema le responde al cliente con el detalle y el "
            "número. Usala solo si el cliente aceptó la propuesta sin cambios.",
            ArmarPedidoInput,
            _armar_pedido,
        ),
        ToolSpec(
            "discard_order_draft",
            "Descarta el borrador de pedido pendiente y cancela la conversación cuando el cliente explícitamente "
            "dice que ya no lo quiere o que cancela el pedido (RF-26).",
            DiscardOrderDraftInput,
            _discard_order_draft,
        ),
        ToolSpec(
            "escalate_to_human",
            "Deriva la conversación a una persona del equipo cuando el cliente pide hablar con alguien humano, o "
            "cuando solicita condiciones comerciales especiales que no se pueden autorizar por chat (descuentos por "
            "cantidad/bulto, envíos al interior/especiales, formas de pago diferidas, etc.) (RF-27, Issue #84).",
            EscalateToHumanInput,
            _escalate_to_human,
        ),
    )
}

# Disponibles desde el inicio del turno: sin consultar el catálogo no se puede elegir una skill
BASE_TOOLS = (
    "leer_skill", "leer_br", "search_products", "buscar_renglones", "get_product", "check_stock", "explorar_catalogo",
    "pedidos_anteriores",
)


# Las que cierran un loop: hacia el cliente en el orquestador (y en el grafo de hoy), hacia el orquestador en un
# sub-loop. Una skill sub_loop no puede listar las primeras (RF-88, RF-90)
TERMINALES_CLIENTE = (TOOL_RESPUESTA, "armar_pedido")
TERMINAL_SUB_LOOP = "entregar_nota"
SOLO_DEL_BACKEND = ("delegar_skill", TERMINAL_SUB_LOOP)  # las suma el backend según el modo; ninguna skill las lista


def available(skills_activas: list[str], orquestador: bool = False) -> list[str]:
    skills = get_skills()
    names = [*BASE_TOOLS, "delegar_skill"] if orquestador else list(BASE_TOOLS)
    for nombre in skills_activas:
        names.extend(tool for tool in skills[nombre].tools_inline if tool not in names)
    return names


def available_sub_loop(skill: Skill) -> list[str]:
    """Las tools de un sub-loop: las de su skill, `leer_br` y su tool terminal. Nunca las que le hablan al cliente."""
    return list(dict.fromkeys([*skill.tools, "leer_br", TERMINAL_SUB_LOOP]))


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
        if reservadas := [tool for tool in skill.tools if tool in SOLO_DEL_BACKEND]:
            raise SkillError(f"La skill {skill.nombre} nombra tools que suma el backend: {', '.join(reservadas)}")
        if skill.modo == "sub_loop" and (terminales := [t for t in skill.tools if t in TERMINALES_CLIENTE]):
            raise SkillError(
                f"La skill {skill.nombre} es sub_loop y nombra tools que le hablan al cliente: {', '.join(terminales)}"
            )


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
    reglas = reglas_que_dispara(name, result, ctx.skills_activas, ctx.br_entregadas)
    return {**result, "reglas": reglas} if reglas else result


def reglas_que_dispara(
    name: str, result: dict[str, Any], skills_activas: list[str], br_entregadas: list[str]
) -> list[dict[str, Any]]:
    """El texto de las BR nuevas que dispara un resultado. Suma a `br_entregadas` las que devuelve (RF-63)."""
    reglas = []
    for br in _brs_que_dispara(name, result):
        if br in br_entregadas:
            continue
        try:
            reglas.append(skill_tools.leer_br(br, skills_activas))
        except ToolFailure:
            continue
        br_entregadas.append(br)
    return reglas


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
    except FallaSubLoop:
        raise  # no es un error de la tool: el turno falla, como cuando falla el LLM del orquestador
    except OSError:
        logger.exception("Tool %s: no se pudo leer el catálogo", name)
        return _failure("catalogo_no_disponible", _CATALOGO_NO_DISPONIBLE)
    except Exception:
        logger.exception("Tool %s: error inesperado", name)
        return _failure("error_interno", _ERROR_INTERNO)
