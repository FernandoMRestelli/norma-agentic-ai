"""Compara modelos de visión con las imágenes ficticias de tests/fixtures/imagenes_pedidos/casos.json.

    python -m internal.comparar_vision --modelo openai:gpt-6-luna --modelo huggingface:Qwen/Qwen3-VL-2B-Instruct

Cada --modelo es `proveedor:modelo`; sin ninguno usa LLM_VISION_PROVIDER y LLM_VISION_MODEL. Los de API usan
LLM_VISION_API_KEY. Para cada modelo muestra cuántas imágenes leyó bien, cuántos renglones acertó (producto y
cantidad), si el JSON salió válido, la latencia y los tokens. Con --precio estima el costo de los de API.
"""

import argparse
import json
import statistics
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from internal.config import get_settings
from internal.schemas.vision_schema import ImagenLeida, RenglonLeido
from internal.services.vision_service import PROVEEDORES_API, Lectura, leer_imagen

CASOS = Path("tests/fixtures/imagenes_pedidos/casos.json")


def _normalizar(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return " ".join(sin_tildes.lower().split())


def _tiene_palabras(texto: str, palabras: list[str]) -> bool:
    normalizado = _normalizar(texto)
    return all(_normalizar(p) in normalizado for p in palabras)


def _coincide(esperado: dict, leido: RenglonLeido) -> bool:
    """El renglón leído tiene todas las palabras esperadas (en cualquier campo) y la misma cantidad.

    Un renglón esperado sin cantidad («Igora Royal con tonos») tiene que leerse sin cantidad.
    """
    campos = " ".join(filter(None, [leido.texto, leido.producto, leido.marca, leido.presentacion]))
    if not _tiene_palabras(campos, esperado["palabras"]):
        return False
    cantidad = _cantidad(leido)
    if esperado["cantidad"] is None:
        return cantidad is None
    return cantidad is not None and float(cantidad) == float(esperado["cantidad"])


def _cantidad(leido: RenglonLeido) -> float | None:
    """La cantidad del renglón; con variantes («Koleston 7.1 x2» como tono 7.1 y cantidad 2), la suma de las suyas."""
    if leido.cantidad is not None or not leido.variantes:
        return leido.cantidad
    cantidades = [variante.cantidad for variante in leido.variantes]
    return None if None in cantidades else sum(cantidades)


def _describir(esperado: dict) -> str:
    cantidad = "?" if esperado["cantidad"] is None else f"{esperado['cantidad']:g}"
    return f"{cantidad} {' '.join(esperado['palabras'])}"


@dataclass
class Puntaje:
    json_valido: bool
    legible_ok: bool
    acertados: int
    esperados: int
    sobrantes: int  # renglones leídos que no corresponden a ninguno esperado (inventados o mal leídos)
    faltantes: list[str] = field(default_factory=list)
    # Lo tachado en la hoja (spec de lectura de imágenes, RNF-18): cuántos se reconocieron como tachados y cuántos
    # terminaron como renglón del pedido, que es el error que importa
    tachados_vistos: int = 0
    tachados_esperados: int = 0
    tachados_pedidos: int = 0

    @property
    def ok(self) -> bool:
        return (
            self.json_valido
            and self.legible_ok
            and self.acertados == self.esperados
            and self.sobrantes == 0
            and self.tachados_pedidos == 0
        )


def puntuar(caso: dict, resultado: ImagenLeida | None) -> Puntaje:
    esperados = caso["renglones"]
    if resultado is None:
        return Puntaje(False, False, 0, len(esperados), 0, [_describir(e) for e in esperados])

    libres = list(resultado.renglones)
    acertados, faltantes = 0, []
    for esperado in esperados:
        par = next((leido for leido in libres if _coincide(esperado, leido)), None)
        if par is None:
            faltantes.append(_describir(esperado))
        else:
            libres.remove(par)
            acertados += 1
    tachados = caso.get("tachados", [])
    leidos_tachados = " ".join(resultado.tachados)
    vistos = sum(_tiene_palabras(leidos_tachados, palabras) for palabras in tachados)
    pedidos = sum(
        any(_tiene_palabras(f"{leido.texto} {leido.producto or ''}", palabras) for leido in resultado.renglones)
        for palabras in tachados
    )
    return Puntaje(
        True, resultado.legible == caso["legible"], acertados, len(esperados), len(libres), faltantes,
        tachados_vistos=vistos, tachados_esperados=len(tachados), tachados_pedidos=pedidos,
    )  # fmt: skip


@dataclass
class Corrida:
    proveedor: str
    modelo: str
    lecturas: list[tuple[dict, Lectura, Puntaje]] = field(default_factory=list)

    def resumen(self, precio: tuple[float, float] | None) -> dict:
        puntajes = [p for _, _, p in self.lecturas]
        segundos = [lectura.segundos for _, lectura, _ in self.lecturas]
        entrada = sum(lectura.tokens_entrada or 0 for _, lectura, _ in self.lecturas)
        salida = sum(lectura.tokens_salida or 0 for _, lectura, _ in self.lecturas)
        resumen = {
            "modelo": f"{self.proveedor}:{self.modelo}",
            "imagenes_ok": f"{sum(p.ok for p in puntajes)}/{len(puntajes)}",
            "renglones": f"{sum(p.acertados for p in puntajes)}/{sum(p.esperados for p in puntajes)}",
            "sobrantes": sum(p.sobrantes for p in puntajes),
            "tachados": f"{sum(p.tachados_vistos for p in puntajes)}/{sum(p.tachados_esperados for p in puntajes)}",
            "tachados_pedidos": sum(p.tachados_pedidos for p in puntajes),
            "json_valido": f"{sum(p.json_valido for p in puntajes)}/{len(puntajes)}",
            "segundos_media": round(statistics.mean(segundos), 1),
            "segundos_max": round(max(segundos), 1),
            "tokens": f"{entrada} → {salida}",
        }
        if precio and self.proveedor in PROVEEDORES_API:
            costo = (entrada * precio[0] + salida * precio[1]) / 1_000_000
            resumen["usd_por_imagen"] = f"{costo / len(puntajes):.5f}"
        return resumen


def correr(proveedor: str, modelo: str, casos: list[dict], carpeta: Path) -> Corrida:
    corrida = Corrida(proveedor, modelo)
    for caso in casos:
        lectura = leer_imagen((carpeta / caso["archivo"]).read_bytes(), proveedor, modelo)
        puntaje = puntuar(caso, lectura.resultado)
        corrida.lecturas.append((caso, lectura, puntaje))
        marca = "✅" if puntaje.ok else "❌"
        detalle = f"{puntaje.acertados}/{puntaje.esperados} renglones"
        if puntaje.sobrantes:
            detalle += f", {puntaje.sobrantes} de más"
        if puntaje.tachados_esperados:
            detalle += f", tachados {puntaje.tachados_vistos}/{puntaje.tachados_esperados}"
        if puntaje.tachados_pedidos:
            detalle += f" ({puntaje.tachados_pedidos} pedidos)"
        if puntaje.faltantes:
            detalle += f", faltan: {'; '.join(puntaje.faltantes)}"
        if lectura.error:
            detalle += f" — {lectura.error[:120]}"
        print(f"  {marca} {caso['id']:<18} {lectura.segundos:5.1f} s  {detalle}")
    return corrida


def _tabla(filas: list[dict]) -> str:
    columnas = list(dict.fromkeys(k for fila in filas for k in fila))
    anchos = {c: max(len(c), *(len(str(f.get(c, ""))) for f in filas)) for c in columnas}
    lineas = [" | ".join(c.ljust(anchos[c]) for c in columnas), "-+-".join("-" * anchos[c] for c in columnas)]
    lineas += [" | ".join(str(f.get(c, "")).ljust(anchos[c]) for c in columnas) for f in filas]
    return "\n".join(lineas)


def _modelo(valor: str) -> tuple[str, str]:
    proveedor, _, modelo = valor.partition(":")
    if not modelo:
        raise argparse.ArgumentTypeError("usar proveedor:modelo, por ejemplo openai:gpt-6-luna")
    return proveedor, modelo


def _precio(valor: str) -> tuple[float, float]:
    entrada, _, salida = valor.partition(",")
    return float(entrada), float(salida)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--modelo", type=_modelo, action="append", help="proveedor:modelo (se puede repetir)")
    parser.add_argument("--casos", type=Path, default=CASOS)
    parser.add_argument("--solo", help="corre solo el caso con este id")
    parser.add_argument("--precio", type=_precio, help="USD por millón de tokens de entrada,salida (ej. 0.10,0.50)")
    parser.add_argument("--salida", type=Path, help="guarda cada lectura en este JSON (ej. data/vision.json)")
    args = parser.parse_args(argv)

    settings = get_settings()
    modelos = args.modelo or [(settings.llm_vision_provider or "", settings.llm_vision_model or "")]
    casos = json.loads(args.casos.read_text())
    if args.solo:
        casos = [c for c in casos if c["id"] == args.solo]

    corridas = []
    for proveedor, modelo in modelos:
        print(f"\n▶ {proveedor}:{modelo}")
        corridas.append(correr(proveedor, modelo, casos, args.casos.parent))

    print("\n" + _tabla([c.resumen(args.precio) for c in corridas]))

    if args.salida:
        detalle = [
            {
                "modelo": f"{c.proveedor}:{c.modelo}",
                "caso": caso["id"],
                "ok": puntaje.ok,
                "segundos": round(lectura.segundos, 2),
                "tokens_entrada": lectura.tokens_entrada,
                "tokens_salida": lectura.tokens_salida,
                "error": lectura.error,
                "resultado": lectura.resultado.model_dump() if lectura.resultado else None,
                "crudo": lectura.crudo,
            }
            for c in corridas
            for caso, lectura, puntaje in c.lecturas
        ]
        args.salida.write_text(json.dumps(detalle, ensure_ascii=False, indent=2))
        print(f"\nDetalle en {args.salida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
