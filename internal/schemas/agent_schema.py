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
            "incluí todas las líneas. Si la omitís, la vigente deja de valer."
        ),
    )
    notes: str | None = Field(
        default=None,
        max_length=MAX_ORDER_NOTES_LENGTH,
        description=(
            "Observación general del pedido que dio el cliente, solo con `propuesta`. Por ejemplo: retira el sábado"
        ),
    )

    pendientes: list[RenglonPendiente] = Field(
        default_factory=list,
        description="Con un pedido numerado: los renglones que le preguntás al cliente en este mensaje",
    )
    afuera: list[RenglonAfuera] = Field(
        default_factory=list, description="Con un pedido numerado: los renglones que no entran al pedido, con el motivo"
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
        if self.notes and not self.propuesta:
            raise ValueError("`notes` es la observación de una propuesta: omitila si no estás proponiendo nada")
        return self


class ArmarPedidoInput(BaseModel):
    """No recibe nada del LLM: la tool registra la propuesta vigente."""

    model_config = ConfigDict(extra="ignore")


class AgentOutcome(BaseModel):
    """Lo que devuelve NormaAgent al orquestador (spec del agente §9.2)."""

    kind: str
    message: str
    draft: OrderDraft | None = None
    order_id: str | None = None
    options: list[str] = Field(default_factory=list)
    iterations: int = 0
    cerrada: bool = False
