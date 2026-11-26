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
import re
import unicodedata
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
        texto = f"{vision_service.instrucciones()}\n--\nlado máximo: {vision_service.MAX_LADO_PX}"
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


# --- Puntaje de una foto (RF-137 a RF-139) ---


def normalizar(texto: str | None) -> str:
    """Minúsculas, sin tildes ni signos y con espacios simples: «Térmico (7)» → «termico 7»."""
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


def _tiene_palabras(textos: list[str], palabras: list[str], marcas: frozenset[str]) -> bool:
    junto = " ".join(normalizar(texto) for texto in textos if texto)
    tokens = junto.split()
    for palabra in map(normalizar, palabras):
        if palabra in junto:
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
    textos: list[str]  # donde se buscan las palabras
    cantidad: float | None
    tono: str | None
    unidad: str | None
    duda: bool
    skus: frozenset[str] = frozenset()


@dataclass
class Par:
    esperado: dict
    leido: Leido
    cantidad_ok: bool
    detalle_ok: bool
    duda: bool
    s: float


@dataclass
class Foto:
    caso: dict
    json_valido: bool
    pares: list[Par] = field(default_factory=list)
    omitidos: list[dict] = field(default_factory=list)
    sobrantes: list[Leido] = field(default_factory=list)
    tachados_pedidos: int = 0

    @property
    def numerador(self) -> float:
        return sum(par.s for par in self.pares)

    @property
    def denominador(self) -> int:
        return len(self.pares) + len(self.omitidos) + len(self.sobrantes)

    @property
    def correctos(self) -> int:
        return sum(1 for par in self.pares if par.cantidad_ok)

    @property
    def leidos(self) -> int:
        return len(self.pares) + len(self.sobrantes)

    @property
    def errores_silenciosos(self) -> int:
        malos = sum(1 for par in self.pares if not par.duda and not (par.cantidad_ok and par.detalle_ok))
        return malos + len(self.sobrantes)

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
        candidatos = [c["sku"] for c in encontrado.get("candidatos", [])]
        estado = encontrado["estado"]
        if estado in ("unico", "sin_stock"):
            skus, duda_de_busqueda = frozenset(candidatos[:1]), False
        elif estado in ("varias", "aproximado"):
            skus, duda_de_busqueda = frozenset(candidatos), True
        else:
            skus, duda_de_busqueda = frozenset(), False
        leidos.append(
            Leido(
                texto=consulta["texto"],
                textos=[renglon["texto"], consulta["texto"], renglon.get("marca")],
                cantidad=buscado.get("cantidad"),
                tono=_tono(buscado.get("tono")),
                unidad=renglon.get("unidad"),
                # sin cantidad leída el agente la va a preguntar
                duda=bool(renglon.get("duda")) or duda_de_busqueda or buscado.get("cantidad") is None,
                skus=skus,
            )
        )
    return leidos


def _puntaje(esperado: dict, leido: Leido) -> Par:
    cantidad_ok = leido.cantidad is not None and float(leido.cantidad) == float(esperado["cantidad"])
    tono_ok = esperado.get("tono") is None or _tono(esperado["tono"]) == leido.tono
    unidad_ok = esperado.get("unidad") is None or esperado["unidad"] == leido.unidad
    detalle_ok = tono_ok and unidad_ok
    s = 0.8 if leido.duda else 0.5 + 0.4 * cantidad_ok + 0.1 * detalle_ok
    return Par(esperado, leido, cantidad_ok, detalle_ok, leido.duda, round(s, 6))


def puntuar_foto(
    caso: dict,
    resultado: ImagenLeida | None,
    nivel: str,
    *,
    marcas: frozenset[str] | None = None,
    buscar: Buscador | None = None,
) -> Foto:
    """Empareja los esperados con lo leído, uno a uno y en el orden de la hoja, y puntúa cada par (RF-139)."""
    esperados = sorted(caso["renglones"], key=lambda renglon: renglon["n"])
    if resultado is None or not resultado.legible:
        return Foto(caso, json_valido=resultado is not None, omitidos=esperados)
    if nivel == "a":
        marcas = marcas_del_catalogo() if marcas is None else marcas
        leidos = leidos_nivel_a(resultado)

        def corresponde(esperado: dict, leido: Leido) -> bool:
            return _tiene_palabras(leido.textos, esperado["palabras"], marcas)
    else:
        leidos = leidos_nivel_b(resultado, buscar)

        def corresponde(esperado: dict, leido: Leido) -> bool:
            return esperado["sku"] in leido.skus

    foto = Foto(caso, json_valido=True)
    libres = list(leidos)
    for esperado in esperados:
        leido = next((leido for leido in libres if corresponde(esperado, leido)), None)
        if leido is None:
            foto.omitidos.append(esperado)
            continue
        libres.remove(leido)
        foto.pares.append(_puntaje(esperado, leido))
    foto.sobrantes = libres
    foto.tachados_pedidos = sum(
        1
        for leido in libres
        if any(_tiene_palabras(leido.textos, tachado, frozenset()) for tachado in caso.get("tachados", []))
    )
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


def _por_condicion(fotos: list[Foto], condicion: str) -> dict[str, float]:
    acumulado: dict[str, list[float]] = {}
    for foto in fotos:
        suma = acumulado.setdefault(foto.caso["condiciones"].get(condicion) or "sin dato", [0.0, 0])
        suma[0] += foto.numerador
        suma[1] += foto.denominador
    return {valor: round(num / den, 3) for valor, (num, den) in sorted(acumulado.items()) if den}


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
    return {
        "fotos": len(fotos),
        "S": _proporcion(numerador, denominador),
        "S_intervalo": [round(bajo / 100, 3), round(alto / 100, 3)],
        "fotos_perfectas_pct": round(100 * sum(foto.perfecta for foto in fotos) / len(fotos), 1) if fotos else None,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "errores_silenciosos": sum(foto.errores_silenciosos for foto in fotos),
        "tachados_pedidos": sum(foto.tachados_pedidos for foto in fotos),
        "json_valido_pct": round(100 * sum(foto.json_valido for foto in fotos) / len(fotos), 1) if fotos else None,
        "ilegibles": {"fotos": len(ilegibles), "marcadas_ilegibles": sum(ilegibles)},
        "segundos_p50": _percentil([salida["segundos"] for salida in salidas], 0.5),
        "segundos_p95": _percentil([salida["segundos"] for salida in salidas], 0.95),
        "usd_por_foto": usd_por_foto,
        "usd_por_1000_renglones": usd_por_1000,
        "por_dificultad": _cortes(fotos, lambda esperado: esperado.get("dificultad", [])),
        "por_origen": _cortes(fotos, lambda esperado: [esperado.get("origen") or "sin dato"]),
        "por_luz": _por_condicion(fotos, "luz"),
        "por_papel": _por_condicion(fotos, "papel"),
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
    return resumen


# --- Línea de comandos ---


def _num(valor: float | None, formato: str, vacio: str = "-") -> str:
    return vacio if valor is None else formato.format(valor)


def _tabla(resumen: dict) -> str:
    columnas = ("modelo", "nivel", "grupo", "fotos", "S", "intervalo", "perfectas", "F1", "silenciosos",
                "tachados", "JSON", "p50 s", "USD/foto")  # fmt: skip
    filas = [columnas]
    for modelo in resumen["modelos"]:
        for nivel, grupos in modelo["niveles"].items():
            for grupo, m in grupos.items():
                if not m:
                    continue
                filas.append((
                    modelo["modelo"], nivel, grupo, str(m["fotos"]), _num(m["S"], "{:.3f}"),
                    f"[{m['S_intervalo'][0]:.2f}, {m['S_intervalo'][1]:.2f}]",
                    _num(m["fotos_perfectas_pct"], "{:.0f} %"), _num(m["f1"], "{:.2f}"),
                    str(m["errores_silenciosos"]), str(m["tachados_pedidos"]), _num(m["json_valido_pct"], "{:.0f} %"),
                    _num(m["segundos_p50"], "{}"), _num(m["usd_por_foto"], "{:.4f}", "?"),
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
                                  f"S = {foto.numerador:.1f} / {foto.denominador}")  # fmt: skip
                    for par in foto.pares:
                        marca = "duda" if par.duda else ("ok" if par.s == 1 else "mal")
                        lineas.append(f"  {par.s:.1f} {marca:<4} {par.esperado['texto']}  ←  {par.leido.texto}")
                    lineas += [f"  0.0 omit {esperado['texto']}" for esperado in foto.omitidos]
                    lineas += [f"  +1  sobra {leido.texto}" for leido in foto.sobrantes]
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

    args = parser.parse_args(argv)
    if not args.casos.exists():
        print(f"Falta {args.casos}: armalo con python -m internal.benchmark_manuscritos casos")
        return 1
    casos = json.loads(args.casos.read_text(encoding="utf-8"))
    carpeta = _carpeta_de_corrida(args.casos, args.corrida)

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
    print(f"\nResumen en {carpeta / 'resumen.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
