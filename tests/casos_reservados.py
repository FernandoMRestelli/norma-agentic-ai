"""Casos `reservado` de los conjuntos `mensajes` y `pedidos` de la evaluación (M8-1, D-50).

Salen de un generador con una semilla propia, y no de la mano de quien ajusta los prompts: así ningún ejemplo de un
prompt, una skill o una BR se escribe mirándolos. Las formas de escribir (abreviaturas, errores, medidas) se sortean
de listas que no copian los casos de desarrollo ni los ejemplos de los prompts, y `test_contaminacion.py` lo controla.

Si alguien ajusta algo mirando un caso reservado, ese caso deja de medir: se cambia `SEMILLA_RESERVADOS` y se
regeneran todos. Para escribirlos en `data_example/`, dejando igual los de desarrollo:

    python -m tests.casos_reservados
"""

import json
import random
from pathlib import Path

from tests.datos_sinteticos import STOCK_MAYORISTA, catalogo_mayorista

SEMILLA_RESERVADOS = 20261125
DATOS = Path(__file__).resolve().parent.parent / "data_example"
ARCHIVOS = {"mensajes": DATOS / "mensajes.json", "pedidos": DATOS / "pedidos_evaluacion.json"}

# --- Formas de escribir cada producto del catálogo mayorista ------------------------------------------------------

_TIPOS = {
    "SH": ["sh", "shampoo", "champu", "shamp"],
    "AC": ["acond", "acondicionador", "enjuague", "acondi"],
    "MA": ["mascara", "masc", "mascarilla", "máscara"],
    "OX": ["oxi", "oxidante", "ox"],
}
_VARIANTES = {
    "KER": ["keratina", "queratina"],
    "ARG": ["argan", "argán"],
    "NUT": ["nutricion", "nutrición"],
    "COL": ["color"],
    "MAT": ["matizador", "matiz"],
}
_MEDIDAS = {
    "300": ["300", "x300", "x 300", "300ml"],
    "900": ["900", "x900", "x 900", "900ml"],
    "5L": ["5l", "5 litros", "x5 lts", "bidon 5l"],
    "250": ["250", "x250", "250g", "x 250 gr"],
    "1K": ["1k", "1 kilo", "x1kg", "de kilo"],
    "90": ["90", "x90", "90ml"],
}
_VOLUMENES = ["{vol} vol", "{vol}vol", "{vol} volumenes"]
# Productos de una sola medida: el nombre alcanza, con la medida o sin ella
_UNICOS = {
    "BA-KAR-1K": ["baño de crema karite", "baño crema karite", "bano de crema karite"],
    "BA-COC-1K": ["baño de crema coco", "baño crema coco", "bano de crema de coco"],
    "FI-CPR-300": ["crema de peinar", "crema peinar rulos", "crema para rulos"],
    "FI-SER-60": ["serum", "serum puntas", "sérum de puntas"],
    "FI-OLE-60": ["oleo", "oleo argan", "óleo de argán"],
    "FI-SPR-250": ["spray brillo", "brillo en spray", "spray de brillo"],
    "FI-GEL-500": ["gel", "gel fijador"],
    "CU-AMP-15": ["ampolla", "ampollas reparadoras", "ampolla reparadora"],
    "CU-PRO-250": ["protector termico", "termoprotector", "protector de calor"],
    "AX-PEI": ["peine de corte", "peine"],
    "AX-BOW": ["bowl", "bowl para tintura"],
    "AX-PIN": ["pincel", "pincel para tintura"],
    "AX-GUA-100": ["guantes", "caja de guantes", "guantes de nitrilo"],
    "AX-CAP": ["capa de corte", "capa"],
    "AX-ALU-5K": ["papel aluminio", "aluminio"],
}
_DECOLORANTES = {
    "DE-50": ["polvo decolorante 50", "deco 50", "deco sachet", "decolorante x50"],
    "DE-700": ["deco 700", "polvo decolorante x700", "decolorante 700 g"],
}
_DECOLORANTE_SIN_MEDIDA = ["polvo decolorante", "deco"]
# Errores de tipeo de una letra o de sonido, como los de los pedidos reales
_MARCAS_MAL_ESCRITAS = {
    "NORDIKA": "nordica", "BELKOR": "belcor", "FIORELA": "fiorella", "KAPRIS": "capris", "ORKIDEA": "orquidea",
    "SILKARA": "silcara", "MONTELLO": "montelo",
}  # fmt: skip
# Una marca que no está en ningún catálogo, ni en los ejemplos de los prompts
MARCA_INEXISTENTE = "Ondessa"
# Coloración en tono/cantidad: «74/2» es tono 7.4, 2 unidades. Sin tonos de tres cifras, que tienen dos lecturas
_TONOS = ["4", "5", "6", "7", "8", "9", "4.5", "5.3", "5.4", "6.3", "6.7", "7.4", "7.7", "8.3", "9.1", "9.2"]
_ENCABEZADOS_COLORACION = ["coloracion", "tinturas", "tintura", "color"]
# Lo que una marca no trabaja, por las categorías del catálogo mayorista. La cantidad se sortea aparte
_AJENOS = {
    "capilar": ["tintura", "oxidante 30 vol 900", "polvo decolorante 700"],
    "color": ["shampoo argan 900", "mascara nutricion 1k", "serum puntas"],
    "accesorios": ["shampoo color 300", "oxidante 10 vol 90"],
    "completo": ["peine de corte", "capa de corte"],
}
_CON_SHAMPOO = ("capilar", "completo")
_ACEPTACIONES = ["listo", "de una", "perfecto, mandalo", "sí, cerralo así"]
_RECHAZOS = ["no, mejor no", "no, lo dejo para la semana que viene", "uh no, cancelá que ya conseguí"]
_SUMAS = ["agregale", "sumá también", "y esto otro"]
_BAJO_STOCK = set(STOCK_MAYORISTA)


def _marcas() -> dict[str, list[str]]:
    """Los códigos de producto de cada marca (lo que sigue al prefijo del SKU)."""
    por_marca: dict[str, list[str]] = {}
    for producto in catalogo_mayorista():
        por_marca.setdefault(producto.brand, []).append(producto.sku.split("-", 1)[1])
    return por_marca


def _prefijo(marca: str) -> str:
    return marca[:3]


def _rubro(marca: str, codigos: list[str]) -> str:
    """`completo` trabaja capilar y color; `capilar`, `color` y `accesorios`, solo eso."""
    if any(c.startswith("AX-") for c in codigos):
        return "accesorios"
    capilar, color = any(c.startswith("SH-") for c in codigos), any(c.startswith("CO-") for c in codigos)
    return "completo" if capilar and color else "capilar" if capilar else "color"


def _encabezado(rng: random.Random, marca: str) -> str:
    formas = [marca.capitalize(), marca.lower(), marca]
    if marca in _MARCAS_MAL_ESCRITAS:
        formas.append(_MARCAS_MAL_ESCRITAS[marca])
    return rng.choice(formas)


def _forma(rng: random.Random, codigo: str, *, con_medida: bool = True) -> str:
    """Cómo escribe un cliente un producto del catálogo mayorista. Sin medida, solo si el producto tiene varias."""
    if codigo in _UNICOS:
        return rng.choice(_UNICOS[codigo])
    if codigo in _DECOLORANTES:
        return rng.choice(_DECOLORANTES[codigo] if con_medida else _DECOLORANTE_SIN_MEDIDA)
    tipo, variante, medida = codigo.split("-")
    if tipo == "OX":
        partes = [rng.choice(_TIPOS["OX"]), rng.choice(_VOLUMENES).format(vol=variante)]
    else:
        partes = [rng.choice(_TIPOS[tipo]), rng.choice(_VARIANTES[variante])]
    if con_medida:
        partes.append(rng.choice(_MEDIDAS[medida]))
    return " ".join(partes)


def _opciones(marca: str, codigo: str, codigos: list[str]) -> list[str]:
    """Los SKU que comparten tipo y variante con `codigo`: lo que puede querer decir un renglón sin medida."""
    raiz = "DE-" if codigo.startswith("DE-") else codigo.rsplit("-", 1)[0] + "-"
    return [f"{_prefijo(marca)}-{c}" for c in codigos if c.startswith(raiz)]


def _con_varias_medidas(marca: str, codigos: list[str]) -> list[str]:
    return [
        c for c in codigos
        if c.split("-")[0] in ("SH", "AC", "MA", "OX", "DE") and len(_opciones(marca, c, codigos)) > 1
        and not set(_opciones(marca, c, codigos)) & _BAJO_STOCK
    ]  # fmt: skip


def _pedibles(marca: str, codigos: list[str]) -> list[str]:
    """Lo que se puede pedir sin dudas ni falta de stock: la coloración necesita tono y tiene su propio caso."""
    return [c for c in codigos if not c.startswith("CO-") and f"{_prefijo(marca)}-{c}" not in _BAJO_STOCK]


class _Lista:
    """Un pedido agrupado por marca: los renglones del texto y lo que espera la evaluación de cada uno."""

    def __init__(self, rng: random.Random, marcas: dict[str, list[str]]):
        self.rng, self.marcas = rng, marcas
        self.lineas: list[str] = []
        self.items: list[dict] = []
        self.dudas: list[dict] = []
        self.afuera: list[str] = []

    def marca(self, marca: str) -> None:
        self.lineas.append(_encabezado(self.rng, marca))

    def claros(self, marca: str, cantidad: int, *, evitar: set[str] = frozenset()) -> None:
        codigos = self.marcas[marca]
        usados = {item["sku"] for item in self.items} | set(evitar)
        disponibles = [c for c in _pedibles(marca, codigos) if f"{_prefijo(marca)}-{c}" not in usados]
        for codigo in self.rng.sample(disponibles, min(cantidad, len(disponibles))):
            unidades = self.rng.randint(1, 6)
            self.lineas.append(f"{unidades} {_forma(self.rng, codigo)}")
            self.items.append({"sku": f"{_prefijo(marca)}-{codigo}", "cantidad": unidades})

    def sin_medida(self, marca: str, cantidad: int) -> None:
        codigos = self.marcas[marca]
        usados = {item["sku"] for item in self.items} | {sku for d in self.dudas for sku in d["opciones"]}
        candidatos = [c for c in _con_varias_medidas(marca, codigos) if not set(_opciones(marca, c, codigos)) & usados]
        elegidos: list[str] = []
        for codigo in self.rng.sample(candidatos, len(candidatos)):
            if len(elegidos) == cantidad:
                break
            if any(set(_opciones(marca, codigo, codigos)) & set(_opciones(marca, otro, codigos)) for otro in elegidos):
                continue
            elegidos.append(codigo)
            renglon = f"{self.rng.randint(1, 5)} {_forma(self.rng, codigo, con_medida=False)}"
            self.lineas.append(renglon)
            self.dudas.append({"renglon": renglon, "opciones": _opciones(marca, codigo, codigos)})

    def ajeno(self, marca: str) -> None:
        """Un renglón que esa marca no trabaja, distinto de los otros que quedan afuera: se avisa uno por uno."""
        dichos = {renglon.split(" ", 1)[1] for renglon in self.afuera}
        producto = self.rng.choice([p for p in _AJENOS[_rubro(marca, self.marcas[marca])] if p not in dichos])
        renglon = f"{self.rng.randint(1, 4)} {producto}"
        self.lineas.append(renglon)
        self.afuera.append(renglon)

    def texto(self) -> str:
        return "\n".join(self.lineas)

    def esperado(self, resultado: str = "borrador") -> dict:
        esperado: dict = {"resultado": resultado, "items": self.items}
        if self.dudas:
            esperado["dudas"] = self.dudas
        if self.afuera:
            esperado["afuera"] = self.afuera
        return esperado


def _caso(id: str, descripcion: str, texto: str, esperado: dict, rng: random.Random, previo=None) -> dict:
    caso: dict = {"id": id, "grupo": "reservado", "descripcion": descripcion}
    if previo is not None:
        caso["previo"] = previo
    caso["mensaje"] = {
        "customer_id": f"cli-{id}", "channel": rng.choice(["telegram", "whatsapp"]), "type": "text", "text": texto,
    }  # fmt: skip
    caso["esperado"] = esperado
    return caso


def _lista_clara(rng, marcas, cuantas: int, por_marca: tuple[int, int]) -> _Lista:
    lista = _Lista(rng, marcas)
    capilares = [m for m, c in marcas.items() if _rubro(m, c) != "color"]
    for marca in rng.sample(capilares, cuantas):
        lista.marca(marca)
        lista.claros(marca, rng.randint(*por_marca))
    return lista


def pedidos_reservados(semilla: int = SEMILLA_RESERVADOS) -> list[dict]:
    """Los casos reservados del conjunto `pedidos`, contra `catalogo_evaluacion.csv`: los mismos tipos de pedido que
    los de desarrollo, con otras marcas, otros productos y otras formas de escribirlos."""
    rng = random.Random(semilla)
    marcas = _marcas()
    casos = []

    for numero, (cuantas, por_marca) in enumerate([(2, (4, 6)), (3, (3, 5)), (2, (6, 8))], 1):
        lista = _lista_clara(rng, marcas, cuantas, por_marca)
        casos.append(_caso(
            f"res-lista-clara-{numero}",
            f"Lista de {len(lista.items)} renglones en {cuantas} marcas, todos con medida: una propuesta con todas",
            lista.texto(), lista.esperado(), rng,
        ))  # fmt: skip

    lista = _lista_clara(rng, marcas, 5, (5, 7))
    casos.append(_caso(
        "res-lista-larga",
        f"Lista larga de {len(lista.items)} renglones en cinco marcas, todos claros: ninguno se pierde",
        lista.texto(), lista.esperado(), rng,
    ))  # fmt: skip

    for numero in (1, 2):
        lista = _Lista(rng, marcas)
        capilares = [m for m, c in marcas.items() if _rubro(m, c) in _CON_SHAMPOO]
        for marca in rng.sample(capilares, 2):
            lista.marca(marca)
            lista.claros(marca, rng.randint(2, 4))
            lista.sin_medida(marca, rng.randint(1, 2))
        casos.append(_caso(
            f"res-dudas-de-medida-{numero}",
            f"{len(lista.dudas)} renglones sin medida de productos que vienen en varias: propone lo claro y pregunta "
            "las dudas juntas", lista.texto(), lista.esperado(), rng,
        ))  # fmt: skip

    for numero in (1, 2):
        lista = _Lista(rng, marcas)
        for marca in rng.sample(list(marcas), 2):
            lista.marca(marca)
            lista.claros(marca, rng.randint(2, 4))
            lista.ajeno(marca)
        lista.lineas += [MARCA_INEXISTENTE, f"{rng.randint(1, 4)} shampoo"]
        lista.afuera.append(lista.lineas[-1])
        casos.append(_caso(
            f"res-fuera-de-catalogo-{numero}",
            "Dos marcas con un producto que no trabajan y una marca que no existe: propone el resto y avisa lo que "
            "quedó afuera", lista.texto(), lista.esperado(), rng,
        ))  # fmt: skip

    casos.append(_coloracion(rng, marcas))
    casos.append(_stock_corto(rng, marcas))

    previa = _lista_clara(rng, marcas, 1, (3, 4))
    suma = _Lista(rng, marcas)
    for marca in rng.sample([m for m, c in marcas.items() if _rubro(m, c) in _CON_SHAMPOO], 2):
        suma.marca(marca)
        suma.claros(marca, rng.randint(1, 3), evitar={item["sku"] for item in previa.items})
    casos.append(_caso(
        "res-pedido-en-dos-mensajes", "El segundo mensaje suma renglones a la propuesta del primero",
        f"{rng.choice(_SUMAS)}\n{suma.texto()}", {"resultado": "borrador", "items": previa.items + suma.items}, rng,
        previo=[previa.texto()],
    ))  # fmt: skip

    lista = _lista_clara(rng, marcas, 2, (3, 4))
    casos.append(_caso(
        "res-lista-y-acepta", f"Lista de {len(lista.items)} renglones y una aceptación: registra todas las líneas",
        rng.choice(_ACEPTACIONES), lista.esperado("pedido"), rng, previo=[lista.texto()],
    ))  # fmt: skip

    lista = _lista_clara(rng, marcas, 1, (3, 4))
    casos.append(_caso(
        "res-lista-y-rechaza", "Recibe la propuesta y la rechaza: no se registra nada",
        rng.choice(_RECHAZOS), {"resultado": "sin_pedido"}, rng, previo=[lista.texto()],
    ))  # fmt: skip
    return casos


def _coloracion(rng: random.Random, marcas: dict[str, list[str]]) -> dict:
    """Coloración en tono/cantidad debajo de una marca que tiene una sola coloración (sin el kit con oxidante)."""
    marca = rng.choice([m for m, c in marcas.items() if "CO-60" in c and "CO-60OX" not in c])
    lista = _Lista(rng, marcas)
    lista.marca(marca)
    lista.lineas.append(rng.choice(_ENCABEZADOS_COLORACION))
    for tono in rng.sample(_TONOS, rng.randint(3, 5)):
        unidades = rng.randint(1, 3)
        lista.lineas.append(f"{tono.replace('.', '')}/{unidades}")
        lista.items.append({"sku": f"{_prefijo(marca)}-CO-60", "cantidad": unidades, "nota": f"tono {tono}"})
    lista.claros(marca, 2)
    return _caso(
        "res-coloracion-tono-cantidad",
        "Coloración en tono/cantidad debajo de la marca: una línea por tono, con el tono en la nota",
        lista.texto(),
        lista.esperado(),
        rng,
    )


def _stock_corto(rng: random.Random, marcas: dict[str, list[str]]) -> dict:
    """Pide más de lo que queda de un producto y otro está agotado: ofrece lo que hay y deja afuera el agotado."""
    lista = _Lista(rng, marcas)
    lista.marca("KAPRIS")
    pedidas = rng.randint(4, 6)
    lista.lineas.append(f"{pedidas} {_forma(rng, 'DE-700')}")
    lista.items.append({"sku": "KAP-DE-700", "cantidad": pedidas})
    lista.claros("KAPRIS", 2)
    lista.marca("FIORELA")
    agotado = f"{rng.randint(2, 4)} {_forma(rng, 'OX-30-900')}"
    lista.lineas.append(agotado)
    lista.items.append({"sku": "FIO-OX-30-900", "cantidad": int(agotado.split()[0])})
    lista.afuera.append(agotado)
    lista.claros("FIORELA", 2)
    esperado = lista.esperado("sin_stock")
    propuesta = [dict(item) for item in lista.items if item["sku"] != "FIO-OX-30-900"]
    propuesta[0]["cantidad"] = STOCK_MAYORISTA["KAP-DE-700"]
    esperado["propuesta"] = propuesta
    return _caso(
        "res-stock-corto",
        "De un renglón queda menos de lo que pide y otro está agotado: ofrece lo que queda, deja afuera el agotado y "
        "propone el resto", lista.texto(), esperado, rng,
    )  # fmt: skip


# --- Conjunto `mensajes`, contra `catalogo.csv` -----------------------------------------------------------------

_PIDE = ["", "mandame ", "necesito ", "pasame ", "me das "]


def mensajes_reservados(semilla: int = SEMILLA_RESERVADOS) -> list[dict]:
    """Los casos reservados del conjunto `mensajes`: los mismos tipos de mensaje que los de desarrollo, con otros
    productos del catálogo chico y otras formas de pedirlos."""
    rng = random.Random(semilla + 1)
    casos = []

    def pide() -> str:
        return rng.choice(_PIDE)

    def caso(id: str, descripcion: str, texto: str, esperado: dict, previo: list[str] | None = None) -> None:
        casos.append(_caso(id, descripcion, texto, esperado, rng, previo=previo))

    n = rng.randint(2, 6)
    caso(
        "res-un-producto",
        "Un producto con marca y medida claras",
        f"{pide()}{n} acondicionadores pantene de 400",
        {"resultado": "borrador", "items": [{"sku": "AC-PAN-400", "cantidad": n}]},
    )
    n = rng.randint(3, 12)
    caso(
        "res-una-sola-medida",
        "El producto viene en una sola medida: no hace falta preguntarla",
        f"{pide()}{n} shampoo sedal",
        {"resultado": "borrador", "items": [{"sku": "SH-SED-340", "cantidad": n}]},
    )
    a, b = rng.randint(4, 12), rng.randint(2, 6)
    caso(
        "res-dos-productos",
        "Dos productos de marcas distintas en una frase",
        f"{pide()}{a} jabones dove y {b} desodorantes rexona",
        {
            "resultado": "borrador",
            "items": [{"sku": "JAB-DOV-90", "cantidad": a}, {"sku": "DES-REX-150", "cantidad": b}],
        },
    )
    a, b = rng.randint(2, 8), rng.randint(2, 5)
    caso(
        "res-tono-y-oxidante",
        "Una tintura con tono y su oxidante",
        f"{a} oxidantes koleston 20 vol y {b} tinturas koleston 6.0",
        {"resultado": "borrador", "items": [{"sku": "OX-KOL-20", "cantidad": a}, {"sku": "TIN-KOL-60", "cantidad": b}]},
    )
    palabras, n, sku = rng.choice(
        [
            ("una docena de pastas colgate", 12, "PAS-COL-90"),
            ("un par de cepillos de dientes colgate", 2, "CEP-COL-MED"),
        ]
    )
    caso(
        "res-cantidad-en-palabras",
        "La cantidad viene en palabras",
        palabras,
        {"resultado": "borrador", "items": [{"sku": sku, "cantidad": n}]},
    )
    caso(
        "res-falta-la-medida",
        "El shampoo viene en dos medidas y no dice cuál",
        f"{pide()}{rng.randint(2, 6)} shampoo pantene",
        {"resultado": "aclaracion", "opciones": ["SH-PAN-200", "SH-PAN-400"]},
    )
    caso(
        "res-falta-el-tono",
        "Pide tinturas sin decir el tono",
        f"{pide()}{rng.randint(2, 4)} tintas koleston",
        {"resultado": "aclaracion", "opciones": ["TIN-KOL-60", "TIN-KOL-61", "TIN-KOL-71"]},
    )
    n = rng.randint(6, 10)
    caso(
        "res-no-alcanza",
        "Pide más de lo que queda",
        f"{pide()}{n} algodones estrella",
        {"resultado": "sin_stock", "items": [{"sku": "ALG-EST-140", "cantidad": n}]},
    )
    n = rng.randint(1, 3)
    caso(
        "res-agotado",
        "El tono que pide está agotado",
        f"{n} tinturas koleston 7.1 rubio medio",
        {"resultado": "sin_stock", "items": [{"sku": "TIN-KOL-71", "cantidad": n}]},
    )
    caso(
        "res-no-lo-trabajamos",
        "Una marca que no está en el catálogo",
        f"tenés shampoo {MARCA_INEXISTENTE}? quiero {rng.randint(2, 5)}",
        {"resultado": "fuera_de_catalogo"},
    )

    n = rng.randint(2, 8)
    caso(
        "res-acepta",
        "Acepta la propuesta con una palabra",
        rng.choice(["listo", "de una"]),
        {"resultado": "pedido", "items": [{"sku": "SH-SED-340", "cantidad": n}]},
        previo=[f"{n} shampoo sedal"],
    )
    n = rng.randint(2, 6)
    caso(
        "res-acepta-con-otras-palabras",
        "Acepta la propuesta con una frase",
        "perfecto, mandalo así",
        {"resultado": "pedido", "items": [{"sku": "DES-REX-150", "cantidad": n}]},
        previo=[f"{n} desodorantes rexona"],
    )
    n = rng.randint(4, 12)
    caso(
        "res-rechaza",
        "Rechaza la propuesta",
        "no, mejor lo dejo",
        {"resultado": "sin_pedido", "kinds": ["informacion", "descartado"], "propuesta_final": []},
        previo=[f"{n} jabones dove"],
    )
    antes, despues = rng.sample(range(2, 10), 2)
    caso(
        "res-cambia-la-cantidad",
        "Cambia la cantidad de la propuesta",
        f"mejor ponele {despues}",
        {"resultado": "borrador", "items": [{"sku": "PAS-COL-90", "cantidad": despues}]},
        previo=[f"{antes} pastas colgate"],
    )
    caso("res-saludo", "Solo saluda", rng.choice(["buenas tardes!", "hola, ¿cómo va?"]), {"resultado": "informacion"})
    caso(
        "res-pide-a-alguien",
        "Pide que lo atienda alguien del equipo",
        "me pasás con alguien del local?",
        {"resultado": "derivada"},
    )
    n = rng.randint(2, 6)
    caso(
        "res-acepta-con-condicion-de-pago",
        "Acepta con una condición de pago que no se puede asegurar",
        "dale, pero cobrame a fin de mes",
        {
            "resultado": "sin_pedido",
            "kinds": ["propuesta", "informacion", "derivada"],
            "propuesta_final": [{"sku": "SH-ELV-400", "cantidad": n}],
        },
        previo=[f"{n} shampoo elvive"],
    )
    n = rng.randint(2, 6)
    caso(
        "res-pregunta-el-precio",
        "Pregunta el precio de la propuesta vigente",
        "¿a cuánto está cada uno?",
        {
            "resultado": "sin_pedido",
            "kinds": ["propuesta", "informacion"],
            "propuesta_final": [{"sku": "AC-PAN-400", "cantidad": n}],
            "dice": ["7\\.?600"],
        },
        previo=[f"{n} acondicionadores pantene de 400"],
    )
    return casos


GENERADORES = {"mensajes": mensajes_reservados, "pedidos": pedidos_reservados}


def con_reservados(casos: list[dict], reservados: list[dict]) -> list[dict]:
    """Los casos de desarrollo, con su grupo explícito, seguidos de los reservados nuevos."""
    desarrollo = [caso for caso in casos if caso.get("grupo", "desarrollo") == "desarrollo"]
    return [{"id": caso["id"], "grupo": "desarrollo", **caso} for caso in desarrollo] + reservados


def escribir(conjunto: str) -> None:
    archivo = ARCHIVOS[conjunto]
    casos = con_reservados(json.loads(archivo.read_text(encoding="utf-8")), GENERADORES[conjunto]())
    archivo.write_text(json.dumps(casos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    for nombre in ARCHIVOS:
        escribir(nombre)
