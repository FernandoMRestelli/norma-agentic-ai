"""Catálogos, mensajes y borradores sintéticos para los tests.

Con semilla fija: un test que falla vuelve a fallar igual, en local y en el CI.
"""

import random
from decimal import Decimal

from internal.schemas.catalog_schema import Product
from internal.schemas.order_schema import Channel, IncomingMessage, MessageType, OrderDraft, OrderItem

SEMILLA = 20261029

_PRODUCTOS = ["Shampoo", "Acondicionador", "Jabón líquido", "Detergente", "Lavandina", "Suavizante", "Desodorante"]
_MARCAS = ["Pantene", "Dove", "Ala", "Magistral", "Ayudín", "Comfort", "Rexona"]
_PRESENTACIONES = ["250 ml", "400 ml", "500 ml", "750 ml", "1 l", "3 l"]


def catalogo(cantidad: int = 20, semilla: int = SEMILLA) -> list[Product]:
    """Catálogo con SKU únicos, precios con centavos y stock entre 0 y 50."""
    rng = random.Random(semilla)
    productos = []
    for i in range(cantidad):
        nombre = rng.choice(_PRODUCTOS)
        productos.append(
            Product(
                sku=f"{nombre[:2].upper()}-{i:04d}",
                name=nombre,
                brand=rng.choice(_MARCAS),
                presentation=rng.choice(_PRESENTACIONES),
                price=Decimal(rng.randint(500, 25000)) / 100,
                stock=rng.randint(0, 50),
            )
        )
    return productos


def sin_stock(productos: list[Product], sku: str) -> list[Product]:
    """Escenario "se agotó": el mismo catálogo con un producto en stock 0."""
    return [p.model_copy(update={"stock": 0}) if p.sku == sku else p for p in productos]


def borrador(
    productos: list[Product],
    items: int = 3,
    canal: Channel = Channel.WEB,
    semilla: int = SEMILLA,
) -> OrderDraft:
    """Borrador con productos distintos del catálogo y cantidades entre 1 y 10."""
    rng = random.Random(semilla)
    elegidos = rng.sample(productos, items)
    return OrderDraft(
        draft_id=f"d-{semilla}",
        customer_id="c-sintetico",
        channel=canal,
        items=[
            OrderItem(
                sku=p.sku,
                product_name=p.name,
                presentation=p.presentation,
                quantity=rng.randint(1, 10),
                unit_price=p.price,
            )
            for p in elegidos
        ],
    )


def mensaje(tipo: MessageType, canal: Channel = Channel.WEB) -> IncomingMessage:
    """Mensaje válido del tipo pedido: texto con contenido, o audio/imagen con su URL."""
    if tipo == MessageType.TEXT:
        return IncomingMessage(customer_id="c-sintetico", channel=canal, type=tipo, text="5 shampoo Pantene 400")
    return IncomingMessage(customer_id="c-sintetico", channel=canal, type=tipo, media_url=f"https://ejemplo.test/{tipo}")


# --- Catálogo mayorista para la evaluación (RF-59, docs/spec/toma-de-pedidos-por-texto.md) ------------------------
#
# Copia las trampas del catálogo real con marcas y precios inventados: la medida va dentro del nombre, la columna
# de presentación está vacía, un mismo producto viene en varias medidas y la coloración no trae el tono.
# El SKU se arma con la marca, la línea, la variante y la medida, así los casos se pueden escribir a mano.
# `data_example/catalogo_evaluacion.csv` es la salida de `catalogo_mayorista()`. Para regenerarlo:
#   python -m tests.datos_sinteticos > data_example/catalogo_evaluacion.csv

SEMILLA_MAYORISTA = 20261112

_VARIANTES = {"KER": "KERATINA", "ARG": "ARGAN", "NUT": "NUTRICION", "COL": "COLOR", "MAT": "MATIZADOR"}
_ML = {"300": "X 300 ML", "900": "X 900 ML", "5L": "X 5 L"}
_PESO = {"250": "X 250 G", "1K": "X 1 KG"}


def _shampoos(medidas: tuple[str, ...] = ("300", "900", "5L")) -> list[tuple[str, str, str]]:
    return [
        (f"SH-{v}-{m}", f"SHAMPOO {_VARIANTES[v]} {_ML[m]}", "Shampoo")
        for v in ("KER", "ARG", "NUT", "COL", "MAT")
        for m in medidas
    ]


def _acondicionadores(medidas: tuple[str, ...] = ("300", "900")) -> list[tuple[str, str, str]]:
    return [
        (f"AC-{v}-{m}", f"ACONDICIONADOR {_VARIANTES[v]} {_ML[m]}", "Acondicionador")
        for v in ("KER", "ARG", "NUT", "COL")
        for m in medidas
    ]


_MASCARAS = [
    (f"MA-{v}-{m}", f"MASCARA {_VARIANTES[v]} {_PESO[m]}", "Tratamiento y Máscara")
    for v in ("KER", "ARG", "NUT")
    for m in ("250", "1K")
] + [
    ("BA-KAR-1K", "BAÑO DE CREMA KARITE X 1 KG", "Tratamiento y Máscara"),
    ("BA-COC-1K", "BAÑO DE CREMA COCO X 1 KG", "Tratamiento y Máscara"),
]
_OXIDANTES = [
    (f"OX-{vol}-{m}", f"OXIDANTE {vol} VOL X {m} ML", "Oxidante") for vol in (10, 20, 30, 40) for m in (90, 900)
]
_COLORACION = [("CO-60", "COLORACION X 60 G", "Coloración")]
_COLORACION_KIT = [("CO-60OX", "COLORACION X 60 G + OXIDANTE X 90 ML", "Coloración")]
_DECOLORANTES = [
    ("DE-50", "POLVO DECOLORANTE X 50 G SACHET", "Decolorante"),
    ("DE-700", "POLVO DECOLORANTE X 700 G", "Decolorante"),
]
_FINALIZACION = [
    ("FI-CPR-300", "CREMA DE PEINAR RULOS X 300 ML", "Finalización y Peinado"),
    ("FI-SER-60", "SERUM PUNTAS X 60 ML", "Finalización y Peinado"),
    ("FI-OLE-60", "OLEO ARGAN X 60 ML", "Finalización y Peinado"),
    ("FI-SPR-250", "SPRAY BRILLO X 250 ML", "Finalización y Peinado"),
    ("FI-GEL-500", "GEL FIJADOR X 500 G", "Finalización y Peinado"),
]
_CUIDADO = [
    ("CU-AMP-15", "AMPOLLA REPARADORA X 15 ML", "Cuidado y Cosmética"),
    ("CU-PRO-250", "PROTECTOR TERMICO X 250 ML", "Cuidado y Cosmética"),
]
_ACCESORIOS = [
    ("AX-PEI", "PEINE DE CORTE", "Accesorios y Herramientas"),
    ("AX-BOW", "BOWL PARA TINTURA", "Accesorios y Herramientas"),
    ("AX-PIN", "PINCEL PARA TINTURA", "Accesorios y Herramientas"),
    ("AX-GUA-100", "GUANTES DE NITRILO X 100 U", "Accesorios y Herramientas"),
    ("AX-CAP", "CAPA DE CORTE", "Accesorios y Herramientas"),
    ("AX-ALU-5K", "PAPEL ALUMINIO X 5 KG", "Accesorios y Herramientas"),
]

_CAPILAR = [*_shampoos(), *_acondicionadores(), *_MASCARAS, *_FINALIZACION, *_CUIDADO]
_COLOR = [*_OXIDANTES, *_DECOLORANTES]

# Qué trabaja cada marca. Que no todas tengan todo permite casos de «esa marca no tiene esa categoría»
_LINEAS_POR_MARCA: dict[str, list[tuple[str, str, str]]] = {
    "NORDIKA": [*_CAPILAR, *_COLOR, *_COLORACION, *_COLORACION_KIT],
    "BELKOR": [*_CAPILAR, *_COLOR, *_COLORACION, *_COLORACION_KIT],
    "FIORELA": [*_CAPILAR, *_COLOR, *_COLORACION, *_COLORACION_KIT],
    "KAPRIS": [*_CAPILAR, *_COLOR, *_COLORACION, *_COLORACION_KIT],
    "LUMINE": _CAPILAR,
    "ORKIDEA": _CAPILAR,
    "TREVIA": _CAPILAR,
    "SILKARA": _CAPILAR,
    "DUOMO": [*_COLOR, *_COLORACION],
    "ARGANIA": [*_COLOR, *_COLORACION],
    "BRISA": [*_shampoos(("900", "5L")), *_acondicionadores(("900",))],
    "MONTELLO": _ACCESORIOS,
}

# Los únicos productos sin stock o con poco: el resto tiene 15 unidades o más
STOCK_MAYORISTA = {"NOR-SH-ARG-900": 0, "FIO-OX-30-900": 0, "BEL-MA-KER-1K": 3, "KAP-DE-700": 2}
STOCK_MINIMO_MAYORISTA = 10
COLUMNAS_CATALOGO = ["sku", "nombre", "marca", "presentacion", "categoria", "precio", "stock", "stock_minimo"]


def catalogo_mayorista(semilla: int = SEMILLA_MAYORISTA) -> list[Product]:
    """Unos 400 productos de 12 marcas inventadas, con el formato del catálogo real."""
    rng = random.Random(semilla)
    productos = []
    for marca, lineas in _LINEAS_POR_MARCA.items():
        for codigo, nombre, categoria in lineas:
            sku = f"{marca[:3]}-{codigo}"
            productos.append(
                Product(
                    sku=sku,
                    name=f"{marca} {nombre}",
                    brand=marca,
                    category=categoria,
                    price=Decimal(rng.randint(18, 420) * 100),
                    stock=STOCK_MAYORISTA.get(sku, rng.randint(15, 60)),
                    min_stock=STOCK_MINIMO_MAYORISTA,
                )
            )
    return productos


def filas_catalogo(productos: list[Product]) -> list[list[str]]:
    """Las filas del CSV, con las columnas de la Sheet 1."""
    return [
        [p.sku, p.name, p.brand or "", p.presentation or "", p.category or "", f"{p.price:.2f}", str(p.stock),
         str(p.min_stock)]
        for p in productos
    ]


if __name__ == "__main__":
    import csv
    import sys

    salida = csv.writer(sys.stdout, lineterminator="\n")
    salida.writerow(COLUMNAS_CATALOGO)
    salida.writerows(filas_catalogo(catalogo_mayorista()))
