"""Filtro conservador de desvíos evidentes: sin LLM, catálogo ni estado de clientes."""

import re
import unicodedata

OFF_TOPIC_REPLY = "Por acá te ayudo con productos y pedidos de Norma. ¿Qué necesitás?"

# Son intenciones concretas, no palabras prohibidas: «Python» o «capital» solos no bloquean.
_OFF_TOPIC = re.compile(
    r"\b(?:"
    r"(?:contame|cuentame|conta|cuenta|decime|dime|inventame|inventa|haceme|hazme) "
    r"(?:un |una )?(?:chiste|cuento|poema|horoscopo)"
    r"|(?:cual es|decime|dime) (?:la |el )?capital de"
    r"|(?:escribime|escribe|escribi|haceme|hazme|genera|generame) "
    r"(?:un |una )?(?:codigo|programa|script)"
    r"|(?:como salio|cuando juega|quien gano) (?:boca|river|el partido)"
    r"|(?:quien gana|quien gano|quien ganara) (?:las elecciones|la eleccion)"
    r"|(?:que tomo|que medicamento) para"
    r"|(?:mostrame|muestrame|revela|revelame) (?:tu |tus |el |las )?"
    r"(?:prompt|instrucciones|claves|contrasenas)"
    r"|(?:ignora|ignora todas|olvida|olvida todas) (?:tus |las )?instrucciones"
    r"|(?:actua|comportate) como (?:un |una )?(?:abogado|programador|medico)"
    r")\b"
)

# Ante señales de negocio, conservamos el mensaje completo y su contexto para el agente.
# No se consulta el catálogo: marcas desconocidas y respuestas cortas pasan por defecto.
_BUSINESS = re.compile(
    r"\b(?:pedido|pedidos|producto|productos|catalogo|precio|precios|stock|comprar|compra|"
    r"agregame|agrega|sumame|suma|saca|quita|cambia|cambiame|confirmo|confirmar|"
    r"shampoo|champu|sh|acondicionador|acond|oxidante|oxi|tintura|coloracion|"
    r"decolorante|mascara|keratina|alisado|gel|crema|perfume|esmalte|"
    r"horario|horarios|pago|pagos|envio|envios|entrega|retiro|reclamo|negocio)s?\b"
)


def is_off_topic(text: str) -> bool:
    """Solo bloquea intenciones conocidas sin señales de negocio; ante la duda deja pasar."""
    normalized = "".join(
        char for char in unicodedata.normalize("NFD", text.casefold()) if unicodedata.category(char) != "Mn"
    )
    normalized = " ".join(normalized.split())
    return bool(_OFF_TOPIC.search(normalized) and not _BUSINESS.search(normalized))
