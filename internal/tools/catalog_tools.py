"""Tools de catálogo que el agente puede invocar (solo lectura sobre Google Sheet 1)."""

import re
import unicodedata
from collections import Counter
from typing import Any

from internal.schemas.catalog_schema import Product, ProductMatch, SearchResult
from internal.services import busqueda_service, inventory_service


def buscar_productos(query: str, limit: int = 5, preferidos: frozenset[str] = frozenset()) -> SearchResult:
    """Busca productos tolerando errores, abreviaturas y medidas escritas de otra forma (RF-07, RF-36 a RF-39).

    El índice guarda el catálogo normalizado; el stock sale fresco de `inventory_service` en cada búsqueda.
    `preferidos` son los SKU que el cliente ya compró: van primero entre los que coinciden igual (RF-73).
    """
    return busqueda_service.buscar(query, inventory_service.get_catalog(), limit, preferidos)


def _citar(textos: list[str]) -> str:
    return ", ".join(f"«{texto}»" for texto in textos)


def hint_busqueda(resultado: SearchResult) -> str | None:
    """Explica al agente por qué una búsqueda no encontró una coincidencia segura y qué hacer con eso.

    Es para el modelo, no para el cliente: puede nombrar productos, pero nunca el número de stock.
    None cuando no hay nada que explicar (por ejemplo, el único cambio fue corregir un error de tipeo).
    """
    pistas = resultado.pistas
    if pistas is None:
        return None
    partes = []
    if pistas.no_estan:
        partes.append(
            f"{_citar(pistas.no_estan)}: no {'aparecen' if len(pistas.no_estan) > 1 else 'aparece'} en ningún "
            "producto del catálogo. Si es la marca o el producto, "
            "no se trabaja: avisale al cliente y no ofrezcas otro en su lugar como si fuera lo pedido."
        )
        if pistas.sin_lo_que_no_esta:
            partes.append(
                f"Sin eso hay: {', '.join(pistas.sin_lo_que_no_esta)}. Si {_citar(pistas.no_estan)} dice para qué lo "
                "quiere y no es una marca, no digas que no lo trabajás: ofrecé esos y preguntale si le sirven."
            )
    primero = pistas.cercanos[0] if pistas.cercanos else None
    faltan = [f for f in (primero.falta if primero else []) if f not in pistas.no_estan]
    medidas_que_faltan = [f for f in faltan if any(c.isdigit() for c in f)]
    if medidas_que_faltan and pistas.medidas:
        partes.append(
            f"No hay ninguno en {_citar(medidas_que_faltan)}. De lo buscado hay: {', '.join(pistas.medidas)}. "
            "Preguntale al cliente cuál quiere; no elijas por él."
        )
    elif faltan and primero:
        hay = "El resultado más parecido" if (resultado.matches or resultado.sugerencias) else "Lo más cercano"
        partes.append(
            f"{hay} ({primero.name}) no tiene {_citar(faltan)}: confirmalo con el cliente antes de proponerlo, "
            "o buscá de nuevo cambiando o sacando esa parte."
        )
    if not partes and not resultado.matches and not resultado.sugerencias:
        partes.append(
            "No hay nada parecido en el catálogo. Avisale al cliente que no lo trabajás; no inventes un producto."
        )
    return " ".join(partes) or None


def search_products(query: str, limit: int = 5) -> list[ProductMatch]:
    """Solo las coincidencias confiables de `buscar_productos`."""
    return buscar_productos(query, limit).matches


def get_product(sku: str) -> Product | None:
    """Devuelve el producto con ese SKU, o None si no existe."""
    return inventory_service.get_product(sku)


MAX_CANDIDATOS = 3  # por renglón: alcanza para preguntar y mantiene chico el resultado de un pedido largo
# Un solo producto con este puntaje o más se toma como seguro aunque una palabra venga mal escrita («queratina»):
# en una lista de 30 renglones no se pregunta «¿es este?» por cada error de tipeo. Por debajo, sí (BR-09)
SCORE_SEGURO = 0.9


def _candidato(match: ProductMatch) -> dict[str, Any]:
    """Lo mínimo para proponer o preguntar. El número de stock no sale: es interno."""
    candidato = {"sku": match.sku, "name": match.name, "price": str(match.price), "hay_stock": match.stock > 0}
    return {**candidato, "comprado_antes": True} if match.comprado_antes else candidato


# Un producto que se vende por caja dice cuántas unidades trae en el nombre: «AMPOLLA 15ML X 12 UNID»
_POR_CAJA = re.compile(r"\bx\s*(\d{1,3})\s*unid", re.IGNORECASE)
# El cliente ya dijo si cuenta cajas o unidades: «2 cajas», «24 u», «24 unidades»
_UNIDAD_ESCRITA = re.compile(r"\b(?:cajas?|unid\w*|u)\b", re.IGNORECASE)


def caja_dudosa(producto: Product, cantidad: int, texto: str, unidad: str | None = None) -> int | None:
    """Las unidades por caja si la cantidad puede ser de cajas o de unidades y el cliente no dijo cuál; si no, None.

    «24» de una ampolla que viene de a 12 son 24 cajas o 2 cajas: hay que preguntar. «2» solo pueden ser cajas.
    """
    if unidad or _UNIDAD_ESCRITA.search(texto) or not (caja := _POR_CAJA.search(producto.name)):
        return None
    por_caja = int(caja.group(1))
    if por_caja < 2 or cantidad < por_caja or cantidad % por_caja:
        return None
    return por_caja


def _renglon(
    n: int,
    texto: str,
    marca: str | None,
    cantidad: int,
    productos: list[Product],
    preferidos: frozenset[str],
    unidad: str | None = None,
) -> dict[str, Any]:
    # La marca del encabezado entra a la búsqueda salvo que el renglón ya la nombre
    de_la_marca = {p.canon for p in busqueda_service.normalizar(marca or "").palabras}
    del_texto = {p.canon for p in busqueda_service.normalizar(texto).palabras}
    consulta = texto if de_la_marca <= del_texto else f"{texto} {marca}"
    resultado = busqueda_service.buscar(consulta, productos, MAX_CANDIDATOS, preferidos)
    encontrados = resultado.matches or resultado.sugerencias
    salida: dict[str, Any] = {"n": n, "total": resultado.total, "candidatos": [_candidato(m) for m in encontrados]}
    if hint := hint_busqueda(resultado):
        salida["_hint"] = hint
    if not encontrados:
        return {**salida, "estado": "no_encontrado"}
    primero = resultado.matches[0] if resultado.matches else None
    if primero is None or (primero.aproximado and primero.score < SCORE_SEGURO):
        return {**salida, "estado": "aproximado"}
    if resultado.total > 1:
        return {**salida, "estado": "varias", "difieren_en": resultado.difieren_en}
    unico = resultado.matches[0]
    if por_caja := caja_dudosa(unico, cantidad, texto, unidad):
        # Antes que el stock: según cuál sea, se piden `cantidad` cajas o `cantidad / por_caja`
        salida["_hint"] = (
            f"Viene en caja de {por_caja}: «{cantidad}» pueden ser {cantidad} cajas o {cantidad // por_caja} "
            f"(las {cantidad} unidades). Preguntale al cliente cuál quiso decir antes de proponerlo"
        )
        return {**salida, "estado": "varias", "difieren_en": ["cajas o unidades"]}
    if unico.stock < cantidad:
        # `requested` y `available` como en `stock_insuficiente`: es el único caso en que el número llega al LLM
        falta = {"sku": unico.sku, "requested": cantidad, "available": unico.stock}
        return {**salida, "estado": "sin_stock", "falta": falta}
    return {**salida, "estado": "unico"}


def buscar_renglones(
    renglones: list[dict[str, Any]], preferidos: frozenset[str] = frozenset()
) -> list[dict[str, Any]]:
    """Busca un lote de renglones de un pedido y devuelve un resultado compacto por cada uno (RF-66).

    Cada renglón trae `n`, `texto`, `cantidad` y, si tiene, la `marca` de su encabezado. El tono de una coloración
    no entra a la búsqueda (RF-69): el catálogo no lo distingue, queda en la nota de la línea.
    Estados: `unico` (un producto y alcanza el stock), `varias`, `aproximado`, `sin_stock` y `no_encontrado`.
    """
    productos = inventory_service.get_catalog()
    return [
        _renglon(r["n"], r["texto"], r.get("marca"), r.get("cantidad", 1), productos, preferidos, r.get("unidad"))
        for r in renglones
    ]

def _normalizar(texto: str | None) -> str:
    """Sin tildes ni signos: «L'Oréal» y «LOREAL» quedan iguales, y «Anti-Frizz» como «anti frizz»."""
    if not texto:
        return ""
    sin_tildes = "".join(c for c in unicodedata.normalize("NFD", texto.lower()) if unicodedata.category(c) != "Mn")
    # El apóstrofo es parte de la palabra; el resto de los signos separan palabras
    return " ".join(re.sub(r"[\W_]", " ", re.sub(r"['’`´]", "", sin_tildes)).split())


def _parecidas(buscado: str, opciones: set[str], tope: int = 3) -> list[str]:
    """Las opciones del catálogo que más se parecen a lo que escribió el cliente («fidelit» → FIDELITE)."""
    clave = _normalizar(buscado)
    puntuadas = []
    for opcion in opciones:
        normal = _normalizar(opcion)
        if clave in normal:
            valor = 1.0
        else:
            valor = max((busqueda_service.parecido(clave, palabra) for palabra in normal.split()), default=0.0)
        if valor >= busqueda_service.MIN_PALABRA_SUGERENCIA:
            puntuadas.append((-valor, opcion))
    return [opcion for _, opcion in sorted(puntuadas)[:tope]]


def _hint_sin_resultados(que: str, buscado: str, opciones: set[str]) -> str:
    parecidas = _parecidas(buscado, opciones)
    if parecidas:
        return (
            f"No hay {que} «{buscado}». Las más parecidas del catálogo: {', '.join(parecidas)}. Si es una de esas, "
            "volvé a llamar con ese nombre; si no, avisale al cliente que no la trabajás."
        )
    return f"No hay {que} «{buscado}» ni otra parecida en el catálogo: avisale al cliente que no la trabajás."


def _marcas_con(products: list[Product], c_norm: str, tope: int = 5) -> str:
    """Las marcas que tienen productos de una categoría, con un tope para no llenar el contexto."""
    marcas = sorted({p.brand for p in products if p.brand and c_norm in _normalizar(p.category or "")})
    resto = f" y {len(marcas) - tope} más" if len(marcas) > tope else ""
    return ", ".join(marcas[:tope]) + resto


def explorar_catalogo(
    brand: str | None = None,
    category: str | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    """Explora marcas, categorías y productos cuando el cliente consulta opciones abiertas (RF-35)."""
    products = inventory_service.get_catalog()
    b_norm = _normalizar(brand) if brand else None
    c_norm = _normalizar(category) if category else None

    # Caso 1: Solo categoría (ej. ¿qué marcas de shampoo tenés?)
    if c_norm and not b_norm:
        matched = [p for p in products if c_norm in _normalizar(p.category or "")]
        marcas = sorted({p.brand for p in matched if p.brand})
        cat_nombre = matched[0].category if matched else category
        salida = {
            "tipo_consulta": "marcas_por_categoria",
            "categoria": cat_nombre,
            "total_marcas": len(marcas),
            "marcas": marcas,
            "total_productos": len(matched),
        }
        if not matched:
            salida["_hint"] = _hint_sin_resultados("categoría", category, {p.category for p in products if p.category})
        return salida

    # Caso 2: Solo marca (ej. ¿qué tenés de Fidelité?)
    if b_norm and not c_norm:
        matched = [p for p in products if b_norm in _normalizar(p.brand or "")]
        if not matched:
            return {
                "tipo_consulta": "marca_no_encontrada",
                "marca": brand,
                "total_productos": 0,
                "mensaje": f"No se encontraron productos de la marca '{brand}'.",
                "_hint": _hint_sin_resultados("marca", brand, {p.brand for p in products if p.brand}),
            }
        cats = Counter(p.category or "Otros" for p in matched)
        marca_real = matched[0].brand or brand
        return {
            "tipo_consulta": "categorias_por_marca",
            "marca": marca_real,
            "total_productos": len(matched),
            "categorias": dict(cats.most_common()),
        }

    # Caso 3: Marca y categoría (ej. mostrame shampoos de Fidelité)
    if b_norm and c_norm:
        matched = [
            p for p in products
            if b_norm in _normalizar(p.brand or "") and c_norm in _normalizar(p.category or "")
        ]
        salida = {
            "tipo_consulta": "productos_por_marca_y_categoria",
            "marca": brand,
            "categoria": category,
            "total_productos": len(matched),
            "muestra_productos": [
                {
                    "sku": p.sku,
                    "nombre": p.name,
                    "presentacion": p.presentation,
                    "precio": str(p.price) if p.price is not None else None,
                    "hay_stock": p.stock > 0,
                }
                for p in matched[:limit]
            ],
        }
        if len(matched) > limit:
            salida["_hint"] = (
                f"Se muestran {limit} de {len(matched)} productos: si busca uno puntual, buscalo con search_products."
            )
        if not matched:
            de_la_marca = {p.category for p in products if p.category and b_norm in _normalizar(p.brand or "")}
            de_esa_marca = (
                f"De esa marca hay: {', '.join(sorted(de_la_marca))}."
                if de_la_marca
                else _hint_sin_resultados("marca", brand, {p.brand for p in products if p.brand})
            )
            salida["_hint"] = f"No hay productos de «{brand}» en «{category}». {de_esa_marca}"
            if de_la_marca and (otras := _marcas_con(products, c_norm)):
                # Para ofrecerle otra marca sin una vuelta más (system prompt, «Proactividad comercial»)
                salida["_hint"] += f" Tienen esa categoría: {otras}. Ofrecele alguna."
        return salida

    # Caso 4: General (sin filtros)
    cats = Counter(p.category or "Otros" for p in products)
    return {
        "tipo_consulta": "categorias_generales",
        "total_productos": len(products),
        "categorias": dict(cats.most_common()),
    }

