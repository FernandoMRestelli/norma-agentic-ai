"""Palabras que el backend reconoce sin llamar al LLM: aceptaciones exactas de la propuesta y saludos.

Lógica pura (texto -> ConfirmationAction | None).
No decide sobre el estado de la conversación: de eso se encarga el grafo del agente,
que solo registra la propuesta si hay una vigente.
"""

import re
import unicodedata

from internal.schemas.order_schema import ConfirmationAction

# Aceptación pura: el mensaje completo es una de estas, sin nada más (RF-13, RF-64, D-23). La BR-06 las cita
ACEPTACIONES = frozenset({"ok", "confirmar", "dale", "si", "listo", "de una", "mandale"})

# Palabras de un saludo o un agradecimiento: un mensaje hecho solo con estas no pide ni consulta nada (RF-62)
_SALUDO = frozenset(
    "hola holaa holis buenas buen buenos dia dias tarde tardes noche noches gracias muchas mil que tal como va "
    "andas estas todo bien genial".split()
)
MAX_PALABRAS_SALUDO = 5


def _normalizar(texto: str) -> str:
    """Sin tildes ni mayúsculas, con un solo espacio entre palabras y sin los signos de un mensaje enfático."""
    nfd = unicodedata.normalize("NFD", texto.strip().lower())
    limpio = " ".join("".join(c for c in nfd if unicodedata.category(c) != "Mn").split())
    # «¡dale!» y «listo.» siguen siendo una aceptación; «ok?» y «sí...» no, porque dudan
    return re.sub(r"^[¡!\s]+|[!\s]+$", "", re.sub(r"(?<!\.)\.$", "", limpio))


def interpretar_palabra_clave(texto: str) -> ConfirmationAction | None:
    """Interpreta si el texto recibido coincide exactamente con una palabra clave.

    Reglas (agente §2.1, RF-13, RF-64, CA-18, CA-120):
    - Se compara sin mayúsculas, tildes ni espacios sobrantes.
    - Coincidencia exacta del mensaje completo:
      * una de `ACEPTACIONES` («ok», «confirmar», «dale», «sí», «listo», «de una», «mandale») -> CONFIRM
      * «corregir» -> CORRECT
    - Cualquier otro texto, incluidas las frases compuestas («dale, pero 2», «ok gracias»), devuelve None
      y el mensaje pasa al agente, que decide si es una aceptación (BR-06).

    Nota: Esta función es pura y no comprueba el estado de la conversación.
    Hoy solo CONFIRM evita al LLM. «corregir» sigue al agente, que pregunta qué cambiar.
    """
    normalizado = _normalizar(texto)
    if normalizado in ACEPTACIONES:
        return ConfirmationAction.CONFIRM
    if normalizado == "corregir":
        return ConfirmationAction.CORRECT
    return None


def es_saludo(texto: str) -> bool:
    """True si el mensaje es solo un saludo o un agradecimiento: no nombra productos ni cantidades."""
    palabras = re.findall(r"[a-z]+", _normalizar(texto))
    return 0 < len(palabras) <= MAX_PALABRAS_SALUDO and all(palabra in _SALUDO for palabra in palabras)
