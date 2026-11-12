"""Búsqueda tolerante a errores sobre el catálogo (RF-07, RF-36 a RF-39, spec `docs/spec/busqueda-tolerante.md`).

La búsqueda y el catálogo pasan por la misma normalización: abreviaturas y sinónimos del rubro, plurales, unidades
y tonos. Las palabras se comparan con tolerancia a errores de tipeo y de sonido; los números nunca se aproximan.

El índice guarda solo el texto normalizado del catálogo. El stock no se guarda: sale de los productos que recibe
cada búsqueda, que `inventory_service` calcula en el momento.
"""

import re
import threading
import unicodedata
from dataclasses import dataclass
from typing import Literal

from internal.schemas.catalog_schema import Product, ProductMatch, SearchResult

MIN_SCORE = 0.6  # puntaje mínimo de un match confiable
MARGEN = 0.1  # los resultados a más de esto del primero no aparecen: si está el de 400 ml, el de 200 ml sobra
MIN_PALABRA = 0.8  # parecido mínimo para que una palabra con errores cuente en un match
MIN_PALABRA_SUGERENCIA = 0.7  # parecido mínimo para que cuente en una sugerencia
MIN_SUGERENCIA = 0.3  # puntaje mínimo de una sugerencia
SINONIMO = 0.85
POR_SONIDO = 0.95
PREFIJO = 0.85
OTRA_DIMENSION = 0.9  # 1 kg y 1000 ml: «la de kilo» puede ser cualquiera de las dos, pero no es la misma medida

STOPWORDS = frozenset("de del el la los las un una unos unas y e o u a al en con para por favor que mas".split())

# Abreviaturas y variantes: valen lo mismo que la palabra canónica (claves y valores ya normalizados)
ABREVIATURAS = {
    "sh": "shampoo",
    "shp": "shampoo",
    "sham": "shampoo",
    "shamp": "shampoo",
    "champu": "shampoo",
    "champoo": "shampoo",
    "shampu": "shampoo",
    "acond": "acondicionador",
    "acondic": "acondicionador",
    "acondi": "acondicionador",
    "masc": "mascara",
    "mascarilla": "mascara",
    "mask": "mascara",
    "masque": "mascara",
    "oxi": "oxidante",
    "oxid": "oxidante",
    "deco": "decolorante",
    "decol": "decolorante",
    "trat": "tratamiento",
    "keratin": "keratina",
    "amp": "ampolla",
}

# Sinónimos: coinciden entre sí con un puntaje menor que la palabra misma
_GRUPOS = {
    "acondicionador": ("acondicionador", "enjuague"),
    "coloracion": ("coloracion", "tintura", "tinte"),
    "oxidante": ("oxidante", "oxigenada", "revelador"),
    "aceite": ("aceite", "oleo", "oil"),
    "serum": ("serum", "suero"),
}
SINONIMOS = {palabra: grupo for grupo, palabras in _GRUPOS.items() for palabra in palabras}

# Frases que el catálogo escribe con una palabra (sobre el texto ya en minúsculas y sin tildes)
FRASES = ((re.compile(r"\bbanos? (?:de )?cremas?\b"), "bano"),)  # «baño de crema» es el «BAÑO» del catálogo

# Unidad → (dimensión, factor a la unidad base)
UNIDADES: dict[str, tuple[str, float]] = {
    **dict.fromkeys(("ml", "mls", "cc"), ("ml", 1)),
    **dict.fromkeys(("l", "lt", "lts", "ltr", "litro", "litros"), ("ml", 1000)),
    **dict.fromkeys(("g", "gr", "grs", "grm", "gramo", "gramos"), ("g", 1)),
    **dict.fromkeys(("k", "kg", "kgs", "kilo", "kilos"), ("g", 1000)),
    **dict.fromkeys(("vol", "vols", "volumen", "volumenes"), ("vol", 1)),
    **dict.fromkeys(("u", "un", "uni", "unid", "unids", "unidad", "unidades"), ("unid", 1)),
    "mm": ("mm", 1),
    "cm": ("mm", 10),
}
_PESO_Y_VOLUMEN = {"ml", "g"}

Tipo = Literal["exacta", "canonica", "sinonimo", "aproximada", "tono"]


@dataclass(frozen=True)
class Palabra:
    original: str  # como la escribió el cliente, en minúsculas y sin tildes
    canon: str  # en singular y con abreviaturas expandidas
    grupo: str | None  # grupo de sinónimos, si tiene


@dataclass(frozen=True)
class Numero:
    original: str
    valor: float
    dimension: str | None  # None si no tiene unidad
    base: float  # el valor en la unidad base (ml, g…); sin unidad, igual a valor
    es_decimal: bool


@dataclass(frozen=True)
class Texto:
    """Un texto normalizado: palabras por un lado y números o medidas por el otro (RF-38)."""

    palabras: tuple[Palabra, ...]
    numeros: tuple[Numero, ...]


# --- Normalización (RF-36) ---------------------------------------------------------------------------------------


def _sin_tildes(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto.lower()) if unicodedata.category(c) != "Mn")


def _singular(palabra: str) -> str:
    if len(palabra) > 4 and palabra.endswith("es") and palabra[-3] in "lnrdj":
        return palabra[:-2]  # jabones → jabon, acondicionadores → acondicionador
    if len(palabra) > 3 and palabra.endswith("s") and not palabra.endswith("ss"):
        return palabra[:-1]  # cremas → crema
    return palabra


def _palabra(original: str) -> Palabra:
    base = ABREVIATURAS.get(original) or ABREVIATURAS.get(_singular(original)) or _singular(original)
    canon = ABREVIATURAS.get(base, base)
    return Palabra(original, canon, SINONIMOS.get(canon))


def _numero(texto: str) -> float:
    entero, _, decimales = texto.partition(".")
    if len(decimales) == 3:  # «1.000 ml»: separador de miles, ningún tono tiene tres decimales
        return float(entero + decimales)
    return float(texto)


def normalizar(texto: str) -> Texto:
    """Normaliza un texto de búsqueda o de catálogo con las mismas reglas."""
    limpio = re.sub(r"['’`´]", "", _sin_tildes(texto or ""))
    limpio = re.sub(r"\s+", " ", limpio)
    for frase, palabra in FRASES:
        limpio = frase.sub(palabra, limpio)
    limpio = re.sub(r"(\d),(\d)", r"\1.\2", limpio)  # 6,1 → 6.1
    limpio = re.sub(r"(\d)([a-z])", r"\1 \2", limpio)  # 400ml → 400 ml, 1k → 1 k
    limpio = re.sub(r"\bx(?=\d)", "x ", limpio)  # x900 → x 900
    tokens = re.findall(r"\d+(?:\.\d+)?|[a-z0-9]+", limpio)
    palabras: list[Palabra] = []
    numeros: list[Numero] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if re.fullmatch(r"\d+(?:\.\d+)?", token):
            valor = _numero(token)
            unidad = UNIDADES.get(tokens[i + 1]) if i + 1 < len(tokens) else None
            if unidad:
                dimension, factor = unidad
                numeros.append(Numero(f"{token} {tokens[i + 1]}", valor, dimension, valor * factor, "." in token))
                i += 2
                continue
            numeros.append(Numero(token, valor, None, valor, "." in token and len(token.partition(".")[2]) < 3))
        elif token == "x" or token in UNIDADES or len(token) == 1:
            pass  # la «x» de «por», una unidad sin número o una letra suelta no dicen nada
        else:
            palabras.append(_palabra(token))
        i += 1
    sin_relleno = [p for p in palabras if p.canon not in STOPWORDS]
    return Texto(tuple(sin_relleno or palabras), tuple(numeros))


# --- Parecido entre palabras (RF-37) -----------------------------------------------------------------------------


def clave_sonido(palabra: str) -> str:
    """Cómo suena una palabra en español: «koleston» y «coleston», «balsamo» y «valsamo» dan lo mismo."""
    s = palabra.replace("ch", "§").replace("sh", "§").replace("ph", "f").replace("ll", "y")
    s = s.replace("qu", "k").replace("q", "k")
    s = re.sub(r"g(?=[ei])", "j", s)
    s = re.sub(r"c(?=[ei])", "s", s)
    s = s.replace("c", "k").replace("z", "s").replace("v", "b").replace("w", "u").replace("h", "")
    s = re.sub(r"y(?![aeiou])", "i", s)
    s = re.sub(r"(.)\1+", r"\1", s)  # letras dobles: shampoo → champo
    return s.replace("§", "ch")


def distancia(a: str, b: str, tope: int) -> int:
    """Distancia de edición con transposiciones (Damerau, versión OSA). Corta cuando pasa de `tope`."""
    if abs(len(a) - len(b)) > tope:
        return tope + 1
    previa2: list[int] = []
    previa = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        actual = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            costo = 0 if ca == cb else 1
            actual[j] = min(previa[j] + 1, actual[j - 1] + 1, previa[j - 1] + costo)
            if i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                actual[j] = min(actual[j], previa2[j - 2] + 1)
        if min(actual) > tope:
            return tope + 1
        previa2, previa = previa, actual
    return previa[-1]


def _bigramas(palabra: str) -> frozenset[str]:
    return frozenset(palabra[i : i + 2] for i in range(len(palabra) - 1))


def parecido(a: str, b: str) -> float:
    """Parecido de 0 a 1 entre dos palabras canónicas distintas. Debajo de MIN_PALABRA_SUGERENCIA, 0."""
    if a == b:
        return 1.0
    if any(c.isdigit() for c in a + b):
        return 0.0  # «system3», «art216»: códigos, no se aproximan
    sonido_a, sonido_b = clave_sonido(a), clave_sonido(b)
    if sonido_a == sonido_b:
        return POR_SONIDO
    if min(len(a), len(b)) <= 4:
        return 0.0  # en una palabra corta, una letra de diferencia ya es otra palabra: «bano» no es «mano»
    largo = max(len(a), len(b))
    tope = int(largo * (1 - MIN_PALABRA_SUGERENCIA))
    errores = min(distancia(a, b, tope), distancia(sonido_a, sonido_b, tope))
    valor = 1 - errores / largo if errores <= tope else 0.0
    if len(a) >= 5 and b.startswith(a):
        valor = max(valor, PREFIJO)  # «hidrat» → «hidratante»
    return valor if valor >= MIN_PALABRA_SUGERENCIA else 0.0


# --- Índice (RF-39) ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ProductoIndexado:
    sku: str
    texto: Texto
    canones: frozenset[str]
    palabras_nombre: int


class Indice:
    """Catálogo normalizado, sin stock. Se reconstruye solo cuando cambia el texto del catálogo."""

    def __init__(self, productos: list[Product]):
        self.huella = huella(productos)
        self.productos: dict[str, ProductoIndexado] = {}
        for product in productos:
            texto = normalizar(f"{product.name} {product.brand or ''} {product.presentation or ''}")
            self.productos[product.sku] = ProductoIndexado(
                product.sku,
                texto,
                frozenset(p.canon for p in texto.palabras),
                len(normalizar(product.name).palabras),
            )
        self.skus = {product.sku.lower(): product.sku for product in productos}
        self.vocabulario = {canon for p in self.productos.values() for canon in p.canones}
        self._bigramas = {canon: _bigramas(canon) for canon in self.vocabulario}
        self._por_grupo: dict[str, set[str]] = {}
        for canon in self.vocabulario:
            if canon in SINONIMOS:
                self._por_grupo.setdefault(SINONIMOS[canon], set()).add(canon)
        self._parecidos: dict[str, dict[str, tuple[float, Tipo]]] = {}
        self._lock = threading.Lock()

    def parecidos(self, palabra: Palabra) -> dict[str, tuple[float, Tipo]]:
        """Las palabras del catálogo que se parecen a esta, con su parecido y de qué tipo es. Se calcula una vez."""
        if palabra.canon in self._parecidos:
            return self._parecidos[palabra.canon]
        resultado: dict[str, tuple[float, Tipo]] = {}
        bigramas = _bigramas(palabra.canon)
        for canon in self.vocabulario:
            if canon == palabra.canon:
                continue
            # Descarte rápido: sin bigramas en común ni el mismo sonido, no hay parecido posible
            if not (bigramas & self._bigramas[canon]) and clave_sonido(canon) != clave_sonido(palabra.canon):
                continue
            valor = parecido(palabra.canon, canon)
            if valor:
                resultado[canon] = (valor, "aproximada")
        if palabra.grupo:
            for canon in self._por_grupo.get(palabra.grupo, ()):
                if canon != palabra.canon and resultado.get(canon, (0.0,))[0] < SINONIMO:
                    resultado[canon] = (SINONIMO, "sinonimo")
        if palabra.canon in self.vocabulario:
            resultado[palabra.canon] = (1.0, "exacta")
        with self._lock:
            if len(self._parecidos) > 5000:
                self._parecidos.clear()
            self._parecidos[palabra.canon] = resultado
        return resultado


def huella(productos: list[Product]) -> int:
    return hash(tuple((p.sku, p.name, p.brand, p.presentation) for p in productos))


_indice: Indice | None = None
_indice_lock = threading.Lock()


def obtener_indice(productos: list[Product]) -> Indice:
    """El índice del catálogo; lo reconstruye si cambió un SKU, un nombre, una marca o una presentación."""
    global _indice
    buscada = huella(productos)
    actual = _indice
    if actual is not None and actual.huella == buscada:
        return actual
    with _indice_lock:
        if _indice is None or _indice.huella != buscada:
            _indice = Indice(productos)
        return _indice


# --- Puntaje -----------------------------------------------------------------------------------------------------


@dataclass
class _Termino:
    valor: float  # para un match: las palabras con parecido menor a MIN_PALABRA valen 0
    valor_sugerencia: float  # para una sugerencia: cuentan desde MIN_PALABRA_SUGERENCIA
    tipo: Tipo | None
    coincide_con: str | None


def _numero_coincide(q: Numero, p: Numero) -> tuple[float, Tipo | None]:
    if q.dimension:
        if p.dimension is None:
            return (1.0, "exacta") if p.valor in (q.valor, q.base) else (0.0, None)
        if p.dimension == q.dimension:
            return (1.0, "exacta") if round(p.base, 3) == round(q.base, 3) else (0.0, None)
        if {p.dimension, q.dimension} == _PESO_Y_VOLUMEN and round(p.base, 3) == round(q.base, 3):
            return OTRA_DIMENSION, "aproximada"
        return 0.0, None
    if p.valor == q.valor or (p.dimension and p.base == q.valor):
        return 1.0, "exacta"  # «400» es «400 ml»; «1000» es «1 kg»
    if p.dimension is None and p.es_decimal and not q.es_decimal and q.valor.is_integer():
        entero, _, decimales = p.original.partition(".")
        if entero == q.original:
            return 1.0, "exacta"  # «6» abarca los tonos 6.0 y 6.1
        if 2 <= len(q.original) <= 3 and int(entero) <= 12 and entero + decimales == q.original:
            return 1.0, "tono"  # «821» es el tono 8.21 (V-01)
    return 0.0, None


def _termino_numero(q: Numero, producto: ProductoIndexado) -> _Termino:
    mejor = _Termino(0.0, 0.0, None, None)
    for p in producto.texto.numeros:
        valor, tipo = _numero_coincide(q, p)
        if valor > mejor.valor:
            mejor = _Termino(valor, valor, tipo, p.original)
    return mejor


def _termino_palabra(parecidos: dict[str, tuple[float, Tipo]], producto: ProductoIndexado) -> _Termino:
    mejor = _Termino(0.0, 0.0, None, None)
    for canon in producto.canones:
        encontrado = parecidos.get(canon)
        if encontrado and encontrado[0] > mejor.valor_sugerencia:
            valor, tipo = encontrado
            mejor = _Termino(valor if valor >= MIN_PALABRA else 0.0, valor, tipo, canon)
    return mejor


@dataclass
class _Puntuado:
    product: Product
    score: float
    score_sugerencia: float
    aproximado: bool
    sugerible: bool
    sobrantes: int
    terminos: list[tuple[Palabra | Numero, _Termino]]


def _puntuar(
    product: Product, producto: ProductoIndexado, busqueda: Texto, parecidos: list[dict[str, tuple[float, Tipo]]]
) -> _Puntuado:
    terminos: list[tuple[Palabra | Numero, _Termino]] = [
        (palabra, _termino_palabra(tabla, producto))
        for palabra, tabla in zip(busqueda.palabras, parecidos, strict=True)
    ]
    terminos += [(numero, _termino_numero(numero, producto)) for numero in busqueda.numeros]
    total = len(terminos)
    score = sum(t.valor for _, t in terminos) / total
    score_sugerencia = sum(t.valor_sugerencia for _, t in terminos) / total
    aproximado = any(t.valor < 1 and t.tipo != "sinonimo" for _, t in terminos)
    palabras = [t for q, t in terminos if isinstance(q, Palabra)]
    sugerible = bool(palabras) and all(t.valor_sugerencia for t in palabras)
    cubiertas = sum(1 for t in palabras if t.valor_sugerencia)
    return _Puntuado(
        product, score, score_sugerencia, aproximado, sugerible, producto.palabras_nombre - cubiertas, terminos
    )


def _cortar(puntuados: list[_Puntuado], clave, limit: int) -> list[_Puntuado]:
    if not puntuados:
        return []
    puntuados.sort(key=lambda p: (-clave(p), p.sobrantes))
    primero = clave(puntuados[0])
    return [p for p in puntuados if clave(p) >= primero - MARGEN - 1e-9][:limit]


def _match(puntuado: _Puntuado, score: float, aproximado: bool) -> ProductMatch:
    return ProductMatch(
        **puntuado.product.model_dump(exclude={"is_low_stock"}), score=round(score, 2), aproximado=aproximado
    )


def _corregido(puntuado: _Puntuado) -> dict[str, str]:
    corregido: dict[str, str] = {}
    for q, termino in puntuado.terminos:
        if termino.coincide_con is None:
            continue
        if isinstance(q, Palabra):
            if termino.tipo in ("sinonimo", "aproximada"):
                corregido[q.original] = termino.coincide_con
            elif q.canon != _singular(q.original):
                corregido[q.original] = q.canon  # abreviatura o variante: «sh» → «shampoo»
        elif termino.tipo == "tono":
            corregido[q.original] = termino.coincide_con
    return corregido


def _es_coloracion(producto: ProductoIndexado) -> bool:
    return any(SINONIMOS.get(canon) == "coloracion" for canon in producto.canones)


def _cantidad_al_principio(query: str, busqueda: Texto) -> bool:
    """Si la búsqueda empieza con un entero sin unidad seguido de palabras, como «2 mascara argan x 1k»."""
    inicio = re.match(r"\s*(\d+)(?![\d.,])", query)
    if not inicio or not busqueda.palabras or not busqueda.numeros:
        return False
    primero = busqueda.numeros[0]
    return primero.original == inicio.group(1) and primero.dimension is None


def buscar(query: str, productos: list[Product], limit: int = 5) -> SearchResult:
    """Busca en `productos` (con el stock del momento) y devuelve matches, sugerencias y palabras corregidas."""
    indice = obtener_indice(productos)
    por_sku = {product.sku: product for product in productos}
    sku = indice.skus.get(query.strip().lower())
    if sku:
        return SearchResult(matches=[ProductMatch(**por_sku[sku].model_dump(exclude={"is_low_stock"}), score=1)])
    busqueda = normalizar(query)
    if not busqueda.palabras and not busqueda.numeros:
        return SearchResult()
    parecidos = [indice.parecidos(palabra) for palabra in busqueda.palabras]

    def puntuar_todos(sin_cantidad: Texto | None = None) -> tuple[list[_Puntuado], list[_Puntuado]]:
        puntuados = []
        for product in productos:
            producto = indice.productos[product.sku]
            # En una coloración, un número suelto que es el único de la búsqueda puede ser el tono: no se descarta
            texto = sin_cantidad or busqueda
            if _es_coloracion(producto) and sin_cantidad and not sin_cantidad.numeros:
                texto = busqueda
            puntuados.append(_puntuar(product, producto, texto, parecidos))
        return puntuados, _cortar([p for p in puntuados if p.score >= MIN_SCORE], lambda p: p.score, limit)

    puntuados, matches = puntuar_todos()
    if _cantidad_al_principio(query, busqueda):
        # El término del número va después de las palabras. Si el mejor match no lo tiene, es la cantidad del pedido
        if not matches or matches[0].terminos[len(busqueda.palabras)][1].valor < 1:
            puntuados, matches = puntuar_todos(Texto(busqueda.palabras, busqueda.numeros[1:]))
    if matches:
        return SearchResult(
            matches=[_match(p, p.score, p.aproximado) for p in matches], corregido=_corregido(matches[0])
        )
    sugeribles = [p for p in puntuados if p.sugerible and p.score_sugerencia >= MIN_SUGERENCIA]
    sugerencias = _cortar(sugeribles, lambda p: p.score_sugerencia, limit)
    if not sugerencias:
        return SearchResult()
    return SearchResult(
        sugerencias=[_match(p, p.score_sugerencia, True) for p in sugerencias], corregido=_corregido(sugerencias[0])
    )
