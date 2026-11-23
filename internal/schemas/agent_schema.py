"""Contratos del agente: entradas de las tools, errores estructurados y resultado de un turno."""

from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from internal.schemas.order_schema import MAX_LINE_NOTE_LENGTH, MAX_ORDER_NOTES_LENGTH, MAX_QUANTITY, OrderDraft


class ToolFailure(Exception):
    """Error que vuelve al LLM en el sobre común, para que pueda corregirlo (spec del agente §4.3)."""

    def __init__(self, code: str, message: str, **details: Any):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


class ReplyKind(StrEnum):
    PROPUESTA = "propuesta"
    ACLARACION = "aclaracion"
    SIN_STOCK = "sin_stock"
    FUERA_DE_CATALOGO = "fuera_de_catalogo"
    INFORMACION = "informacion"


MAX_RENGLONES_POR_BUSQUEDA = 60
MAX_LARGO_MENSAJE = 600  # un mensaje normal
MAX_LARGO_MENSAJE_CON_DUDAS = 1200  # una lista larga puede traer varias dudas juntas (RF-70)

MotivoAfuera = Literal["fuera_de_catalogo", "sin_stock", "lo_saco_el_cliente", "no_es_producto"]


class ProposedItem(BaseModel):
    sku: str = Field(min_length=1, description="SKU exacto del catálogo")
    quantity: int = Field(gt=0, le=MAX_QUANTITY, description="Cantidad de unidades")
    renglon: int | None = Field(
        default=None,
        ge=1,
        description="Número del renglón del cliente que resuelve esta línea, si el pedido vino numerado",
    )
    note: str | None = Field(
        default=None,
        max_length=MAX_LINE_NOTE_LENGTH,
        description="Nota de esta línea, si el cliente la dio: tono, variante o aclaración. Por ejemplo: tono 7.1",
    )


class SearchProductsInput(BaseModel):
    query: str = Field(
        min_length=1,
        description=(
            "Producto, marca, medida o tono como lo escribió el cliente, sin la cantidad. "
            "Por ejemplo: shampoo pantene 400, sh keratina x900, tintura koleston 7.1"
        ),
    )
    limit: int = Field(default=5, ge=1, le=10, description="Cantidad máxima de resultados")
    quantity: int | None = Field(
        default=None,
        gt=0,
        le=MAX_QUANTITY,
        description="Cantidad que pide el cliente, si la dijo. Con ella, cada resultado indica si alcanza el stock",
    )


class RenglonBuscado(BaseModel):
    n: int = Field(ge=1, description="Número del renglón, tal como figura en el estado actual")
    texto: str = Field(
        min_length=1,
        description=(
            "Producto, medida y variante como los escribió el cliente, sin la cantidad ni el tono. "
            "Por ejemplo: sh keratina x900, mascara argan 1k, coloracion"
        ),
    )
    marca: str | None = Field(default=None, description="Marca del encabezado bajo el que está el renglón, si hay")
    cantidad: int = Field(default=1, gt=0, le=MAX_QUANTITY, description="Unidades que pide el renglón")
    tono: str | None = Field(
        default=None,
        max_length=20,
        description="Solo en coloración: el tono con punto. «821/2» es tono 8.21 y cantidad 2. No se usa para buscar",
    )


class BuscarRenglonesInput(BaseModel):
    renglones: list[RenglonBuscado] = Field(
        min_length=1, max_length=MAX_RENGLONES_POR_BUSQUEDA, description="Todos los renglones a buscar, juntos"
    )


class RenglonPendiente(BaseModel):
    renglon: int = Field(ge=1, description="Número del renglón que le preguntás al cliente")
    opciones: list[str] = Field(default_factory=list, description="SKU de las opciones que le ofrecés para ese renglón")


class RenglonAfuera(BaseModel):
    renglon: int = Field(ge=1, description="Número del renglón que no entra al pedido")
    motivo: MotivoAfuera = Field(
        description=(
            "fuera_de_catalogo: no lo trabajan. sin_stock: está agotado. lo_saco_el_cliente: pidió sacarlo. "
            "no_es_producto: la línea no pide nada, por ejemplo un título o un comentario."
        )
    )


class PedidosAnterioresInput(BaseModel):
    limite: int = Field(default=3, ge=1, le=5, description="Cuántos pedidos anteriores del cliente traer")


class GetProductInput(BaseModel):
    sku: str = Field(min_length=1, description="SKU exacto, tal como lo devolvió una tool")


class CheckStockInput(BaseModel):
    sku: str = Field(min_length=1, description="SKU exacto, tal como lo devolvió una tool")
    quantity: int = Field(gt=0, le=MAX_QUANTITY, description="Cantidad que quiere el cliente")


class ExplorarCatalogoInput(BaseModel):
    brand: str | None = Field(
        default=None,
        description="Marca por la que consulta el cliente (por ejemplo 'Alfaparf', 'Fidelite', 'Loreal'). Opcional.",
    )
    category: str | None = Field(
        default=None,
        description=(
            "Rubro o categoría a consultar (por ejemplo 'Shampoo', 'Acondicionador', 'Coloración', 'Oxidante'). "
            "Opcional."
        ),
    )
    limit: int = Field(default=5, ge=1, le=10, description="Tope de productos a listar")


class LeerSkillInput(BaseModel):
    nombre: str = Field(min_length=1, description="Nombre de la skill, tal como figura en el índice")


class LeerBrInput(BaseModel):
    id: str = Field(min_length=1, description="ID de la regla, por ejemplo BR-01")


class RespondToCustomerInput(BaseModel):
    kind: ReplyKind = Field(
        description=(
            "propuesta: el pedido está claro y alcanza el stock. "
            "aclaracion: falta producto, presentación, tono o cantidad. "
            "sin_stock: no alcanza el stock. "
            "fuera_de_catalogo: el producto no está en el catálogo. "
            "informacion: saludo, consulta o cualquier otra respuesta."
        )
    )
    message: str = Field(
        min_length=1,
        max_length=MAX_LARGO_MENSAJE_CON_DUDAS,
        description=f"Texto para el cliente, sin SKU. Hasta {MAX_LARGO_MENSAJE} caracteres; el doble con `pendientes`",
    )
    options: list[str] = Field(
        default_factory=list, description="SKU de las opciones o alternativas que le ofrecés en el mensaje"
    )
    propuesta: list[ProposedItem] | None = Field(
        default=None,
        description=(
            "Pedido completo que le proponés, solo con kind propuesta o sin_stock. Reemplaza a la propuesta vigente: "
            "incluí todas las líneas. Si la omitís, la vigente sigue igual."
        ),
    )
    descartar_propuesta: bool = Field(
        default=False,
        description=(
            "true solo si el cliente rechaza la propuesta vigente o saca todo: deja de valer. Va sin `propuesta`. "
            "Una pregunta o una consulta no la descarta"
        ),
    )
    notes: str | None = Field(
        default=None,
        max_length=MAX_ORDER_NOTES_LENGTH,
        description=(
            "Observaciones completas del cliente, solo con `propuesta`, incluidas solicitudes de retiro y pago. "
            "No implican aceptación del negocio. Omitir conserva las notas vigentes; null o texto vacío las borra. "
            "Al actualizar, incluir también las notas anteriores que sigan vigentes."
        ),
    )

    pendientes: list[RenglonPendiente] = Field(
        default_factory=list,
        description=(
            "Con un pedido numerado: los renglones que le preguntás al cliente en este mensaje. El cliente no ve este "
            "campo: la pregunta de cada renglón, con sus opciones, va escrita en `message`, una por renglón con una "
            "viñeta (•)"
        ),
    )
    afuera: list[RenglonAfuera] = Field(
        default_factory=list,
        description=(
            "Con un pedido numerado: los renglones que no entran al pedido, con el motivo. El cliente no ve este "
            "campo: lo que no trabajan se lo decís en `message`"
        ),
    )

    @model_validator(mode="after")
    def check_largo(self) -> Self:
        if len(self.message) > MAX_LARGO_MENSAJE and not self.pendientes:
            raise ValueError(f"El mensaje tiene que entrar en {MAX_LARGO_MENSAJE} caracteres: acortalo")
        return self

    @model_validator(mode="after")
    def check_propuesta(self) -> Self:
        if self.propuesta is not None and not self.propuesta:
            raise ValueError("`propuesta` no puede estar vacía: omitila si no estás proponiendo nada")
        if self.propuesta and self.kind not in (ReplyKind.PROPUESTA, ReplyKind.SIN_STOCK):
            raise ValueError("`propuesta` solo se usa con kind propuesta o sin_stock")
        if self.kind == ReplyKind.PROPUESTA and not self.propuesta:
            raise ValueError("Con kind propuesta hay que indicar `propuesta`")
        if self.descartar_propuesta and self.propuesta:
            raise ValueError(
                "`descartar_propuesta` va sin `propuesta`: una propuesta nueva ya reemplaza a la vigente"
            )
        if self.notes and not self.propuesta:
            raise ValueError("`notes` es la observación de una propuesta: omitila si no estás proponiendo nada")
        return self


class ArmarPedidoInput(BaseModel):
    """No recibe nada del LLM: la tool registra la propuesta vigente."""

    model_config = ConfigDict(extra="ignore")


MAX_LARGO_ENCARGO = 300
MAX_LARGO_OBSERVACIONES = 300


class DelegarSkillInput(BaseModel):
    """Lo que el orquestador le encarga a una skill que corre en su propio sub-loop (spec del orquestador §4.2)."""

    skill: str = Field(min_length=1, description="Una skill del índice que se delega (modo sub_loop)")
    encargo: str = Field(
        min_length=1,
        max_length=MAX_LARGO_ENCARGO,
        description="Qué tiene que resolver, en una o dos frases. Por ejemplo: resolvé tamaño y cantidad de estos",
    )
    renglones: list[int] = Field(
        default_factory=list,
        description="Números de los renglones del estado actual que tiene que resolver. Vacía si no hay renglones",
    )


_RENGLON_DE_LA_NOTA = "Número del renglón del encargo. Sin renglones en el encargo, omitilo"


class NotaResuelto(BaseModel):
    renglon: int | None = Field(default=None, ge=1, description=_RENGLON_DE_LA_NOTA)
    sku: str = Field(min_length=1, description="SKU exacto, tal como salió de una búsqueda")
    cantidad: int = Field(gt=0, le=MAX_QUANTITY, description="Unidades")
    tono: str | None = Field(default=None, max_length=20, description="Solo en coloración: el tono con punto")


class NotaPendiente(BaseModel):
    renglon: int | None = Field(default=None, ge=1, description=_RENGLON_DE_LA_NOTA)
    pregunta: str = Field(min_length=1, max_length=200, description="Qué hay que preguntarle, en pocas palabras")
    opciones: list[str] = Field(
        default_factory=list, description="SKU de las opciones, tal como salieron de una búsqueda"
    )


class NotaAfuera(BaseModel):
    renglon: int | None = Field(default=None, ge=1, description=_RENGLON_DE_LA_NOTA)
    motivo: MotivoAfuera = Field(
        description=(
            "fuera_de_catalogo: no lo trabajan. sin_stock: está agotado. lo_saco_el_cliente: pidió sacarlo. "
            "no_es_producto: la línea no pide nada."
        )
    )


class EntregarNotaInput(BaseModel):
    """Cierra un sub-loop: lo que resolvió y lo que falta, para el orquestador (spec del orquestador §4.3)."""

    resueltos: list[NotaResuelto] = Field(
        default_factory=list,
        description=(
            "Los renglones identificados y con stock, cada uno con su SKU y cantidad. Si no alcanza el stock, con la "
            "cantidad que queda"
        ),
    )
    pendientes: list[NotaPendiente] = Field(
        default_factory=list, description="Los renglones que hay que preguntarle al cliente, con sus opciones"
    )
    afuera: list[NotaAfuera] = Field(default_factory=list, description="Los renglones que no entran al pedido")
    observaciones: str = Field(
        default="",
        max_length=MAX_LARGO_OBSERVACIONES,
        description="Lo que el orquestador tiene que saber para responder, en una o dos frases. El cliente no lo ve",
    )


class DiscardOrderDraftInput(BaseModel):
    """Descarta el borrador de pedido pendiente cuando el cliente lo pide de forma explícita (RF-26)."""

    reason: str = Field(description="Motivo del descarte, por ejemplo: el cliente canceló el pedido")
    message: str | None = Field(default=None, description="Mensaje opcional para el cliente confirmando el descarte")


# Motivos que puede emitir el LLM en escalate_to_human
LlmDerivationReason = Literal["solicitud_humano", "condicion_comercial", "reclamo", "otro"]
# Conjunto completo de motivos de derivación para agrupación en dashboard (incluye los del backend)
DerivationReason = Literal[
    "solicitud_humano",
    "condicion_comercial",
    "reclamo",
    "otro",
    "fallas_repetidas",
    "aclaraciones_repetidas",
]


class EscalateToHumanInput(BaseModel):
    """Deriva la conversación a una persona del equipo (RF-27, Issue #84)."""

    reason: LlmDerivationReason = Field(
        description="Motivo de la derivación: solicitud_humano, condicion_comercial, reclamo u otro"
    )
    message: str | None = Field(
        default=None,
        description="Mensaje opcional para el cliente explicando que se deriva al equipo humano",
    )


class AgentOutcome(BaseModel):
    """Lo que devuelve NormaAgent al orquestador (spec del agente §9.2)."""

    kind: str
    message: str
    draft: OrderDraft | None = None
    order_id: str | None = None
    options: list[str] = Field(default_factory=list)
    iterations: int = 0  # llamadas al LLM del loop principal (el de hoy o el orquestador)
    llamadas_sub_loop: int = 0  # llamadas al LLM de los sub-loops del turno (RF-94)
    cerrada: bool = False
    derivation_reason: str | None = None
