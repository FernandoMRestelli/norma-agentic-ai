"""Listas, Google Form y casos del benchmark de pedidos escritos a mano (docs/spec/benchmark-manuscritos.md).

    python -m internal.benchmark_manuscritos listas --cantidad 30 --semilla 116
    python -m internal.benchmark_manuscritos form
    python -m internal.benchmark_manuscritos casos

`listas` sortea las listas numeradas desde `pool.csv` (núcleo fijo + 10 renglones estratificados por dificultad) y las
guarda en `listas.json`. `form` arma `crear_form.gs`, un Google Apps Script que crea el Form con una sección por lista:
se pega en script.google.com y se corre una vez. `casos` cruza las respuestas del Form y la revisión de cada foto con
las listas y arma `data/benchmark_manuscritos/casos.json`, el archivo que puntúa `evaluar_manuscritos`.
"""

import argparse
import csv
import json
import math
import random
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

CARPETA = Path("data_example/benchmark_manuscritos")
POOL = CARPETA / "pool.csv"
LISTAS = CARPETA / "listas.json"
FORM = CARPETA / "crear_form.gs"
CATALOGO = Path("data_example/catalogo_evaluacion.csv")

# Lo de cada experimento: letra de personas reales, no se versiona
DATOS = Path("data/benchmark_manuscritos")
RESPUESTAS = DATOS / "respuestas.csv"
REVISION = DATOS / "revision.json"
CASOS = DATOS / "casos.json"

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

SEMILLA_GRUPOS = 130
PROPORCION_DESARROLLO = 0.4

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


# --- Casos (RF-130 a RF-132) ---

_FOTO = re.compile(r"^(p\d{2,})-([AB])$")
# Una columna o un valor con datos de la persona: el export del Form se limpia a mano antes (diseño, §5)
_COLUMNAS_PRIVADAS = ("mail", "correo", "nombre", "archivo")
_LINK_DE_DRIVE = "drive.google.com"


@dataclass
class Armado:
    """Lo que sale de cruzar las respuestas con las listas: los casos, o los problemas que lo impiden."""

    casos: list[dict] = field(default_factory=list)
    descartadas: dict[str, str] = field(default_factory=dict)
    problemas: list[str] = field(default_factory=list)


def _sin_tildes(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().lower()


def problema_de_privacidad(columna: str) -> str:
    return f"respuestas.csv tiene la columna «{columna}», con datos de la persona: borrala antes de armar los casos"


def _problemas_de_privacidad(respuestas: list[dict]) -> list[str]:
    columnas = list(dict.fromkeys(columna for fila in respuestas for columna in fila))
    privadas = [
        columna
        for columna in columnas
        if any(clave in _sin_tildes(columna) for clave in _COLUMNAS_PRIVADAS)
        or any(_LINK_DE_DRIVE in (fila.get(columna) or "") for fila in respuestas)
    ]
    return [problema_de_privacidad(columna) for columna in privadas]


def lista_del_pedido(pedido: str) -> str:
    """Lo inverso de `nombre_del_pedido`: «Pedido 07» es «L07»."""
    return "L" + pedido.strip().removeprefix("Pedido").strip()


def _archivo(carpeta: Path, foto: str) -> str | None:
    """La foto en `imagenes/`, con la extensión que tenga, relativa a la carpeta de casos.json."""
    encontradas = sorted((carpeta / "imagenes").glob(f"{foto}.*"))
    return f"imagenes/{encontradas[0].name}" if encontradas else None


def asignar_grupos(
    personas: list[str], semilla: int = SEMILLA_GRUPOS, anteriores: dict[str, str] | None = None
) -> dict[str, str]:
    """`desarrollo` o `reservado` para cada persona (RF-131).

    El 40 % de las personas, redondeado para arriba, va a `desarrollo`. Quien ya tenía grupo en `anteriores` lo
    conserva: solo se sortean las nuevas, completando la proporción sobre el total.
    """
    unicas = sorted(set(personas))
    grupos = {persona: anteriores[persona] for persona in unicas if anteriores and persona in anteriores}
    nuevas = [persona for persona in unicas if persona not in grupos]
    random.Random(semilla).shuffle(nuevas)
    faltan = max(0, math.ceil(PROPORCION_DESARROLLO * len(unicas)) - list(grupos.values()).count("desarrollo"))
    for indice, persona in enumerate(nuevas):
        grupos[persona] = "desarrollo" if indice < faltan else "reservado"
    return dict(sorted(grupos.items()))


def armar_casos(
    respuestas: list[dict],
    revision: dict[str, dict],
    listas: dict,
    carpeta: Path,
    semilla: int = SEMILLA_GRUPOS,
    anteriores: dict[str, str] | None = None,
) -> Armado:
    """Un caso por foto no descartada (RF-130), o la lista de problemas si hay alguno (RF-132).

    `carpeta` es la de casos.json, con las fotos en `imagenes/`. `anteriores` son los grupos de un casos.json previo.
    """
    if privacidad := _problemas_de_privacidad(respuestas):
        return Armado(problemas=privacidad)

    por_id = {lista["id"]: lista for lista in listas["listas"]}
    armado = Armado()
    vistas: Counter = Counter(fila.get("foto", "").strip() for fila in respuestas)
    for foto in sorted(foto for foto, veces in vistas.items() if veces > 1):
        armado.problemas.append(f"{foto}: la foto está repetida en respuestas.csv ({vistas[foto]} filas)")

    casos = []
    for fila in respuestas:
        foto = fila.get("foto", "").strip()
        if vistas[foto] > 1:
            continue
        if not (partes := _FOTO.match(foto)):
            armado.problemas.append(f"{foto or '(vacía)'}: el id de la foto tiene que ser pNN-A o pNN-B")
            continue
        notas = revision.get(foto)
        if notas and notas.get("descartar"):
            armado.descartadas[foto] = notas["descartar"]
            continue
        notas = notas or {}
        pedido = fila.get(TEXTOS["elegir_lista"], "").strip()
        lista = por_id.get(lista_del_pedido(pedido)) if pedido else None
        if lista is None:
            armado.problemas.append(f"{foto}: «{pedido}» no está en listas.json")
            continue
        if (archivo := _archivo(carpeta, foto)) is None:
            armado.problemas.append(f"{foto}: no hay archivo en imagenes/ ({foto}.jpg, .png…)")
        if fila.get(TEXTOS["copio_todo"], "").strip() == TEXTOS["si_no"][1] and foto not in revision:
            armado.problemas.append(
                f"{foto}: dice «No» en «{TEXTOS['copio_todo']}» y no tiene entrada en revision.json"
                " (si copió todo, anotala con {{}})"
            )
        sin_copiar = list(notas.get("sin_copiar", []))
        posiciones = {renglon["n"] for renglon in lista["renglones"]}
        if fuera := [n for n in sin_copiar if n not in posiciones]:
            armado.problemas.append(f"{foto}: sin_copiar tiene {fuera}, fuera de 1 a {max(posiciones)}")
        caso = {
            "id": foto,
            "archivo": archivo,
            "persona": partes.group(1),
            "hoja": partes.group(2),
            "lista": lista["id"],
            "grupo": None,
            "condiciones": {
                "luz": fila.get(TEXTOS["luz"], "").strip().lower(),
                "papel": fila.get(TEXTOS["papel"], "").strip().lower(),
            },
            "sexo": fila.get(TEXTOS["sexo"], "").strip().lower(),
            "legible": notas.get("legible", True),
            "encabezado": lista["encabezado"],
            "renglones": [renglon for renglon in lista["renglones"] if renglon["n"] not in sin_copiar],
            "tachados": [list(palabras) for palabras in notas.get("tachados", [])],
            "sin_copiar": sin_copiar,
        }
        if notas.get("notas"):
            caso["notas"] = notas["notas"]
        casos.append(caso)

    fotos = {fila.get("foto", "").strip() for fila in respuestas}
    for foto in sorted(set(revision) - fotos):
        armado.problemas.append(f"{foto}: está en la revisión y no en respuestas.csv")
    if armado.problemas:
        return Armado(problemas=armado.problemas)

    grupos = asignar_grupos([caso["persona"] for caso in casos], semilla, anteriores)
    for caso in casos:
        caso["grupo"] = grupos[caso["persona"]]
    armado.casos = sorted(casos, key=lambda caso: caso["id"])
    return armado


def leer_respuestas(ruta: Path = RESPUESTAS) -> list[dict]:
    # utf-8-sig: el CSV que baja Google Forms puede traer BOM
    with open(ruta, encoding="utf-8-sig", newline="") as archivo:
        return [{(clave or "").strip(): valor for clave, valor in fila.items()} for fila in csv.DictReader(archivo)]


def _grupos_anteriores(ruta: Path) -> dict[str, str]:
    if not ruta.exists():
        return {}
    return {caso["persona"]: caso["grupo"] for caso in json.loads(ruta.read_text(encoding="utf-8"))}


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

    casos = comandos.add_parser("casos", help="Arma casos.json desde las respuestas del Form y la revisión")
    casos.add_argument("--respuestas", type=Path, default=RESPUESTAS)
    casos.add_argument("--revision", type=Path, default=REVISION)
    casos.add_argument("--listas", type=Path, default=LISTAS)
    casos.add_argument("--salida", type=Path, default=CASOS)
    casos.add_argument("--semilla", type=int, default=SEMILLA_GRUPOS)

    args = parser.parse_args(argv)
    if args.comando == "casos":
        return _comando_casos(args)
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


def _comando_casos(args: argparse.Namespace) -> int:
    if not args.respuestas.exists():
        print(f"Falta {args.respuestas}: exportá las respuestas del Form, sin mail ni nombre, y sumá la columna foto")
        return 1
    revision = json.loads(args.revision.read_text(encoding="utf-8")) if args.revision.exists() else {}
    armado = armar_casos(
        leer_respuestas(args.respuestas),
        revision,
        json.loads(args.listas.read_text(encoding="utf-8")),
        args.salida.parent,
        args.semilla,
        _grupos_anteriores(args.salida),
    )
    if armado.problemas:
        print(f"No se armaron los casos; {len(armado.problemas)} problemas:\n- " + "\n- ".join(armado.problemas))
        return 1
    args.salida.write_text(json.dumps(armado.casos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    grupos = Counter(caso["grupo"] for caso in armado.casos)
    print(
        f"{len(armado.casos)} casos en {args.salida} "
        f"({grupos['desarrollo']} de desarrollo, {grupos['reservado']} reservados)"
    )
    for foto, motivo in armado.descartadas.items():
        print(f"- {foto} descartada: {motivo}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
