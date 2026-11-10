"""Interpretación de palabras clave para confirmación y corrección de propuestas.

Lógica pura (texto -> ConfirmationAction | None).
No decide sobre el estado de la conversación: de eso se encarga el grafo del agente,
que solo registra la propuesta si hay una vigente.
"""

import unicodedata

from internal.schemas.order_schema import ConfirmationAction


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
      dejando que el mensaje pase al agente, que decide si es una aceptación.

    Nota: Esta función es pura y no comprueba el estado de la conversación.
    Hoy solo CONFIRM evita al LLM. 'corregir' sigue al agente, que pregunta qué cambiar.
    """
    normalizado = _normalizar(texto)
    if normalizado in ("ok", "confirmar"):
        return ConfirmationAction.CONFIRM
    if normalizado == "corregir":
        return ConfirmationAction.CORRECT
    return None
