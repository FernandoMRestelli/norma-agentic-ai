"""Listas y Google Form del benchmark de pedidos escritos a mano (docs/spec/benchmark-manuscritos.md).

    python -m internal.benchmark_manuscritos listas --cantidad 30 --semilla 116
    python -m internal.benchmark_manuscritos form

`listas` sortea las listas numeradas desde `pool.csv` (núcleo fijo + 10 renglones estratificados por dificultad) y las
guarda en `listas.json`. `form` arma `crear_form.gs`, un Google Apps Script que crea el Form con una sección por lista:
se pega en script.google.com y se corre una vez.
"""

import argparse
import csv
import json
import random
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

CARPETA = Path("data_example/benchmark_manuscritos")
POOL = CARPETA / "pool.csv"
LISTAS = CARPETA / "listas.json"
FORM = CARPETA / "crear_form.gs"
CATALOGO = Path("data_example/catalogo_evaluacion.csv")

SEMILLA = 116
VARIABLES = 10
TIPOS = (
    "abreviatura",
    "x_medida",
    "numero_en_producto",
    "cantidad_en_palabras",
    "cantidad_al_final",
    "numeros_confusos",
    "tono",
    "unidad",
    "casi_iguales",
)
UNIDADES = {"unidades", "cajas", "cada_uno"}
MINIMO_POR_TIPO = 5
MINIMO_SORTEABLES = 100

# El encabezado de la hoja, igual en todas las listas: no es pedido y la lectura lo tiene que separar.
ENCABEZADO = "Pedido para Norma"


@dataclass(frozen=True)
class Renglon:
    """Un renglón del pool, tal como se copia, con su respuesta."""

    id: str
    origen: str
    texto: str
    sku: str
    cantidad: float
    tono: str | None
    unidad: str | None
    palabras: tuple[str, ...]
    dificultad: tuple[str, ...]

    def como_dict(self, n: int) -> dict:
        cantidad = int(self.cantidad) if float(self.cantidad).is_integer() else self.cantidad
        return {
            "n": n,
            "id": self.id,
            "origen": self.origen,
            "texto": self.texto,
            "sku": self.sku,
            "cantidad": cantidad,
            "tono": self.tono,
            "unidad": self.unidad,
            "palabras": list(self.palabras),
            "dificultad": list(self.dificultad),
        }


def leer_pool(ruta: Path = POOL) -> list[Renglon]:
    with ruta.open(encoding="utf-8", newline="") as archivo:
        return [
            Renglon(
                id=fila["id"],
                origen=fila["origen"],
                texto=fila["texto"],
                sku=fila["sku"],
                cantidad=float(fila["cantidad"]),
                tono=fila["tono"] or None,
                unidad=fila["unidad"] or None,
                palabras=tuple(fila["palabras"].split()),
                dificultad=tuple(fila["dificultad"].split()),
            )
            for fila in csv.DictReader(archivo)
        ]


def leer_skus(ruta: Path = CATALOGO) -> set[str]:
    with ruta.open(encoding="utf-8", newline="") as archivo:
        return {fila["sku"] for fila in csv.DictReader(archivo)}


def validar_pool(pool: list[Renglon], skus: set[str]) -> list[str]:
    """Los problemas del pool (RF-126); vacío si está bien."""
    problemas = []
    ids = [renglon.id for renglon in pool]
    if len(ids) != len(set(ids)):
        problemas.append("hay ids repetidos")
    nucleo = [renglon for renglon in pool if renglon.origen == "nucleo"]
    sorteables = [renglon for renglon in pool if renglon.origen == "pool"]
    if len(nucleo) != 5:
        problemas.append(f"el núcleo tiene {len(nucleo)} renglones y tienen que ser 5")
    if len(sorteables) < MINIMO_SORTEABLES:
        problemas.append(f"hay {len(sorteables)} renglones sorteables y tienen que ser al menos {MINIMO_SORTEABLES}")
    for renglon in pool:
        if renglon.origen not in {"nucleo", "pool"}:
            problemas.append(f"{renglon.id}: origen «{renglon.origen}» desconocido")
        if renglon.sku not in skus:
            problemas.append(f"{renglon.id}: el SKU {renglon.sku} no está en el catálogo")
        if renglon.cantidad <= 0:
            problemas.append(f"{renglon.id}: la cantidad tiene que ser mayor que cero")
        if renglon.unidad and renglon.unidad not in UNIDADES:
            problemas.append(f"{renglon.id}: unidad «{renglon.unidad}» desconocida")
        if not renglon.palabras:
            problemas.append(f"{renglon.id}: no tiene palabras")
        for tipo in renglon.dificultad:
            if tipo not in TIPOS:
                problemas.append(f"{renglon.id}: dificultad «{tipo}» desconocida")
    for tipo in TIPOS:
        cuantos = sum(tipo in renglon.dificultad for renglon in sorteables)
        if cuantos < MINIMO_POR_TIPO:
            problemas.append(f"«{tipo}» tiene {cuantos} renglones sorteables y tienen que ser {MINIMO_POR_TIPO} o más")
    return problemas


def _menos_usados(candidatos: list[Renglon], usos: Counter, rng: random.Random, cuantos: int) -> list[Renglon]:
    """Los `cuantos` candidatos que salieron menos veces en las listas anteriores; los empates, al azar."""
    orden = sorted(candidatos, key=lambda renglon: (usos[renglon.id], rng.random()))
    return orden[:cuantos]


def _sortear_variables(
    sorteables: list[Renglon], usados: set[str], usos: Counter, rng: random.Random
) -> list[Renglon]:
    """10 renglones sin repetir SKU: primero uno por cada tipo que falte cubrir (RF-127), después al azar.

    Entre los candidatos se prefieren los que salieron menos en las listas anteriores, así el pool se usa parejo.
    """
    elegidos: list[Renglon] = []
    for tipo in rng.sample(TIPOS, len(TIPOS)):
        if any(tipo in renglon.dificultad for renglon in elegidos):
            continue
        candidatos = [r for r in sorteables if tipo in r.dificultad and r.sku not in usados]
        if not candidatos:
            raise ValueError(f"El pool no tiene renglones de «{tipo}» que no repitan un SKU de la lista")
        [elegido] = _menos_usados(candidatos, usos, rng, 1)
        elegidos.append(elegido)
        usados.add(elegido.sku)
    resto = [r for r in sorteables if r.sku not in usados]
    faltan = VARIABLES - len(elegidos)
    if faltan < 0 or len(resto) < faltan:
        raise ValueError("El pool no alcanza para 10 renglones sin repetir SKU")
    completan = _menos_usados(resto, usos, rng, faltan)
    usados.update(renglon.sku for renglon in completan)
    variables = elegidos + completan
    rng.shuffle(variables)
    return variables


def sortear_lista(pool: list[Renglon], numero: int, rng: random.Random, usos: Counter | None = None) -> dict:
    """Una lista numerada. `usos` cuenta cuántas veces salió cada renglón en las anteriores y se actualiza."""
    usos = Counter() if usos is None else usos
    nucleo = [renglon for renglon in pool if renglon.origen == "nucleo"]
    sorteables = [renglon for renglon in pool if renglon.origen == "pool"]
    usados = {renglon.sku for renglon in nucleo}
    variables = _sortear_variables(sorteables, usados, usos, rng)
    usos.update(renglon.id for renglon in variables)
    return {
        "id": f"L{numero:02d}",
        "encabezado": ENCABEZADO,
        "renglones": [renglon.como_dict(n) for n, renglon in enumerate([*nucleo, *variables], start=1)],
    }


def sortear_listas(pool: list[Renglon], cantidad: int, semilla: int = SEMILLA) -> dict:
    rng = random.Random(semilla)
    usos: Counter = Counter()
    return {
        "semilla": semilla,
        "listas": [sortear_lista(pool, numero, rng, usos) for numero in range(1, cantidad + 1)],
    }


def lineas_de_la_hoja(lista: dict) -> list[str]:
    """Los renglones en el orden de la hoja."""
    return [renglon["texto"] for renglon in sorted(lista["renglones"], key=lambda renglon: renglon["n"])]


INICIO = """¡Hola! Te pido una mano para un trabajo de la facu: estoy midiendo qué tan bien una IA lee pedidos \
escritos a mano.

1. Elegí abajo el pedido que quieras y tocá «Siguiente»: vas a ver los renglones que tenés que copiar.
2. Copialo a mano en un papel, con tu letra de siempre, como si se lo hicieras a tu proveedor.
3. Arriba, como encabezado, poné «Pedido para Norma».
4. Si te equivocás al escribir, tachalo con una línea y seguí, como harías en un pedido de verdad. No hace falta \
empezar de nuevo.
5. Sacale una foto con el celu como la mandarías por WhatsApp: sin escanear, sin recortar, con la luz que tengas, y \
subila al final.

No pongas tu nombre ni datos tuyos en la hoja. La foto se usa solo para este trabajo, no se publica, y se manda a la \
API del modelo que la lee (OpenAI o Google) para medir. Si preferís que no, no pasa nada."""

CONSENTIMIENTO = "Acepto que la foto se use para este trabajo y se mande a la API del modelo que la lee"

# Los textos que muestra el Form, fuera de la plantilla: van al script como JSON con escapes \uXXXX (ver
# `script_del_form`).
TEXTOS = {
    "titulo": "Pedidos escritos a mano",
    "inicio": INICIO,
    "consentimiento_titulo": "Consentimiento",
    "consentimiento": CONSENTIMIENTO,
    "elegir_lista": "Elegí un pedido",
    "final_titulo": "La foto y unas preguntas",
    "final_ayuda": "Subí la foto de la hoja y contanos cómo la sacaste.",
    "copio_todo": "¿Escribiste todos los renglones tal cual?",
    "si_no": ["Sí", "No"],
    "cambios": "Si no, ¿cuáles cambiaste o salteaste?",
    "luz": "Luz",
    "luces": ["Natural", "Artificial", "Poca luz"],
    "papel": "Papel",
    "papeles": ["Cuaderno rayado", "Hoja lisa", "Otro"],
    "sexo": "Sexo",
    "sexos": ["Masculino", "Femenino", "Prefiero no responder"],
}

# Solo ASCII: el portapapeles de macOS (pbcopy) y algunos editores leen el archivo como Mac Roman y rompen los acentos
# («Copiá» → «Copi√°»). Los acentos de los textos van como \uXXXX, que JavaScript entiende igual.
PLANTILLA_FORM = """// Generado con `python -m internal.benchmark_manuscritos form` (semilla {semilla}).
// No se edita a mano: se cambia el pool o la semilla y se vuelve a generar.
//
// 1. Pegalo en un proyecto nuevo de https://script.google.com y corre crearForm() con la cuenta que va a recibir
//    las fotos (pide permiso para crear Forms).
// 2. Abri el link de edicion que queda en el log y, en la seccion final, agrega la pregunta "Subir archivo":
//    "Foto del pedido", solo imagenes, un archivo, obligatoria. Apps Script no puede crear esa pregunta.
// 3. Manda el link publico: cada persona elige el pedido que quiera en el desplegable.

const TEXTOS = {textos};
const LISTAS = {listas};

function crearForm() {{
  const form = FormApp.create(TEXTOS.titulo);
  form.setDescription(TEXTOS.inicio);
  form.setCollectEmail(false);
  form.setProgressBar(true);

  form.addCheckboxItem()
    .setTitle(TEXTOS.consentimiento_titulo)
    .setChoiceValues([TEXTOS.consentimiento])
    .setRequired(true);
  const elegir = form.addListItem()
    .setTitle(TEXTOS.elegir_lista)
    .setRequired(true);

  const secciones = LISTAS.map(function (lista) {{
    return form.addPageBreakItem()
      .setTitle(lista.nombre)
      .setHelpText(lista.texto);
  }});

  const final = form.addPageBreakItem()
    .setTitle(TEXTOS.final_titulo)
    .setHelpText(TEXTOS.final_ayuda);
  secciones.forEach(function (seccion) {{
    seccion.setGoToPage(final);
  }});
  elegir.setChoices(LISTAS.map(function (lista, i) {{
    return elegir.createChoice(lista.nombre, secciones[i]);
  }}));

  form.addMultipleChoiceItem()
    .setTitle(TEXTOS.copio_todo)
    .setChoiceValues(TEXTOS.si_no)
    .setRequired(true);
  form.addParagraphTextItem()
    .setTitle(TEXTOS.cambios);
  form.addMultipleChoiceItem()
    .setTitle(TEXTOS.luz)
    .setChoiceValues(TEXTOS.luces)
    .setRequired(true);
  form.addMultipleChoiceItem()
    .setTitle(TEXTOS.papel)
    .setChoiceValues(TEXTOS.papeles)
    .setRequired(true);
  form.addMultipleChoiceItem()
    .setTitle(TEXTOS.sexo)
    .setChoiceValues(TEXTOS.sexos)
    .setRequired(true);

  Logger.log('Para editar (agrega la pregunta de la foto): ' + form.getEditUrl());
  Logger.log('Para mandar: ' + form.getPublishedUrl());
}}
"""


def nombre_del_pedido(id_lista: str) -> str:
    """Cómo ve la lista quien participa: «L07» es «Pedido 07». Es lo que queda en las respuestas del Form."""
    return f"Pedido {id_lista.removeprefix('L')}"


def script_del_form(listas: dict) -> str:
    """El Apps Script que crea el Form (RF-129).

    Los textos van como JSON, que también es JavaScript válido, con `ensure_ascii=True`: el archivo sale solo en
    ASCII y se puede copiar y pegar sin que se rompan los acentos.
    """
    secciones = []
    for lista in listas["listas"]:
        texto = "\n".join(
            [
                "Escribí este pedido en un papel e incluí el encabezado.",
                "",
                lista["encabezado"],
                "",
                *lineas_de_la_hoja(lista),
            ]
        )
        secciones.append({"id": lista["id"], "nombre": nombre_del_pedido(lista["id"]), "texto": texto})
    return PLANTILLA_FORM.format(
        semilla=listas["semilla"],
        textos=json.dumps(TEXTOS, ensure_ascii=True, indent=2),
        listas=json.dumps(secciones, ensure_ascii=True, indent=2),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    comandos = parser.add_subparsers(dest="comando", required=True)

    listas = comandos.add_parser("listas", help="Sortea las listas numeradas desde pool.csv")
    listas.add_argument("--pool", type=Path, default=POOL)
    listas.add_argument("--catalogo", type=Path, default=CATALOGO)
    listas.add_argument("--cantidad", type=int, default=30)
    listas.add_argument("--semilla", type=int, default=SEMILLA)
    listas.add_argument("--salida", type=Path, default=LISTAS)

    form = comandos.add_parser("form", help="Arma el Apps Script que crea el Google Form")
    form.add_argument("--listas", type=Path, default=LISTAS)
    form.add_argument("--salida", type=Path, default=FORM)

    args = parser.parse_args(argv)
    if args.comando == "listas":
        pool = leer_pool(args.pool)
        problemas = validar_pool(pool, leer_skus(args.catalogo))
        if problemas:
            print("El pool tiene problemas:\n- " + "\n- ".join(problemas))
            return 1
        resultado = sortear_listas(pool, args.cantidad, args.semilla)
        args.salida.write_text(json.dumps(resultado, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{len(resultado['listas'])} listas en {args.salida}")
    else:
        resultado = json.loads(args.listas.read_text(encoding="utf-8"))
        args.salida.write_text(script_del_form(resultado), encoding="utf-8")
        print(f"Script del Form en {args.salida}: pegalo en script.google.com y corré crearForm()")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
