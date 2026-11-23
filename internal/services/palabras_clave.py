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

# Cancelación pura: el mensaje completo pide descartar el pedido (RF-26, CA-23)
CANCELACIONES = frozenset(
    {
        "cancelar",
        "cancela",
        "cancelalo",
        "deja sin efecto",
        "anular",
        "anula",
        "anulalo",
        "cancelar pedido",
    }
)

# Palabras de un saludo o un agradecimiento: un mensaje hecho solo con estas no pide ni consulta nada (RF-62)
_SALUDO = frozenset(
    "hola holaa holis buenas buen buenos dia dias tarde tardes noche noches gracias muchas mil que tal como va "
    "andas estas todo bien genial".split()
)
MAX_PALABRAS_SALUDO = 5
# Palabras con las que abre un saludo: «buenas! para el salón necesito» empieza así, «todo nordika» no
_APERTURA_SALUDO = frozenset("hola holaa holis buenas buen buenos".split())


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
    if normalizado in CANCELACIONES:
        return ConfirmationAction.CANCEL
    if normalizado == "corregir":
        return ConfirmationAction.CORRECT
    return None


def es_saludo(texto: str) -> bool:
    """True si el mensaje es solo un saludo o un agradecimiento: no nombra productos ni cantidades."""
    palabras = re.findall(r"[a-z]+", _normalizar(texto))
    return 0 < len(palabras) <= MAX_PALABRAS_SALUDO and all(palabra in _SALUDO for palabra in palabras)


def empieza_con_saludo(texto: str) -> bool:
    """True si el texto abre con un saludo («Buenas! Para el salón necesito»), aunque después siga."""
    palabras = re.findall(r"[a-z]+", _normalizar(texto))
    return bool(palabras) and palabras[0] in _APERTURA_SALUDO


def es_pedido_humano(texto: str) -> bool:
    """True si el cliente pide explícitamente hablar con una persona, operador o asesor (RF-27)."""
    normalizado = _normalizar(texto)
    return bool(
        re.search(
            r"\b(?:hablar|comunicar|comunicame|pasar|pasame|atender|atendeme)\s+con\s+"
            r"(?:(?:un[ao]?|el|la)\s+)?(?:humano|persona|alguien|operador|asesor|representante|vendedor)\b|"
            r"\batencion humana\b|"
            r"\bderiva(?:me|r)?\s+(?:a|con)\s+(?:(?:un[ao]?|el|la)\s+)?"
            r"(?:humano|persona|operador|asesor|vendedor)\b",
            normalizado,
        )
    )


def es_cancelacion(texto: str) -> bool:
    """True si el mensaje completo pide cancelar o descartar la propuesta o el pedido (RF-26).

    Exige coincidencia del mensaje completo para no forzar `derivar_o_cancelar` ante ediciones parciales
    como «cancelá los vasos y sumá 2 platos» o «no, cancelá el rojo».
    """
    normalizado = _normalizar(texto)
    if normalizado in CANCELACIONES:
        return True
    return bool(
        re.fullmatch(
            r"(?:quiero\s+)?(?:cancelar|cancela|cancelame|cancelalo|anular|anula|anulalo|descartar|descarta)"
            r"(?:\s+(?:todo|el\s+pedido|la\s+propuesta))?|"
            r"deja(?:lo)?\s+sin\s+efecto",
            normalizado,
        )
    )


def es_solicitud_comercial(texto: str) -> bool:
    """True si pide explícitamente condiciones comerciales especiales (Issue #84).

    Evita palabras sueltas como «descuento» o «cuotas» y preguntas generales como
    «¿hacen envío al interior?» o «¿hacen descuento si llevo 2 cajas?» para no forzar
    `derivar_o_cancelar` en consultas que `informar` puede responder con datos del negocio.
    Exige verbos o expresiones explícitas de pedido/negociación.
    """
    if "?" in texto or "¿" in texto:
        return False
    normalizado = _normalizar(texto)
    if re.search(
        r"\b(?:hacen|tienen|hay|haces|tenes|se puede)\s+(?:envio|envios|flete|descuento|precio|cuenta)\b",
        normalizado,
    ):
        return False

    return bool(
        re.search(
            r"\bdescuento(?:s)?\s+(?:por\s+(?:volumen|bulto|cantidad|mayor))\b|"
            r"\b(?:hacer(?:me)?|haceme|pedir|solicitar|dame)\s+(?:un\s+)?(?:\d+\s*%?\s*(?:de\s+)?)?descuento\b|"
            r"\b(?:hacer(?:me)?|haceme|pedir|solicitar|quiero|necesito|dame)\s+(?:un\s+)?precio especial\b|"
            r"\b(?:abrir|pagar\s+(?:con|en)|dejar\s+en|usar|quiero|necesito|anotar\s+en)\s+cuenta corriente\b|"
            r"\b(?:pagar\s+con|hacer|pedir|solicitar|quiero|necesito)\s+pago diferido\b|"
            r"\b(?:quiero|necesito|mandar|mandame|mandalo|hacer(?:me)?|haceme|pedir|solicitar)\s+(?:un\s+)?"
            r"(?:envio\s+al\s+interior|flete\s+especial|transporte\s+al\s+interior)\b|"
            r"\b(?:mandar?(?:me)?(?:lo)?|enviar?(?:me)?(?:lo)?)\s+al\s+interior\b",
            normalizado,
        )
    )


_CONSULTA_ESTADO_PEDIDO = re.compile(
    r"\b(?:como\s+(?:viene|va|esta|sigue)|estado\s+(?:de\s+(?:mi|el|la)|del)|"
    r"ya\s+(?:esta\s+list[oa]|salio|despacharon|prepararon)|esta\s+list[oa]|"
    r"donde\s+esta|por\s+donde\s+(?:va|viene)|que\s+paso\s+con|puedo\s+retirar|"
    r"cuando\s+(?:llega|sale|retiro|puedo\s+retirar))\s+"
    r"(?:(?:mi|el|la|ese|esa|tu)\s+)?(?:pedido|orden|compra|ord-\d+|lo\s+mio)\b|"
    r"\b(?:mi|el)\s+(?:pedido|orden)\s+(?:como\s+(?:viene|va|esta)|ya\s+(?:esta|salio)|esta\s+list[oa]|"
    r"donde\s+esta)\b|"
    r"\b(?:consulta|consultar|averiguar|saber)\s+(?:el\s+)?(?:estado|por\s+el\s+estado)\b|"
    r"\b(?:seguimiento|estado)\s+(?:de\s+)?(?:mi\s+|el\s+)?ord-\d+\b"
)


def es_consulta_estado_pedido(texto: str) -> bool:
    """True si el cliente consulta por el estado de un pedido propio ya registrado (Issue #26).

    Exige giros o verbos explícitos pegados al pedido o número de orden (ej. «¿cómo viene mi pedido?»,
    «¿cómo está el ORD-0005?»). No preactiva si el mensaje contiene números fuera de `ORD-XXXX` (cantidades).
    """
    if len(texto.splitlines()) > 1:
        return False
    normalizado = _normalizar(texto)
    # Si hay dígitos fuera de ORD-XXXX (cantidades, medidas), puede ser un pedido nuevo: que decida tomar_pedido/LLM
    if any(caracter.isdigit() for caracter in re.sub(r"\bord-\d+\b", "", normalizado)):
        return False
    return bool(_CONSULTA_ESTADO_PEDIDO.search(normalizado))
