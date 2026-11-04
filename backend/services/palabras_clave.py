"""Interpretación de palabras clave para confirmación y corrección de borradores.

Lógica pura (texto -> ConfirmationAction | None).
No decide sobre el estado de la conversación (eso le corresponde al orquestador
en `conversation_service`, quien verificará que la conversación esté en `borrador_pendiente`).
"""

import unicodedata

from backend.schemas.order_schema import ConfirmationAction

AYUDA_CONFIRMAR: str = "Respondé OK para confirmar o CORREGIR para cambiar algo"


def _normalizar(texto: str) -> str:
    """Elimina tildes (NFD), pasa a minúsculas y recorta espacios en los extremos."""
    nfd = unicodedata.normalize("NFD", texto.strip().lower())
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn")


def interpretar_palabra_clave(texto: str) -> ConfirmationAction | None:
    """Interpreta si el texto recibido coincide exactamente con una palabra clave de confirmación.

    Reglas (agente §2.1, RF-13, CA-18):
    - Se compara sin mayúsculas, tildes ni espacios sobrantes.
    - Coincidencia exacta:
      * 'ok' o 'confirmar' -> ConfirmationAction.CONFIRM
      * 'corregir' -> ConfirmationAction.CORRECT
    - Cualquier otro texto (incluyendo 'dale', 'sí', o frases compuestas) devuelve None,
      dejando que el mensaje pase al agente (quien no puede confirmar y recordará cómo hacerlo).

    Nota: Esta función es pura y no comprueba el estado de la conversación.
    El orquestador debe asegurar que solo se aplique en `borrador_pendiente`.
    """
    normalizado = _normalizar(texto)
    if normalizado in ("ok", "confirmar"):
        return ConfirmationAction.CONFIRM
    if normalizado == "corregir":
        return ConfirmationAction.CORRECT
    return None
