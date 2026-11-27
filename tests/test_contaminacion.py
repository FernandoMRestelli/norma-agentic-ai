"""La evaluación no se mide con lo que ya vio el modelo (M8-1, docs/spec/mejoras-clase8.md).

En Software 3.0 el prompt es lo que se «entrena»: si trae el texto de un caso que lo mide, el caso deja de medir. Estos
tests corren en el CI y fallan si:

- un caso `reservado` comparte tres palabras seguidas con un prompt, una skill o una BR;
- un caso `reservado` se parece demasiado a uno de `desarrollo` del mismo catálogo (D-M8-1).

Si falla, no se acomoda el prompt mirando el caso: se reescribe el ejemplo del prompt, o el caso pasa a desarrollo y
se escribe otro (D-50). Los reservados de `mensajes` y `pedidos` se regeneran con otra semilla
(`tests/casos_reservados.py`).
"""

import json
import re
import unicodedata
from pathlib import Path

import pytest

from internal import evaluar_agente
from tests import casos_reservados

RAIZ = Path(__file__).resolve().parent.parent
DATOS = RAIZ / "data_example"
# Lo que lee el LLM: el system prompt, el del orquestador y el del sub-loop, cada skill, sus BR y sus datos
PROMPTS = sorted([*(RAIZ / "internal" / "agent").glob("*.md"), *(RAIZ / "internal" / "skills").rglob("*.md")])
# Los conjuntos que comparten catálogo se comparan entre sí: una consulta de búsqueda puede filtrar un adversarial
FAMILIAS = {
    "catalogo": ["mensajes.json"],
    "catalogo_evaluacion": ["pedidos_evaluacion.json", "adversariales.json", "consultas_busqueda.json"],
}
N = 3
# D-M8-1: un n-grama con dos palabras de función o más («con una persona», «la cantidad a») no dice de qué caso
# salió. Parecido de Jaccard sobre los n-gramas de dos casos: arriba de esto, son el mismo caso escrito dos veces
PALABRAS_DE_FUNCION = {
    "a", "al", "con", "de", "del", "el", "en", "es", "la", "las", "lo", "los", "me", "mi", "no", "o", "para", "por",
    "que", "se", "si", "su", "te", "un", "una", "y",
}  # fmt: skip
MAX_PARECIDO = 0.5


def palabras(texto: str) -> list[str]:
    """Las palabras de un texto, normalizadas con los primeros pasos de `busqueda_service.normalizar`: sin tildes ni
    mayúsculas, la coma decimal como punto y el número separado de su unidad («x900» → «x 900»). Un tono/cantidad
    («7/3») queda como una sola palabra."""
    limpio = "".join(c for c in unicodedata.normalize("NFD", texto.lower()) if unicodedata.category(c) != "Mn")
    limpio = re.sub(r"['’`´]", "", limpio)
    limpio = re.sub(r"(\d),(\d)", r"\1.\2", limpio)
    limpio = re.sub(r"(\d)([a-z])", r"\1 \2", limpio)
    limpio = re.sub(r"\bx(?=\d)", "x ", limpio)
    return re.findall(r"\d+(?:[./]\d+)*|[a-z0-9]+", limpio)


# Un n-grama no cruza renglones, comillas ni signos: «x 1k», «1 sh» no es la frase «1k 1 sh». La coma y el punto
# cortan solo seguidos de un espacio, para no partir «6.1» ni «1,5»
_CORTES = re.compile(r"[\n«»\"“”;:¿?¡!()•*`]+|[.,](?=\s|$)")


def ngramas(texto: str, *, con_funcion: bool = True) -> set[tuple[str, ...]]:
    todos = set()
    for tramo in _CORTES.split(texto):
        tokens = palabras(tramo)
        todos |= {tuple(tokens[i : i + N]) for i in range(len(tokens) - N + 1)}
    if con_funcion:
        return todos
    return {g for g in todos if sum(palabra in PALABRAS_DE_FUNCION for palabra in g) < 2}


def textos(caso: dict) -> list[str]:
    """Lo que escribe el cliente en un caso: los turnos previos y el último, o la consulta de búsqueda."""
    if "consulta" in caso:
        return [caso["consulta"]]
    return evaluar_agente._turnos(caso)


def _casos(archivo: str) -> list[dict]:
    return json.loads((DATOS / archivo).read_text(encoding="utf-8"))


def _reservados() -> list[tuple[str, dict]]:
    return [(a, c) for archivos in FAMILIAS.values() for a in archivos for c in _casos(a) if c["grupo"] == "reservado"]


@pytest.fixture(scope="module")
def ngramas_de_prompts() -> dict[tuple[str, ...], list[str]]:
    donde: dict[tuple[str, ...], list[str]] = {}
    for prompt in PROMPTS:
        for g in ngramas(prompt.read_text(encoding="utf-8"), con_funcion=False):
            donde.setdefault(g, []).append(str(prompt.relative_to(RAIZ)))
    return donde


def test_hay_prompts_y_casos_reservados():
    """Sin esto, los controles de abajo pasarían sin mirar nada."""
    assert any(p.name == "system_prompt.md" for p in PROMPTS) and any(p.name == "skill.md" for p in PROMPTS)
    assert {archivo for archivo, _ in _reservados()} == {a for archivos in FAMILIAS.values() for a in archivos}


@pytest.mark.parametrize("archivo,caso", _reservados(), ids=lambda x: x if isinstance(x, str) else x["id"])
def test_ningun_prompt_copia_un_caso_reservado(archivo, caso, ngramas_de_prompts):
    """M8-1: ningún n-grama de tres palabras o más de un caso reservado está en un prompt, una skill o una BR."""
    compartidos = {
        " ".join(g): ngramas_de_prompts[g]
        for texto in textos(caso)
        for g in ngramas(texto, con_funcion=False)
        if g in ngramas_de_prompts
    }
    assert not compartidos, f"{archivo} {caso['id']}: {compartidos}"


def test_ningun_reservado_repite_uno_de_desarrollo():
    """M8-1 y D-M8-1: un caso reservado casi igual a uno de desarrollo mide lo que ya se miró."""
    repetidos = []
    for archivos in FAMILIAS.values():
        casos = [(a, c) for a in archivos for c in _casos(a)]
        desarrollo = [(a, c, palabras("\n".join(textos(c)))) for a, c in casos if c["grupo"] == "desarrollo"]
        for archivo, caso in casos:
            if caso["grupo"] != "reservado":
                continue
            propias = palabras("\n".join(textos(caso)))
            for otro_archivo, otro, ajenas in desarrollo:
                if parecido(propias, ajenas) > MAX_PARECIDO:
                    repetidos.append(f"{archivo} {caso['id']} ~ {otro_archivo} {otro['id']}")
    assert not repetidos


def parecido(a: list[str], b: list[str]) -> float:
    """Jaccard de los n-gramas de dos textos. Uno de menos de tres palabras solo se parece a otro igual."""
    if len(a) < N or len(b) < N:
        return float(a == b)
    ga, gb = ({tuple(t[i : i + N]) for i in range(len(t) - N + 1)} for t in (a, b))
    return len(ga & gb) / len(ga | gb)


def test_el_control_encuentra_una_copia():
    """Que el control no pase de largo: la frase de un caso, pegada en un prompt, se detecta."""
    caso = "2 Sh Keratína x900\n1 máscara argán x 1k"
    prompt = "Escriben así: «1 sh keratina x 900, y una mascara argan x1k»."
    assert ngramas(caso, con_funcion=False) & ngramas(prompt, con_funcion=False)
    assert not ngramas("quiero hablar con una persona", con_funcion=False) & ngramas(
        "pide hablar con una persona", con_funcion=False
    )  # solo comparten «con una persona»: dos palabras de función
    assert not ngramas("2 sh color 900\n1 mascara argan") & ngramas("color 900 1")  # no cruza renglones
    assert ("tono", "6.1", "y") in ngramas("tono 6.1 y 7")
    assert parecido(palabras("tenés algo para rulos?"), palabras("tenes algo para rulos")) == 1.0
    assert parecido(palabras("deco 700"), palabras("deco 50")) == 0.0


# --- Los reservados de `mensajes` y `pedidos` salen del generador -----------------------------------------------


@pytest.mark.parametrize("conjunto", sorted(casos_reservados.GENERADORES))
def test_los_reservados_son_los_del_generador(conjunto):
    """Nadie los ajusta a mano: si cambian, se cambia la semilla y se regeneran (`python -m tests.casos_reservados`)."""
    casos = json.loads(casos_reservados.ARCHIVOS[conjunto].read_text(encoding="utf-8"))
    assert all(caso["grupo"] in evaluar_agente.GRUPOS for caso in casos), "Cada caso dice su grupo"
    assert [c for c in casos if c["grupo"] == "reservado"] == casos_reservados.GENERADORES[conjunto]()
    assert len({caso["id"] for caso in casos}) == len(casos)


def test_pedidos_tiene_tantos_reservados_como_de_desarrollo():
    """M8-1: el umbral de release se mide sobre lo reservado, y no puede ser un puñado de casos."""
    grupos = [caso["grupo"] for caso in _casos("pedidos_evaluacion.json")]
    assert grupos.count("reservado") >= grupos.count("desarrollo")
