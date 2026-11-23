"""Contrato de la lectura de una imagen: lo que devuelve cualquier modelo de visión, en JSON.

El modelo solo transcribe y separa renglones; no busca SKU ni precios. Eso lo sigue haciendo el agente contra el
catálogo (el LLM interpreta, el backend valida).
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

TipoImagen = Literal["lista_pedido", "captura_chat", "foto_producto", "ticket", "otro"]


class RenglonLeido(BaseModel):
    """Un renglón del pedido tal como aparece en la imagen."""

    texto: str = Field(description="El renglón completo, copiado tal cual de la imagen")
    cantidad: float | None = Field(
        default=None, description="Cantidad pedida como número (media docena = 6). null si no se ve"
    )
    producto: str | None = Field(default=None, description="Qué producto es, sin la cantidad. null si no se entiende")
    marca: str | None = Field(default=None, description="Marca si aparece, si no null")
    presentacion: str | None = Field(default=None, description="Tamaño o variante si aparece: 400 ml, 90 g, tono 6.1")


class ImagenLeida(BaseModel):
    """Salida de la lectura de una imagen. Es la misma para la API de frontera y para el modelo local."""

    legible: bool = Field(description="false si la imagen no tiene texto o no se puede leer")
    tipo: TipoImagen = Field(description="Qué es la imagen")
    texto: str = Field(description="Todo el texto visible, en orden de lectura, con saltos de línea")
    renglones: list[RenglonLeido] = Field(
        default_factory=list, description="Los productos pedidos, uno por renglón. Vacío si no es un pedido"
    )
    dudas: list[str] = Field(
        default_factory=list, description="Partes que no se pudieron leer o que son ambiguas, en pocas palabras"
    )

    @model_validator(mode="after")
    def _sin_texto_no_es_legible(self) -> "ImagenLeida":
        # Los modelos chicos a veces dicen legible con el texto vacío: sin texto no hay nada que leer
        if not self.texto.strip():
            self.legible = False
        return self

    def como_texto_para_el_agente(self) -> str:
        """Lo que entra al agente: el texto leído, marcado con su origen (spec §F3)."""
        return f"[imagen leída]\n{self.texto.strip()}"
