"""Contrato de la lectura de una imagen: lo que devuelve cualquier modelo de visión, en JSON.

El modelo solo transcribe y ordena la hoja; no busca SKU ni precios. Eso lo sigue haciendo el agente contra el
catálogo (el LLM interpreta, el backend valida). Además de los renglones, la lectura separa lo que no se pide: el
encabezado de la hoja y lo tachado (spec de lectura de imágenes §4.1). Los campos nuevos tienen valor por defecto:
el modelo local y una lectura vieja siguen validando.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

TipoImagen = Literal["lista_pedido", "captura_chat", "foto_producto", "ticket", "otro"]
Unidad = Literal["unidades", "cajas", "cada_uno"]

ETIQUETA_TIPO = {
    "lista_pedido": "lista de pedido",
    "captura_chat": "captura de chat",
    "foto_producto": "foto de producto",
    "ticket": "ticket",
    "otro": "otra imagen",
}


class Encabezado(BaseModel):
    """Lo que identifica la hoja y no se pide."""

    cliente: str | None = Field(default=None, description="Nombre del cliente o del negocio, si está escrito")
    fecha: str | None = Field(default=None, description="La fecha tal cual está escrita")
    otros: list[str] = Field(default_factory=list, description="Otros datos de la hoja: «hoja 1», un teléfono")

    def partes(self) -> list[str]:
        return [parte for parte in (self.cliente, self.fecha, *self.otros) if parte and parte.strip()]


class Variante(BaseModel):
    """Un color, modelo o tono de un renglón que pide varios en la misma línea."""

    detalle: str = Field(description="El color o el modelo: «negro», «Maya». Vacío si solo es un tono")
    tono: str | None = Field(default=None, description="Solo en coloración: el tono con punto, «7.1»")
    cantidad: float | None = Field(default=None, description="Cantidad de esta variante. null si no está escrita")
    duda: str | None = Field(default=None, description="Qué no se lee seguro de esta variante, con la otra lectura")


class RenglonLeido(BaseModel):
    """Un renglón del pedido tal como aparece en la imagen."""

    texto: str = Field(description="El renglón completo, copiado tal cual de la imagen")
    cantidad: float | None = Field(
        default=None, description="Cantidad pedida como número (media docena = 6). null si no se ve o si hay variantes"
    )
    producto: str | None = Field(
        default=None, description="Qué producto es, sin la cantidad, la marca ni las variantes. null si no se entiende"
    )
    marca: str | None = Field(default=None, description="Marca escrita en el renglón o en su encabezado de marca")
    presentacion: str | None = Field(default=None, description="Tamaño o medida si aparece: 400 ml, x90, 1L")
    cantidad_escrita: str | None = Field(default=None, description="La cantidad como está escrita: «x24», «(3)»")
    unidad: Unidad | None = Field(default=None, description="Solo si está escrita: unidades, cajas o cada_uno (c/u)")
    variantes: list[Variante] = Field(
        default_factory=list, description="Colores, modelos o tonos de la misma línea, cada uno con su cantidad"
    )
    sigue_a: int | None = Field(
        default=None,
        description="Si la línea continúa la de arriba, el número (desde 1) del renglón de esta lista al que sigue",
    )
    duda: str | None = Field(default=None, description="Qué no se lee seguro de este renglón, con la otra lectura")


class ImagenLeida(BaseModel):
    """Salida de la lectura de una imagen. Es la misma para la API de frontera y para el modelo local."""

    legible: bool = Field(description="false si la imagen no tiene texto o no se puede leer")
    tipo: TipoImagen = Field(description="Qué es la imagen")
    texto: str = Field(description="Todo el texto visible, en orden de lectura, con saltos de línea")
    encabezado: Encabezado | None = Field(
        default=None, description="Cliente, fecha y número de hoja: lo que identifica la hoja y no se pide"
    )
    renglones: list[RenglonLeido] = Field(
        default_factory=list,
        description="Los productos pedidos, uno por renglón, sin el encabezado ni lo tachado. Vacío si no es un pedido",
    )
    tachados: list[str] = Field(
        default_factory=list, description="Cada renglón o bloque tachado, con el texto que se alcanza a leer"
    )
    dudas: list[str] = Field(
        default_factory=list, description="Lo que no se lee bien y no es de un renglón, en pocas palabras"
    )

    @model_validator(mode="after")
    def _sin_texto_no_es_legible(self) -> "ImagenLeida":
        # Los modelos chicos a veces dicen legible con el texto vacío: sin texto no hay nada que leer
        if not self.texto.strip():
            self.legible = False
        return self

    def como_texto_para_el_agente(self) -> str:
        """Lo que entra al agente (spec de lectura de imágenes §4.3).

        Con renglones, la lectura ordenada: el encabezado y lo tachado aparte, marcados como «no es pedido», y cada
        renglón con sus variantes y su duda. Sin renglones, el texto leído como hoy (RF-110).
        """
        if not self.renglones:
            return f"[imagen leída]\n{self.texto.strip()}"
        lineas = [f"[imagen leída: {ETIQUETA_TIPO[self.tipo]}]"]
        if self.encabezado and (partes := self.encabezado.partes()):
            lineas.append(f"No es pedido (encabezado): {' · '.join(partes)}")
        if tachados := [tachado.strip() for tachado in self.tachados if tachado.strip()]:
            lineas.append(f"No es pedido (tachado en la hoja): {'; '.join(tachados)}")
        lineas.append("Renglones:")
        lineas += [f"- {_describir(renglon)}" for renglon in self.renglones]
        if dudas := [duda.strip() for duda in self.dudas if duda.strip()]:
            lineas.append(f"Dudas de lectura: {'; '.join(dudas)}")
        return "\n".join(lineas)


def _describir(renglon: RenglonLeido) -> str:
    linea = renglon.texto.strip()
    if renglon.duda:
        linea += f" (duda de lectura: {renglon.duda.strip()})"
    dudas_variantes = [f"{v.detalle or v.tono}: {v.duda.strip()}" for v in renglon.variantes if v.duda]
    if dudas_variantes:
        linea += f" (duda de lectura: {'; '.join(dudas_variantes)})"
    return linea


def _numero(valor: float | None) -> int | None:
    """Una cantidad entera y positiva, o None: «1.5 cajas» o «0» no se pueden buscar tal cual."""
    if valor is None or valor <= 0 or valor != int(valor):
        return None
    return int(valor)


def _producto(renglon: RenglonLeido) -> str:
    return " ".join(filter(None, (renglon.producto, renglon.presentacion))).strip()


def renglones_desde_lectura(lectura: ImagenLeida, desde: int = 1) -> list[dict[str, Any]]:
    """Los renglones del pedido de un mensaje con imagen, numerados desde `desde` (RF-102, RF-103).

    Cada renglón queda como los que arma `numerar_renglones` (`n`, `texto`, `marca`, `estado`) y suma lo que ya
    separó la lectura para la precarga (`buscar`, `cantidad`, `tono`) y la `duda` de lectura. Un renglón con
    variantes da uno por variante; uno con `sigue_a` hereda producto y marca. El encabezado y lo tachado no dan
    renglones.
    """
    leidos = lectura.renglones
    resultado: list[dict[str, Any]] = []
    for indice, renglon in enumerate(leidos):
        producto, marca = _producto(renglon), renglon.marca
        if renglon.sigue_a is not None and 1 <= renglon.sigue_a <= len(leidos) and renglon.sigue_a != indice + 1:
            anterior = leidos[renglon.sigue_a - 1]
            producto = " ".join(filter(None, (_producto(anterior), producto))).strip()
            marca = marca or anterior.marca
        if renglon.variantes:
            for variante in renglon.variantes:
                detalle = " ".join(filter(None, (variante.detalle.strip(), variante.tono))).strip()
                texto = " ".join(filter(None, (producto or renglon.texto.strip(), detalle)))
                if variante.cantidad is not None:
                    texto += f" x{variante.cantidad:g}"
                resultado.append(
                    _renglon(desde + len(resultado), texto, marca, producto, variante.cantidad, variante.tono,
                             variante.duda or renglon.duda, renglon)
                )  # fmt: skip
            continue
        resultado.append(
            _renglon(desde + len(resultado), renglon.texto.strip(), marca, producto, renglon.cantidad, None,
                     renglon.duda, renglon)
        )  # fmt: skip
    return resultado


def _renglon(
    n: int,
    texto: str,
    marca: str | None,
    producto: str,
    cantidad: float | None,
    tono: str | None,
    duda: str | None,
    leido: RenglonLeido,
) -> dict[str, Any]:
    renglon: dict[str, Any] = {"n": n, "texto": texto, "marca": marca, "estado": "sin_resolver", "origen": "imagen"}
    if producto and (numero := _numero(cantidad)) is not None:
        renglon["buscar"] = {"texto": producto, "cantidad": numero, "tono": tono}
    if leido.cantidad_escrita:
        renglon["cantidad_escrita"] = leido.cantidad_escrita
    if leido.unidad:
        renglon["unidad"] = leido.unidad
    if duda and duda.strip():
        renglon["duda"] = duda.strip()
    return renglon
