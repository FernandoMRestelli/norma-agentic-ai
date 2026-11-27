"""Corre y puntúa el benchmark de pedidos escritos a mano (docs/spec/benchmark-manuscritos.md, §3.3 y §3.4).

    python -m internal.evaluar_manuscritos correr --corrida 2026-12-02-qwen \
        --modelo huggingface:Qwen/Qwen3-VL-2B-Instruct
    python -m internal.evaluar_manuscritos correr --corrida 2026-12-02-luna --modelo openai:gpt-6-luna --max-llamadas 20
    python -m internal.evaluar_manuscritos puntuar 2026-12-02-luna --precio openai:gpt-6-luna=0.10,0.50

`correr` pasa cada foto de casos.json por cada modelo y guarda lo que devolvió en
`data/benchmark_manuscritos/corridas/<corrida>/<modelo>/<foto>.json`; lo que ya está guardado no se vuelve a pedir.
Un modelo de API cuesta plata: sin --max-llamadas no se llama. Por defecto corre solo el grupo `desarrollo`.

`puntuar` lee esas salidas y puntúa la lectura (nivel a) y la lectura + búsqueda contra catalogo_evaluacion.csv
(nivel b), sin llamar a ningún modelo. Escribe `resumen.json` en la carpeta de la corrida y muestra una tabla.
"""

import argparse
import contextlib
import hashlib
import json
import math
import random
import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from internal.benchmark_manuscritos import CASOS, CATALOGO
from internal.schemas.vision_schema import ImagenLeida, RenglonLeido, renglones_desde_lectura
from internal.services import vision_service

GRUPOS = ("desarrollo", "reservado")
NIVELES = ("a", "b")

# La scoring del diseño, §6.4: una pregunta al cliente cuesta 0,2 y todo error silencioso cuesta más
DUDA = 0.8
COSTO_PREGUNTA = 1 - DUDA
PENALIZACION = {"ok": 0.0, "cerca": 0.4, "lejos": 0.6, "variante": 0.3}
PISO = 0.1
MAX_GRAVES_PCT = 2.0

Lector = Callable[[bytes, str, str], vision_service.Lectura]
Buscador = Callable[[list[dict]], list[dict]]


# --- Catálogo y huella ---


@contextlib.contextmanager
def catalogo_de_evaluacion(ruta: Path = CATALOGO) -> Iterator[list]:
    """`inventory_service.get_catalog` devuelve catalogo_evaluacion.csv con stock en todo, como en `evaluar_busqueda`.

    Lo usan la búsqueda del nivel (b) y, con `VISION_MARCAS`, las marcas que se suman al prompt de visión: así el
    benchmark no lee Sheets ni la base, y las marcas son las del catálogo de las listas.
    """
    from internal.evaluar_busqueda import leer_catalogo
    from internal.services import inventory_service

    productos = leer_catalogo(ruta)
    original = inventory_service.get_catalog
    inventory_service.get_catalog = lambda *_, **__: productos
    try:
        yield productos
    finally:
        inventory_service.get_catalog = original


def huella() -> str:
    """Lo que se le manda al modelo además de la foto (RF-135): si cambia, las salidas ya no son comparables."""
    with catalogo_de_evaluacion():
        texto = (
            f"{vision_service.instrucciones()}\n--\nlado máximo: {vision_service.MAX_LADO_PX}\n"
            f"tokens del modelo local: {vision_service.MAX_TOKENS_ENTRADA} de entrada, "
            f"{vision_service.MAX_TOKENS_SALIDA} de salida"
        )
    return hashlib.sha256(texto.encode()).hexdigest()


def nombre_de_carpeta(proveedor: str, modelo: str) -> str:
    return f"{proveedor}:{modelo}".replace(":", "_").replace("/", "_")


def leer_salidas(carpeta: Path) -> dict[str, dict]:
    return {ruta.stem: json.loads(ruta.read_text(encoding="utf-8")) for ruta in sorted(carpeta.glob("*.json"))}


def _sin_respuesta(salida: dict) -> bool:
    """El modelo no contestó (cuota, timeout, red): no es un resultado suyo, se pide otra vez con --reintentar."""
    return salida["resultado"] is None and not salida.get("crudo")


# --- Corrida (RF-133 a RF-136) ---


@dataclass
class Informe:
    por_modelo: dict[str, dict[str, int]] = field(default_factory=dict)
    previstas: int = 0
    llamadas: int = 0
    cortada: bool = False
    problema: str | None = None
    avisos: list[str] = field(default_factory=list)


def _del_grupo(casos: list[dict], grupo: str, solo: str | None) -> list[dict]:
    if solo:
        return [caso for caso in casos if caso["id"] == solo]
    return [caso for caso in casos if grupo == "todos" or caso["grupo"] == grupo]


def _guardar(ruta: Path, datos: dict) -> None:
    """Escribe y renombra: una corrida cortada a la mitad no deja un JSON a medias."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_suffix(".tmp")
    temporal.write_text(json.dumps(datos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporal.replace(ruta)


def correr(
    casos: list[dict],
    carpeta_casos: Path,
    carpeta_corrida: Path,
    modelos: list[tuple[str, str]],
    *,
    grupo: str = "desarrollo",
    max_llamadas: int | None = None,
    reintentar: bool = False,
    solo: str | None = None,
    leer: Lector | None = None,
) -> Informe:
    """Pasa cada foto por cada modelo y guarda cada salida en cuanto vuelve. `leer` reemplaza a `leer_imagen`."""
    leer = leer or vision_service.leer_imagen
    elegidos = _del_grupo(casos, grupo, solo)
    huella_actual = huella()
    informe = Informe()

    pendientes: dict[tuple[str, str], list[dict]] = {}
    for proveedor, modelo in modelos:
        carpeta = carpeta_corrida / nombre_de_carpeta(proveedor, modelo)
        salidas = leer_salidas(carpeta)
        if any(salida["huella"] != huella_actual for salida in salidas.values()):
            informe.problema = (
                f"{proveedor}:{modelo} ya tiene salidas en {carpeta_corrida.name} con otro prompt de visión "
                "(INSTRUCCIONES, VISION_MARCAS o el tamaño de la imagen): usá otra --corrida para no mezclarlas"
            )
            return informe
        faltan = [
            caso
            for caso in elegidos
            if caso["id"] not in salidas or (reintentar and salidas[caso["id"]]["resultado"] is None)
        ]
        pendientes[(proveedor, modelo)] = faltan
        informe.por_modelo[f"{proveedor}:{modelo}"] = {
            "nuevas": 0,
            "salteadas": len(elegidos) - len(faltan),
            "fallidas": 0,
        }

    informe.previstas = sum(len(faltan) for faltan in pendientes.values())
    de_api = [
        f"{proveedor}:{modelo}"
        for (proveedor, modelo), faltan in pendientes.items()
        if proveedor in vision_service.PROVEEDORES_API and faltan
    ]
    if de_api and max_llamadas is None:
        informe.problema = (
            f"Serían {informe.previstas} llamadas, con modelos de API que cuestan plata ({', '.join(de_api)}): "
            "pasá --max-llamadas N con el permiso de quien paga la clave"
        )
        return informe

    for (proveedor, modelo), faltan in pendientes.items():
        cuenta = informe.por_modelo[f"{proveedor}:{modelo}"]
        carpeta = carpeta_corrida / nombre_de_carpeta(proveedor, modelo)
        for caso in faltan:
            if max_llamadas is not None and informe.llamadas >= max_llamadas:
                informe.cortada = True
                break
            imagen = (carpeta_casos / caso["archivo"]).read_bytes()
            informe.llamadas += 1
            try:
                lectura = leer(imagen, proveedor, modelo)
            except ImportError as exc:  # el modelo local sin requirements-vision-local.txt: no es culpa de la foto
                informe.avisos.append(f"{proveedor}:{modelo} no se pudo cargar ({exc}); sigo con los demás")
                break
            _guardar(
                carpeta / f"{caso['id']}.json",
                {
                    "foto": caso["id"],
                    "proveedor": proveedor,
                    "modelo": modelo,
                    "huella": huella_actual,
                    "fecha": datetime.now().isoformat(timespec="seconds"),
                    "segundos": round(lectura.segundos, 3),
                    "tokens_entrada": lectura.tokens_entrada,
                    "tokens_salida": lectura.tokens_salida,
                    "error": lectura.error,
                    "crudo": lectura.crudo,
                    "resultado": lectura.resultado.model_dump(mode="json") if lectura.resultado else None,
                },
            )
            cuenta["fallidas" if lectura.resultado is None else "nuevas"] += 1
    return informe


# --- Puntaje de una foto (RF-137 a RF-142) ---


def normalizar(texto: str | None) -> str:
    """Minúsculas, sin tildes y con los signos como espacios: «Térmico (7)» → «termico 7»."""
    sin_tildes = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^\w]+", " ", sin_tildes).split())


def _a_lo_sumo_una_letra(a: str, b: str) -> bool:
    """Distancia de edición ≤ 1 (cambiar, sacar o agregar una letra)."""
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a
    for i, (x, y) in enumerate(zip(a, b, strict=False)):
        if x != y:
            return a[i + 1 :] == b[i + 1 :] if len(a) == len(b) else a[i:] == b[i + 1 :]
    return True


def marcas_del_catalogo(ruta: Path = CATALOGO) -> frozenset[str]:
    from internal.evaluar_busqueda import leer_catalogo

    return frozenset(normalizar(p.brand) for p in leer_catalogo(ruta) if p.brand)


def _tiene_numero(palabra: str) -> bool:
    return any(caracter.isdigit() for caracter in palabra)


def _tiene_palabras(textos: list[str | None], palabras: list[str], marcas: frozenset[str]) -> bool:
    """Todas las palabras están en los textos: tal cual, sin espacios («cocacola» en «Coca-Cola») o, si son marca del
    catálogo, con una letra de diferencia (RF-137)."""
    junto = " ".join(normalizar(texto) for texto in textos if texto)
    pegado = junto.replace(" ", "")
    tokens = junto.split()
    for palabra in map(normalizar, palabras):
        if palabra in junto or palabra.replace(" ", "") in pegado:
            continue
        if palabra in marcas and any(_a_lo_sumo_una_letra(palabra, token) for token in tokens):
            continue
        return False
    return True


def _tono(tono: str | None) -> str | None:
    return tono.strip().replace(",", ".") if tono and tono.strip() else None


@dataclass
class Leido:
    """Un renglón de la salida, del nivel que sea: lo que hace falta para emparejarlo y puntuarlo."""

    texto: str
    textos: list[str | None]  # donde se buscan las palabras
    cantidad: float | None
    tono: str | None
    unidad: str | None
    duda: bool
    skus: frozenset[str] = frozenset()
    candidatos: tuple[str, ...] = ()  # nivel (b): los SKU de la búsqueda, en su orden
    estado: str | None = None  # nivel (b): el estado de la búsqueda


@dataclass
class Par:
    esperado: dict
    leido: Leido
    cantidad: str  # "ok", "cerca" o "lejos"
    variante_ok: bool
    duda: bool
    s: float

    @property
    def cantidad_ok(self) -> bool:
        return self.cantidad == "ok"

    @property
    def silencioso(self) -> bool:
        """Salió mal sin duda: el agente no lo va a preguntar."""
        return not self.duda and not (self.cantidad_ok and self.variante_ok)

    @property
    def grave(self) -> bool:
        return not self.duda and self.cantidad == "lejos"


@dataclass
class Sobrante:
    leido: Leido
    tachado: bool

    @property
    def costo(self) -> float:
        """1 si se mandaría (silencioso o tachado), 0,2 si el agente lo pregunta antes (RF-142)."""
        return 1.0 if self.tachado or not self.leido.duda else COSTO_PREGUNTA


@dataclass
class Foto:
    caso: dict
    json_valido: bool
    pares: list[Par] = field(default_factory=list)
    omitidos: list[dict] = field(default_factory=list)
    sobrantes: list[Sobrante] = field(default_factory=list)

    @property
    def numerador(self) -> float:
        return sum(par.s for par in self.pares)

    @property
    def esperados(self) -> int:
        return len(self.pares) + len(self.omitidos)

    @property
    def denominador(self) -> float:
        return round(self.esperados + sum(sobrante.costo for sobrante in self.sobrantes), 6)

    @property
    def S(self) -> float:  # noqa: N802 - es el nombre del puntaje en el diseño
        return self.numerador / self.denominador if self.denominador else 0.0

    @property
    def correctos(self) -> int:
        return sum(1 for par in self.pares if par.cantidad_ok)

    @property
    def leidos(self) -> int:
        return len(self.pares) + len(self.sobrantes)

    @property
    def tachados_pedidos(self) -> int:
        return sum(1 for sobrante in self.sobrantes if sobrante.tachado)

    @property
    def sobrantes_que_se_mandan(self) -> int:
        return sum(1 for sobrante in self.sobrantes if sobrante.costo == 1)

    @property
    def errores_silenciosos(self) -> int:
        return sum(1 for par in self.pares if par.silencioso) + self.sobrantes_que_se_mandan

    @property
    def errores_graves(self) -> int:
        return sum(1 for par in self.pares if par.grave) + self.sobrantes_que_se_mandan

    @property
    def perfecta(self) -> bool:
        return not self.omitidos and not self.sobrantes and all(par.s == 1 for par in self.pares)


def _cantidad(renglon: RenglonLeido) -> float | None:
    """La del renglón o, con variantes, la suma de las suyas (como `comparar_vision`)."""
    if renglon.cantidad is not None or not renglon.variantes:
        return renglon.cantidad
    cantidades = [variante.cantidad for variante in renglon.variantes]
    return None if None in cantidades else sum(cantidades)


def leidos_nivel_a(resultado: ImagenLeida) -> list[Leido]:
    return [
        Leido(
            texto=renglon.texto,
            textos=[renglon.texto, renglon.producto, renglon.marca, renglon.presentacion],
            cantidad=_cantidad(renglon),
            tono=next((_tono(v.tono) for v in renglon.variantes if _tono(v.tono)), None),
            unidad=renglon.unidad,
            duda=bool(renglon.duda or any(variante.duda for variante in renglon.variantes)),
        )
        for renglon in resultado.renglones
    ]


def buscar_en_catalogo(renglones: list[dict]) -> list[dict]:
    from internal.tools import catalog_tools

    with catalogo_de_evaluacion():
        return catalog_tools.buscar_renglones(renglones)


def leidos_nivel_b(resultado: ImagenLeida, buscar: Buscador | None = None) -> list[Leido]:
    """Los renglones de `renglones_desde_lectura`, buscados como en la precarga (RF-138)."""
    renglones = renglones_desde_lectura(resultado)
    if not renglones:
        return []
    consultas = []
    for renglon in renglones:
        buscado = renglon.get("buscar") or {}
        cantidad = buscado.get("cantidad")
        consulta = {"n": renglon["n"], "texto": buscado.get("texto") or renglon["texto"], "cantidad": cantidad or 1}
        if renglon.get("marca"):
            consulta["marca"] = renglon["marca"]
        if renglon.get("unidad"):
            consulta["unidad"] = renglon["unidad"]
        consultas.append(consulta)
    encontrados = {r["n"]: r for r in (buscar or buscar_en_catalogo)(consultas)}

    leidos = []
    for renglon, consulta in zip(renglones, consultas, strict=True):
        buscado = renglon.get("buscar") or {}
        encontrado = encontrados.get(renglon["n"], {"estado": "no_encontrado", "candidatos": []})
        candidatos = tuple(c["sku"] for c in encontrado.get("candidatos", []))
        estado = encontrado["estado"]
        if estado in ("unico", "sin_stock"):
            skus = frozenset(candidatos[:1])
        elif estado in ("varias", "aproximado"):
            skus = frozenset(candidatos)
        else:
            skus = frozenset()
        leidos.append(
            Leido(
                texto=consulta["texto"],
                textos=[renglon["texto"], consulta["texto"], renglon.get("marca")],
                cantidad=buscado.get("cantidad"),
                tono=_tono(buscado.get("tono")),
                unidad=renglon.get("unidad"),
                # el agente pregunta si la lectura duda, si la búsqueda no da un solo producto o si falta la cantidad
                duda=bool(renglon.get("duda")) or estado not in ("unico", "sin_stock") or not buscado.get("cantidad"),
                skus=skus,
                candidatos=candidatos,
                estado=estado,
            )
        )
    return leidos


def _clase_de_unidad(unidad: str | None) -> str:
    return "cajas" if unidad == "cajas" else "sueltas"


def comparar_cantidad(esperado: dict, leido: Leido) -> str:
    """`ok`, `cerca` o `lejos` (RF-141): lejos es el doble o más, o cajas por unidades."""
    if _clase_de_unidad(esperado.get("unidad")) != _clase_de_unidad(leido.unidad):
        return "lejos"
    pedida, leida = float(esperado["cantidad"]), float(leido.cantidad or 0)
    if pedida == leida:
        return "ok"
    menor, mayor = sorted((pedida, leida))
    return "lejos" if menor <= 0 or mayor >= 2 * menor else "cerca"


def _puntaje(esperado: dict, leido: Leido, medida_ok: bool = True) -> Par:
    """0,8 con duda o sin cantidad; si no, 1 menos las penalizaciones, con piso 0,1 (RF-139)."""
    cantidad = comparar_cantidad(esperado, leido) if leido.cantidad is not None else "lejos"
    variante_ok = medida_ok and (esperado.get("tono") is None or _tono(esperado["tono"]) == leido.tono)
    duda = leido.duda or leido.cantidad is None
    if duda:
        s = DUDA
    else:
        penalizacion = PENALIZACION[cantidad] + (0 if variante_ok else PENALIZACION["variante"])
        s = max(PISO, 1 - penalizacion)
    return Par(esperado, leido, cantidad, variante_ok, duda, round(s, 6))


def puntuar_foto(
    caso: dict,
    resultado: ImagenLeida | None,
    nivel: str,
    *,
    marcas: frozenset[str] | None = None,
    buscar: Buscador | None = None,
) -> Foto:
    """Empareja los esperados con lo leído, uno a uno y en el orden de la hoja, y puntúa cada par (RF-137 a RF-142)."""
    esperados = sorted(caso["renglones"], key=lambda renglon: renglon["n"])
    if resultado is None or not resultado.legible:
        return Foto(caso, json_valido=resultado is not None, omitidos=esperados)
    marcas = marcas_del_catalogo() if marcas is None else marcas
    leidos = leidos_nivel_a(resultado) if nivel == "a" else leidos_nivel_b(resultado, buscar)

    foto = Foto(caso, json_valido=True)
    libres = list(leidos)
    for esperado in esperados:
        if nivel == "a":
            sin_numeros = [p for p in esperado["palabras"] if not _tiene_numero(p)]
            con_numeros = [p for p in esperado["palabras"] if _tiene_numero(p)]
            candidatos = [leido for leido in libres if _tiene_palabras(leido.textos, sin_numeros, marcas)]
            completos = [leido for leido in candidatos if _tiene_palabras(leido.textos, con_numeros, marcas)]
            leido = (completos or candidatos or [None])[0]
            medida_ok = leido is not None and leido in completos
        else:
            leido = next((leido for leido in libres if esperado["sku"] in leido.skus), None)
            medida_ok = True  # la medida está en el SKU
        if leido is None:
            foto.omitidos.append(esperado)
            continue
        libres.remove(leido)
        foto.pares.append(_puntaje(esperado, leido, medida_ok))
    tachados = caso.get("tachados", [])
    foto.sobrantes = [
        Sobrante(leido, any(_tiene_palabras(leido.textos, tachado, frozenset()) for tachado in tachados))
        for leido in libres
    ]
    return foto


# --- Resumen (RF-140) ---


def _percentil(valores: list[float], q: float) -> float | None:
    if not valores:
        return None
    ordenados = sorted(valores)
    return round(ordenados[max(math.ceil(q * len(ordenados)) - 1, 0)], 2)


def _proporcion(num: float, den: float) -> float | None:
    return round(num / den, 3) if den else None


def _usd(salida: dict, precio: tuple[float, float] | None) -> float | None:
    if salida["proveedor"] not in vision_service.PROVEEDORES_API:
        return 0.0
    if precio is None or salida.get("tokens_entrada") is None or salida.get("tokens_salida") is None:
        return None
    return (salida["tokens_entrada"] * precio[0] + salida["tokens_salida"] * precio[1]) / 1_000_000


def _cortes(fotos: list[Foto], clave: Callable[[dict], list[str]]) -> dict[str, float]:
    """S por tipo de dificultad u origen: los pares y omitidos de ese tipo, sin los sobrantes."""
    acumulado: dict[str, list[float]] = {}
    for foto in fotos:
        filas = [(par.esperado, par.s) for par in foto.pares] + [(esperado, 0.0) for esperado in foto.omitidos]
        for esperado, s in filas:
            for valor in clave(esperado):
                suma = acumulado.setdefault(valor, [0.0, 0])
                suma[0] += s
                suma[1] += 1
    return {valor: round(num / den, 3) for valor, (num, den) in sorted(acumulado.items())}


def _por_foto(fotos: list[Foto], clave: Callable[[dict], str | None]) -> dict[str, float]:
    """S micro de las fotos de cada valor: persona, letra, nitidez, luz o papel."""
    acumulado: dict[str, list[float]] = {}
    for foto in fotos:
        suma = acumulado.setdefault(clave(foto.caso) or "sin dato", [0.0, 0])
        suma[0] += foto.numerador
        suma[1] += foto.denominador
    return {valor: round(num / den, 3) for valor, (num, den) in sorted(acumulado.items()) if den}


def _condicion(nombre: str) -> Callable[[dict], str | None]:
    return lambda caso: caso.get("condiciones", {}).get(nombre)


def peores_renglones(fotos: list[Foto], cuantos: int = 10, minimo: int = 2) -> list[dict]:
    """Los renglones del pool con menor s promedio entre las fotos en que aparecieron (un omitido vale 0)."""
    por_id: dict[str, dict] = {}
    for foto in fotos:
        filas = [(par.esperado, par.s) for par in foto.pares] + [(esperado, 0.0) for esperado in foto.omitidos]
        for esperado, s in filas:
            fila = por_id.setdefault(esperado.get("id") or esperado["texto"], {"texto": esperado["texto"], "s": []})
            fila["s"].append(s)
    peores = [
        {"id": id_, "texto": fila["texto"], "s": round(sum(fila["s"]) / len(fila["s"]), 3),
         "apariciones": len(fila["s"])}
        for id_, fila in por_id.items()
        if len(fila["s"]) >= minimo
    ]  # fmt: skip
    return sorted(peores, key=lambda fila: (fila["s"], fila["id"]))[:cuantos]


def elegibilidad(tachados_pedidos: int, errores_graves: int, esperados: int) -> tuple[bool, str | None]:
    """Si la configuración se puede elegir (RF-143): sin tachados pedidos y con hasta 2 % de errores graves."""
    motivos = []
    if tachados_pedidos:
        motivos.append(f"pide {tachados_pedidos} {'tachado' if tachados_pedidos == 1 else 'tachados'}")
    pct = 100 * errores_graves / esperados if esperados else 0.0
    if pct > MAX_GRAVES_PCT:
        motivos.append(f"errores graves en {pct:.1f} % de los renglones (máx. {MAX_GRAVES_PCT:g} %)")
    return not motivos, "; ".join(motivos) or None


def resumir(fotos: list[Foto], salidas: list[dict], ilegibles: list[bool], precio: tuple[float, float] | None) -> dict:
    """Las métricas de un modelo, un nivel y un grupo."""
    from internal.evaluar_agente import intervalo_por_casos

    numerador = sum(foto.numerador for foto in fotos)
    denominador = sum(foto.denominador for foto in fotos)
    correctos = sum(foto.correctos for foto in fotos)
    leidos = sum(foto.leidos for foto in fotos)
    esperados = sum(len(foto.pares) + len(foto.omitidos) for foto in fotos)
    filas = [
        {"caso": foto.caso["id"], "resultado": "ok", "num": foto.numerador, "den": foto.denominador} for foto in fotos
    ]
    bajo, alto = intervalo_por_casos(filas, "num", "den")
    precision, recall = _proporcion(correctos, leidos), _proporcion(correctos, esperados)
    f1 = round(2 * precision * recall / (precision + recall), 3) if precision and recall else 0.0
    costos = [_usd(salida, precio) for salida in salidas]
    usd_total = None if None in costos else sum(costos)
    usd_por_foto = round(usd_total / len(salidas), 6) if usd_total is not None and salidas else None
    usd_por_1000 = round(1000 * usd_total / esperados, 4) if usd_total is not None and esperados else None
    tachados = sum(foto.tachados_pedidos for foto in fotos)
    graves = sum(foto.errores_graves for foto in fotos)
    elegible, motivo = elegibilidad(tachados, graves, esperados)
    peores_hojas = sorted(({"foto": foto.caso["id"], "S": round(foto.S, 3)} for foto in fotos),
                          key=lambda hoja: (hoja["S"], hoja["foto"]))[:5]  # fmt: skip
    return {
        "fotos": len(fotos),
        "S": _proporcion(numerador, denominador),
        "S_intervalo": [round(bajo / 100, 3), round(alto / 100, 3)],
        "fotos_perfectas_pct": round(100 * sum(foto.perfecta for foto in fotos) / len(fotos), 1) if fotos else None,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "S_por_hoja": round(sum(foto.S for foto in fotos) / len(fotos), 3) if fotos else None,
        "elegible": elegible,
        "motivo": motivo,
        "errores_graves": graves,
        "errores_graves_pct": round(100 * graves / esperados, 1) if esperados else None,
        "errores_silenciosos": sum(foto.errores_silenciosos for foto in fotos),
        "tachados_pedidos": tachados,
        "peores_hojas": peores_hojas,
        "peores_renglones": peores_renglones(fotos),
        "json_valido_pct": round(100 * sum(foto.json_valido for foto in fotos) / len(fotos), 1) if fotos else None,
        "ilegibles": {"fotos": len(ilegibles), "marcadas_ilegibles": sum(ilegibles)},
        "segundos_p50": _percentil([salida["segundos"] for salida in salidas], 0.5),
        "segundos_p95": _percentil([salida["segundos"] for salida in salidas], 0.95),
        "usd_por_foto": usd_por_foto,
        "usd_por_1000_renglones": usd_por_1000,
        "por_dificultad": _cortes(fotos, lambda esperado: esperado.get("dificultad", [])),
        "por_origen": _cortes(fotos, lambda esperado: [esperado.get("origen") or "sin dato"]),
        "por_persona": _por_foto(fotos, lambda caso: caso.get("persona")),
        "por_letra": _por_foto(fotos, _condicion("letra")),
        "por_nitidez": _por_foto(fotos, _condicion("nitidez")),
        "por_luz": _por_foto(fotos, _condicion("luz")),
        "por_papel": _por_foto(fotos, _condicion("papel")),
    }


@dataclass
class Puntuado:
    modelo: str
    huella: str | None
    fotos: dict[str, dict[str, list[Foto]]]  # nivel → grupo → fotos
    resumen: dict


def puntuar_modelo(
    salidas: dict[str, dict],
    casos: list[dict],
    precio: tuple[float, float] | None,
    *,
    marcas: frozenset[str] | None = None,
    buscar: Buscador | None = None,
) -> tuple[dict, dict[str, dict[str, list[Foto]]], list[str]]:
    por_id = {caso["id"]: caso for caso in casos}
    avisos = []
    if sobran := sorted(set(salidas) - set(por_id)):
        avisos.append(f"{len(sobran)} salidas de fotos que ya no están en casos.json: {', '.join(sobran)}")
    if sin_respuesta := sorted(foto for foto, salida in salidas.items() if _sin_respuesta(salida)):
        avisos.append(f"{len(sin_respuesta)} fotos sin respuesta del modelo (correr --reintentar): "
                      + ", ".join(sin_respuesta))  # fmt: skip
    marcas = marcas_del_catalogo() if marcas is None else marcas
    niveles: dict[str, dict] = {}
    fotos_por_nivel: dict[str, dict[str, list[Foto]]] = {}
    for nivel in NIVELES:
        niveles[nivel], fotos_por_nivel[nivel] = {}, {}
        for grupo in GRUPOS:
            usadas = [
                (por_id[foto], salida)
                for foto, salida in sorted(salidas.items())
                if foto in por_id and por_id[foto]["grupo"] == grupo and not _sin_respuesta(salida)
            ]
            if not usadas:
                niveles[nivel][grupo] = {}
                continue
            fotos, ilegibles = [], []
            for caso, salida in usadas:
                resultado = ImagenLeida.model_validate(salida["resultado"]) if salida["resultado"] else None
                if not caso.get("legible", True):
                    ilegibles.append(resultado is not None and not resultado.legible)
                    continue
                fotos.append(puntuar_foto(caso, resultado, nivel, marcas=marcas, buscar=buscar))
            niveles[nivel][grupo] = resumir(fotos, [salida for _, salida in usadas], ilegibles, precio)
            fotos_por_nivel[nivel][grupo] = fotos
    return niveles, fotos_por_nivel, avisos


def puntuar_corrida(
    carpeta_corrida: Path,
    casos: list[dict],
    precios: dict[str, tuple[float, float]] | None = None,
    *,
    buscar: Buscador | None = None,
    detalle: list[Puntuado] | None = None,
) -> dict:
    """El resumen de todos los modelos de una corrida, sin llamar a ninguno (RF-140)."""
    precios = precios or {}
    marcas = marcas_del_catalogo()
    resumen: dict[str, Any] = {"corrida": carpeta_corrida.name, "modelos": [], "avisos": []}
    for carpeta in sorted(p for p in carpeta_corrida.iterdir() if p.is_dir()):
        salidas = leer_salidas(carpeta)
        if not salidas:
            continue
        primera = next(iter(salidas.values()))
        modelo = f"{primera['proveedor']}:{primera['modelo']}"
        huellas = sorted({salida["huella"] for salida in salidas.values()})
        niveles, fotos, avisos = puntuar_modelo(salidas, casos, precios.get(modelo), marcas=marcas, buscar=buscar)
        if len(huellas) > 1:
            avisos.append(f"las salidas tienen {len(huellas)} prompts distintos: no se comparan entre sí")
        por_grupo = {grupo: [c["id"] for c in casos if c["grupo"] == grupo] for grupo in GRUPOS}
        for grupo, ids in por_grupo.items():
            corridas = [foto for foto in ids if foto in salidas]
            if corridas and len(corridas) < len(ids):
                avisos.append(f"{grupo}: {len(ids) - len(corridas)} de {len(ids)} fotos sin salida")
        resumen["avisos"] += [f"{modelo}: {aviso}" for aviso in avisos]
        resumen["modelos"].append({"modelo": modelo, "huella": huellas[0] if len(huellas) == 1 else None,
                                   "niveles": niveles})  # fmt: skip
        if detalle is not None:
            detalle.append(Puntuado(modelo, huellas[0], fotos, niveles))
    distintas = {m["huella"] for m in resumen["modelos"]}
    if len(distintas) > 1:
        resumen["avisos"].append(
            "los modelos de esta corrida se leyeron con prompts distintos (huellas "
            + ", ".join(sorted((h or "mezcladas")[:8] for h in distintas))
            + "): no se comparan entre sí"
        )
    return resumen


# --- Validación de la scoring (RF-145, RF-146) ---

SEMILLA_ORDENAR = 145
MIN_ACUERDO_PCT = 80.0
MAX_DIFERENCIA_INVERTIDA = 0.1


def _renglon_para_ordenar(leido: Leido, nombres: dict[str, str]) -> str:
    if not leido.candidatos:
        producto = f"«{leido.texto}» (no está en el catálogo)"
    elif len(leido.skus) == 1:
        producto = nombres.get(leido.candidatos[0], leido.candidatos[0])
    else:
        producto = " o ".join(nombres.get(sku, sku) for sku in leido.candidatos)
    cantidad = "sin cantidad" if leido.cantidad is None else f"× {leido.cantidad:g}"
    detalle = f", tono {leido.tono}" if leido.tono else ""
    detalle += f", {leido.unidad}" if leido.unidad else ""
    return f"{producto} {cantidad}{detalle}" + (" — pregunta" if leido.duda else "")


def para_ordenar(
    puntuados: list[Puntuado], cuantos: int, semilla: int = SEMILLA_ORDENAR
) -> tuple[str, dict, list[str]]:
    """El documento para ordenar a mano y su clave: pares modelo + foto de desarrollo, nivel (b), sin modelo ni S."""
    from internal.evaluar_busqueda import leer_catalogo

    nombres = {producto.sku: producto.name for producto in leer_catalogo()}
    todos = sorted(
        ((puntuado.modelo, foto) for puntuado in puntuados for foto in puntuado.fotos["b"].get("desarrollo", [])),
        key=lambda par: (par[1].caso["id"], par[0]),
    )
    rng = random.Random(semilla)
    elegidos = rng.sample(todos, min(cuantos, len(todos)))
    avisos = [f"hay {len(todos)} pares de desarrollo, menos que los {cuantos} pedidos"] if len(todos) < cuantos else []

    lineas = ["# Ordenar de mejor a peor", "",
              "Cada bloque es lo que quedó de una hoja: lo que pidió el cliente y lo que se le habría propuesto. "
              "Ordená los ids de mejor a peor y pasalos a `validar --orden`.", ""]  # fmt: skip
    clave: dict[str, Any] = {"semilla": semilla, "nivel": "b", "items": {}}
    for numero, (modelo, foto) in enumerate(elegidos, start=1):
        id_ = f"O{numero:02d}"
        clave["items"][id_] = {"modelo": modelo, "foto": foto.caso["id"], "S": round(foto.S, 4)}
        lineas += [f"## {id_}", "", "| Pidió | Se propone |", "|---|---|"]
        propuestos = {id(par.esperado): par.leido for par in foto.pares}
        for esperado in sorted(foto.caso["renglones"], key=lambda renglon: renglon["n"]):
            leido = propuestos.get(id(esperado))
            lineas.append(f"| {esperado['texto']} | {_renglon_para_ordenar(leido, nombres) if leido else '—'} |")
        for sobrante in foto.sobrantes:
            lineas.append(f"| — | {_renglon_para_ordenar(sobrante.leido, nombres)} |")
        lineas.append("")
    return "\n".join(lineas), clave, avisos


@dataclass
class Validacion:
    acuerdo_pct: float | None = None
    invertidos: list[tuple[str, str, float]] = field(default_factory=list)  # mejor según la persona, peor, ΔS
    problemas: list[str] = field(default_factory=list)

    @property
    def valida(self) -> bool:
        return (
            not self.problemas
            and self.acuerdo_pct is not None
            and self.acuerdo_pct >= MIN_ACUERDO_PCT
            and all(diferencia <= MAX_DIFERENCIA_INVERTIDA for *_, diferencia in self.invertidos)
        )


def validar_orden(clave: dict, orden: list[str]) -> Validacion:
    """Compara el orden de una persona (de mejor a peor) con el de S (RF-146)."""
    items = clave["items"]
    validacion = Validacion()
    vistos = Counter(orden)
    if repetidos := sorted(id_ for id_, veces in vistos.items() if veces > 1):
        validacion.problemas.append(f"ids repetidos: {', '.join(repetidos)}")
    if desconocidos := sorted(set(orden) - set(items)):
        validacion.problemas.append(f"ids que no están en la clave: {', '.join(desconocidos)}")
    if faltan := sorted(set(items) - set(orden)):
        validacion.problemas.append(f"faltan ids: {', '.join(faltan)}")
    if validacion.problemas:
        return validacion
    de_acuerdo = total = 0
    for i, mejor in enumerate(orden):
        for peor in orden[i + 1 :]:
            total += 1
            diferencia = items[peor]["S"] - items[mejor]["S"]
            if diferencia <= 0:
                de_acuerdo += 1
            else:
                validacion.invertidos.append((mejor, peor, round(diferencia, 4)))
    validacion.acuerdo_pct = round(100 * de_acuerdo / total, 1) if total else 100.0
    validacion.invertidos.sort(key=lambda par: -par[2])
    return validacion


# --- Línea de comandos ---


def _num(valor: float | None, formato: str, vacio: str = "-") -> str:
    return vacio if valor is None else formato.format(valor)


def _tabla(resumen: dict) -> str:
    columnas = ("modelo", "prompt", "nivel", "grupo", "fotos", "S", "intervalo", "elegible", "graves", "silenciosos",
                "tachados", "perfectas", "F1", "JSON", "p50 s", "USD/foto")  # fmt: skip
    filas = [columnas]
    for modelo in resumen["modelos"]:
        for nivel, grupos in modelo["niveles"].items():
            for grupo, m in grupos.items():
                if not m:
                    continue
                filas.append((
                    modelo["modelo"], (modelo["huella"] or "mezcladas")[:8], nivel, grupo, str(m["fotos"]),
                    _num(m["S"], "{:.3f}"), f"[{m['S_intervalo'][0]:.2f}, {m['S_intervalo'][1]:.2f}]",
                    "sí" if m["elegible"] else "no", _num(m["errores_graves_pct"], "{:.1f} %"),
                    str(m["errores_silenciosos"]), str(m["tachados_pedidos"]),
                    _num(m["fotos_perfectas_pct"], "{:.0f} %"), _num(m["f1"], "{:.2f}"),
                    _num(m["json_valido_pct"], "{:.0f} %"), _num(m["segundos_p50"], "{}"),
                    _num(m["usd_por_foto"], "{:.4f}", "?"),
                ))  # fmt: skip
    anchos = [max(len(fila[i]) for fila in filas) for i in range(len(columnas))]
    return "\n".join("  ".join(valor.ljust(ancho) for valor, ancho in zip(fila, anchos, strict=True)) for fila in filas)


def _detalle(puntuados: list[Puntuado]) -> str:
    lineas = []
    for puntuado in puntuados:
        for nivel, grupos in puntuado.fotos.items():
            for grupo, fotos in grupos.items():
                for foto in fotos:
                    lineas.append(f"\n{puntuado.modelo} · nivel {nivel} · {grupo} · {foto.caso['id']}: "
                                  f"S = {foto.numerador:.1f} / {foto.denominador:g}")  # fmt: skip
                    for par in foto.pares:
                        marca = "duda" if par.duda else ("ok" if par.s == 1 else "mal")
                        lineas.append(f"  {par.s:.1f} {marca:<4} {par.esperado['texto']}  ←  {par.leido.texto}")
                    lineas += [f"  0.0 omit {esperado['texto']}" for esperado in foto.omitidos]
                    for sobrante in foto.sobrantes:
                        motivo = " (tachado)" if sobrante.tachado else ""
                        lineas.append(f"  +{sobrante.costo:g} sobra {sobrante.leido.texto}{motivo}")
    return "\n".join(lineas)


def _modelo(valor: str) -> tuple[str, str]:
    proveedor, _, modelo = valor.partition(":")
    if not modelo:
        raise argparse.ArgumentTypeError("tiene que ser proveedor:modelo, por ejemplo openai:gpt-6-luna")
    return proveedor, modelo


def _precio(valor: str) -> tuple[str, tuple[float, float]]:
    modelo, _, precios = valor.rpartition("=")
    entrada, _, salida = precios.partition(",")
    try:
        return modelo, (float(entrada), float(salida))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("tiene que ser proveedor:modelo=ENTRADA,SALIDA (USD por millón)") from exc


def _carpeta_de_corrida(casos: Path, corrida: str) -> Path:
    ruta = Path(corrida)
    return ruta if ruta.is_dir() and len(ruta.parts) > 1 else casos.parent / "corridas" / corrida


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    comandos = parser.add_subparsers(dest="comando", required=True)

    correr_ = comandos.add_parser("correr", help="Pasa cada foto por cada modelo y guarda lo que devolvió")
    correr_.add_argument("--corrida", required=True, help="nombre de la carpeta en corridas/ (ej. 2026-12-02-luna)")
    correr_.add_argument("--modelo", type=_modelo, action="append", required=True, help="proveedor:modelo")
    correr_.add_argument("--casos", type=Path, default=CASOS)
    correr_.add_argument("--grupo", choices=(*GRUPOS, "todos"), default="desarrollo")
    correr_.add_argument("--max-llamadas", type=int, help="tope de llamadas nuevas; obligatorio con modelos de API")
    correr_.add_argument("--reintentar", action="store_true", help="vuelve a pedir las salidas sin resultado")
    correr_.add_argument("--solo", help="corre solo esta foto (ej. p03-A)")

    puntuar_ = comandos.add_parser("puntuar", help="Puntúa las salidas guardadas de una corrida, sin llamar a nadie")
    puntuar_.add_argument("corrida", help="nombre de la carpeta en corridas/, o su ruta")
    puntuar_.add_argument("--casos", type=Path, default=CASOS)
    ayuda_precio = "proveedor:modelo=ENTRADA,SALIDA en USD por millón de tokens (se puede repetir)"
    puntuar_.add_argument("--precio", type=_precio, action="append", default=[], help=ayuda_precio)
    puntuar_.add_argument("--detalle", action="store_true", help="muestra cada renglón de cada foto")
    puntuar_.add_argument("--para-ordenar", type=int, metavar="N",
                          help="escribe ordenar.md con N pares de desarrollo para ordenar a mano")  # fmt: skip
    puntuar_.add_argument("--semilla", type=int, default=SEMILLA_ORDENAR)

    validar_ = comandos.add_parser("validar", help="Compara un orden hecho a mano con el de la scoring")
    validar_.add_argument("corrida", help="nombre de la carpeta en corridas/, o su ruta")
    validar_.add_argument("--casos", type=Path, default=CASOS)
    validar_.add_argument("--orden", required=True, help="los ids de ordenar.md de mejor a peor: O07,O02,…")

    args = parser.parse_args(argv)
    carpeta = _carpeta_de_corrida(args.casos, args.corrida)
    if args.comando == "validar":
        return _comando_validar(carpeta, args.orden)
    if not args.casos.exists():
        print(f"Falta {args.casos}: armalo con python -m internal.benchmark_manuscritos casos")
        return 1
    casos = json.loads(args.casos.read_text(encoding="utf-8"))

    if args.comando == "correr":
        informe = correr(casos, args.casos.parent, carpeta, args.modelo, grupo=args.grupo,
                         max_llamadas=args.max_llamadas, reintentar=args.reintentar, solo=args.solo)  # fmt: skip
        if informe.problema:
            print(informe.problema)
            return 1
        for modelo, cuenta in informe.por_modelo.items():
            print(f"{modelo}: {cuenta['nuevas']} nuevas, {cuenta['salteadas']} ya estaban, "
                  f"{cuenta['fallidas']} sin resultado")  # fmt: skip
        for aviso in informe.avisos:
            print(f"- {aviso}")
        if informe.cortada:
            print(f"Se cortó en {informe.llamadas} llamadas (--max-llamadas): lo que falta sigue en la próxima")
        print(f"Salidas en {carpeta}")
        return 0

    if not carpeta.is_dir():
        print(f"No existe la corrida {carpeta}")
        return 1
    puntuados: list[Puntuado] = []
    resumen = puntuar_corrida(carpeta, casos, dict(args.precio), detalle=puntuados)
    resumen["casos"] = str(args.casos)
    _guardar(carpeta / "resumen.json", resumen)
    print(_tabla(resumen))
    for aviso in resumen["avisos"]:
        print(f"- {aviso}")
    if args.detalle:
        print(_detalle(puntuados))
    if args.para_ordenar:
        documento, clave, avisos = para_ordenar(puntuados, args.para_ordenar, args.semilla)
        (carpeta / "ordenar.md").write_text(documento + "\n", encoding="utf-8")
        _guardar(carpeta / "ordenar_clave.json", clave)
        for aviso in avisos:
            print(f"- {aviso}")
        print(f"\nPara ordenar: {carpeta / 'ordenar.md'} (no abras ordenar_clave.json hasta terminar)")
    print(f"\nResumen en {carpeta / 'resumen.json'}")
    return 0


def _comando_validar(carpeta: Path, orden: str) -> int:
    ruta = carpeta / "ordenar_clave.json"
    if not ruta.exists():
        print(f"Falta {ruta}: primero corré puntuar --para-ordenar")
        return 1
    validacion = validar_orden(json.loads(ruta.read_text(encoding="utf-8")),
                               [id_.strip() for id_ in orden.split(",") if id_.strip()])  # fmt: skip
    if validacion.problemas:
        print("El orden no sirve:\n- " + "\n- ".join(validacion.problemas))
        return 1
    print(f"Pares en el mismo orden que S: {validacion.acuerdo_pct:.1f} % (mínimo {MIN_ACUERDO_PCT:g} %)")
    for mejor, peor, diferencia in validacion.invertidos:
        print(f"- {mejor} antes que {peor}, pero S dice lo contrario por {diferencia:.3f}")
    if validacion.valida:
        print("La scoring es válida: se puede usar para comparar y optimizar")
        return 0
    print(f"La scoring no es válida: revisá los pares invertidos (máximo {MAX_DIFERENCIA_INVERTIDA:g} de S)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
