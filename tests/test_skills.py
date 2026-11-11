"""Cómo están escritas las skills y cómo suena el agente (spec de skills y respuestas), sin LLM real."""

import re
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from internal import evaluar_agente
from internal.agent.context import SYSTEM_PROMPT_PATH
from internal.agent.norma_agent import NormaAgent
from internal.services import sheets_service, skill_service
from internal.services.skill_service import SkillError, load_skills
from internal.tools import skill_tools
from tests.test_agent_graph import ModeloGuionado, _ai, _call

ROOT = Path(__file__).resolve().parent.parent
VALIDACIONES = ROOT / "docs" / "validaciones_pendientes.md"
TEMAS_DEL_NEGOCIO = {"horarios", "zona_de_entrega", "medios_de_pago", "minimo_de_compra"}
EJEMPLO = re.compile(r"^- \*\*(?P<situacion>.+?):\*\* «(?P<respuesta>.+)»$", re.MULTILINE)
MINIMO_DE_EJEMPLOS = {"tomar_pedido": 10, "confirmar_pedido": 4, "informar": 5}


def _instrucciones() -> dict[str, str]:
    """Cada archivo que lee el LLM: el system prompt, cada skill y cada BR."""
    textos = {"system_prompt": SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")}
    for skill in skill_service.get_skills().values():
        textos[skill.nombre] = skill.instrucciones
        textos.update({f"{skill.nombre}/{id_br}": texto for id_br, texto in skill.br.items()})
    return textos


def _skus() -> list[str]:
    return [product.sku for product in sheets_service.get_catalog()]


# Una marca de cada regla de fondo, y el único archivo donde puede estar
REGLAS = {
    "stock interno": (r"stock es interno", "system_prompt"),
    "pedido confirmado cerrado": (r"confirmado.{0,40}(no se (cambia|modifica)|cerrad|definitiv)", "system_prompt"),
    "sin promesas": (r"no (te comprometas|prometas)", "system_prompt"),
    "una pregunta por mensaje": (r"una (sola )?pregunta (por|a la) (mensaje|vez)", "system_prompt"),
    "nada interno al cliente": (r"SKU.{0,80}son internos", "system_prompt"),
    "no inventar datos": (r"inventes un SKU", "system_prompt"),
}


@pytest.mark.parametrize("regla", REGLAS)
def test_cada_regla_vive_en_un_solo_lugar(regla):
    """CA-103: cada regla de fondo está en un solo archivo de los que lee el LLM."""
    patron, dueno = REGLAS[regla]
    donde = [nombre for nombre, texto in _instrucciones().items() if re.search(patron, texto, re.IGNORECASE)]
    assert donde == [dueno]


def test_instrucciones_sin_mayusculas_enfaticas_ni_cierre_fijo():
    """CA-104: sin palabras en mayúsculas sostenidas ni el cierre que se repetía en la 0.1.1."""
    permitidas = {"SKU", "OK", "CONFIRMAR"}
    for nombre, texto in _instrucciones().items():
        enfaticas = set(re.findall(r"\b[A-ZÁÉÍÓÚÑ]{3,}\b", texto)) - permitidas
        assert not enfaticas, (nombre, enfaticas)
        assert "¿Querés sumar algo más o te confirmo el pedido?" not in texto, nombre


@pytest.mark.parametrize("nombre", sorted(MINIMO_DE_EJEMPLOS))
def test_cada_skill_tiene_ejemplos_que_pasan_el_estilo(nombre):
    """CA-105: ejemplos ilustrativos, con cierres distintos, que pasan los mismos chequeos que el LLM."""
    instrucciones = skill_service.get_skills()[nombre].instrucciones
    assert "## Ejemplos" in instrucciones
    seccion = instrucciones.split("## Ejemplos", 1)[1]
    assert "Muestran el tono, no qué decir" in seccion
    ejemplos = [(m["situacion"], m["respuesta"]) for m in EJEMPLO.finditer(seccion)]
    assert len(ejemplos) >= MINIMO_DE_EJEMPLOS[nombre]

    skus = _skus()
    for situacion, respuesta in ejemplos:
        kind = "sin_stock" if "stock" in situacion.lower() else ""
        largo = "largo" in situacion.lower()
        assert evaluar_agente.chequear_estilo(respuesta, skus=skus, kind=kind, pedido_largo=largo) == [], situacion
    assert not evaluar_agente.cierres_repetidos([respuesta for _, respuesta in ejemplos])


def test_br_de_coincidencia_aproximada_y_observaciones():
    """CA-106: BR-09 pregunta antes de proponer; BR-10 separa observaciones de condiciones."""
    br09 = skill_tools.leer_br("BR-09", ["tomar_pedido"])["texto"]
    assert "Coincidencia aproximada" in br09
    for campo in ("sugerencias", "aproximado", "corregido", "kind = aclaracion", "options", "BR-04"):
        assert campo in br09
    assert "no hay `matches` ni `sugerencias`" in br09
    assert "esta regla no aplica" in br09  # sin los campos del frente A, la búsqueda es la de la 0.1.1

    br10 = skill_tools.leer_br("BR-10", ["tomar_pedido"])["texto"]
    assert "`notes`" in br10 and "`note`" in br10
    assert "Envío" in br10 and "forma de pago" in br10


def test_informar_trae_los_datos_del_negocio():
    """CA-107: `leer_skill` devuelve los datos del negocio solo con la skill que los tiene."""
    informar = skill_tools.leer_skill("informar")
    assert set(informar["datos_negocio"]) == TEMAS_DEL_NEGOCIO
    assert "`datos_negocio`" in skill_service.get_skills()["informar"].instrucciones
    for nombre in ("tomar_pedido", "confirmar_pedido"):
        assert "datos_negocio" not in skill_tools.leer_skill(nombre)


def test_datos_pendientes_figuran_en_validaciones():
    """CA-108: nada se inventa: cada dato sin confirmar figura en V-06."""
    datos = skill_service.get_skills()["informar"].datos_negocio
    v06 = VALIDACIONES.read_text(encoding="utf-8").split("## V-06", 1)[1].split("\n## ", 1)[0]
    for tema, valor in datos.items():
        if valor is None:
            assert f"`{tema}`" in v06, tema


@pytest.mark.parametrize("encabezado", ["horarios: 9", "- horarios", "horarios: [lunes, martes]"])
def test_datos_invalidos_impiden_arrancar(tmp_path, encabezado):
    """CA-108: un dato que no es texto ni null no deja arrancar la aplicación."""
    folder = tmp_path / "mi_skill"
    folder.mkdir()
    (folder / "skill.md").write_text("---\nnombre: mi_skill\nfuncion: x\ncuando: x\ntools: [leer_br]\n---\nTexto")
    (folder / "datos_negocio.md").write_text(f"---\n{encabezado}\n---\nExplicación")
    with pytest.raises(SkillError):
        load_skills(tmp_path)


@pytest.mark.parametrize(
    ("respuesta", "kind", "falla"),
    [
        ("Te armé 2 del SH-PAN-400. ¿Lo confirmo?", "propuesta", "sin_sku"),
        ("Te armé **2 shampoos**. ¿Lo confirmo?", "propuesta", "sin_negrita"),
        ("¿Qué presentación buscás?", "aclaracion", "sin_presentacion"),
        ("¿El de 200 o el de 400? ¿Y cuántos?", "aclaracion", "una_pregunta"),
        ("Listo, hay 24 disponibles. ¿Lo confirmo?", "propuesta", "sin_cantidad_de_stock"),
        ("Tenemos stock: 24. ¿Te lo armo?", "informacion", "sin_cantidad_de_stock"),
        ("Sí, de ese quedan 12. ¿Cuántos querés?", "informacion", "sin_cantidad_de_stock"),
        ("Lo busqué con search_products. ¿Es este?", "aclaracion", "sin_nombres_internos"),
        ("Según la BR-03 te tengo que preguntar. ¿Cuál?", "aclaracion", "sin_nombres_internos"),
        ("Te sumé los items. ¿Lo confirmo?", "propuesta", "sin_ingles"),
        ("¿Qué sizes buscás?", "aclaracion", "sin_ingles"),
    ],
)
def test_chequeos_de_estilo_detectan_cada_falla(respuesta, kind, falla):
    """CA-109: cada chequeo marca lo que un cliente notaría."""
    assert falla in evaluar_agente.chequear_estilo(respuesta, skus=_skus(), kind=kind)


@pytest.mark.parametrize(
    ("respuesta", "kind", "pedido_largo"),
    [
        ("Listo, van los 5 shampoos Pantene de 400. ¿Te lo confirmo así?", "propuesta", False),
        ("De Koleston tengo 6.0, 6.1 y 7.1. ¿Qué tono necesitás?", "aclaracion", False),
        ("De la crema de enjuague me quedan 4, no llego a las 10. ¿Te paso esas 4?", "sin_stock", False),
        ("Me quedan dos dudas: 1) ¿shampoo o crema? 2) ¿de 250 o de 1 kilo?", "propuesta", True),
        ("¡Buen día! ¿Qué te preparo hoy?", "informacion", False),
        ("Te puedo informar que ese dato no lo tengo. ¿Te armo un pedido?", "informacion", False),
    ],
)
def test_chequeos_de_estilo_aceptan_respuestas_buenas(respuesta, kind, pedido_largo):
    """CA-110: una respuesta con buena voz no falla ningún chequeo."""
    assert evaluar_agente.chequear_estilo(respuesta, skus=_skus(), kind=kind, pedido_largo=pedido_largo) == []


def test_pedido_largo_se_reconoce_por_los_renglones():
    assert evaluar_agente.es_pedido_largo("Pantene\n2 sh 400\n1 acond")
    assert not evaluar_agente.es_pedido_largo("2 shampoos pantene 400")


def test_evaluacion_de_varios_turnos_con_modelo_falso(tmp_path):
    """CA-111: manda todos los turnos, evalúa el último, chequea cada respuesta y escribe la conversación."""
    caso = {
        "id": "eval-varios-turnos",
        "descripcion": "Elige la medida en el segundo turno",
        "previo": ["mandame 2 pantene"],
        "mensaje": {"customer_id": "cli-eval", "channel": "telegram", "type": "text", "text": "el de 400"},
        "esperado": {"resultado": "borrador", "items": [{"sku": "SH-PAN-400", "cantidad": 2}]},
    }
    aclarar = _call(
        "respond_to_customer",
        kind="aclaracion",
        message="El shampoo lo tengo en 200 y en 400. ¿Cuál te mando?",
        options=["SH-PAN-200", "SH-PAN-400"],
    )
    proponer = _call(
        "respond_to_customer",
        kind="propuesta",
        message="Listo, van los 2 de 400. ¿Cuál te mando?",  # repite el cierre del turno anterior
        propuesta=[{"sku": "SH-PAN-400", "quantity": 2}],
    )
    modelo = ModeloGuionado(
        _ai(_call("leer_skill", nombre="tomar_pedido")),
        _ai(aclarar),
        _ai(_call("leer_skill", nombre="tomar_pedido")),
        _ai(proponer),
    )
    agent = NormaAgent(modelo, InMemorySaver())

    fila, conversacion = evaluar_agente._evaluar(agent, caso, _skus())

    assert fila["resultado"] == "correcto"
    assert fila["turnos"] == 2
    assert fila["estilo"] == "cierre_variado"
    assert [turno["cliente"] for turno in conversacion] == ["mandame 2 pantene", "el de 400"]
    assert [turno["kind"] for turno in conversacion] == ["aclaracion", "propuesta"]
    # El chequeo mira solo lo que escribió el LLM: el detalle con precios lo agrega el sistema
    assert conversacion[1]["agente"] == "Listo, van los 2 de 400. ¿Cuál te mando?"
    assert "Total estimado" in conversacion[1]["respuesta"]

    salida = tmp_path / "conversaciones.md"
    evaluar_agente.escribir_conversaciones(salida, [caso], [fila], [conversacion], agent.prompt_version)
    texto = salida.read_text(encoding="utf-8")
    assert "## eval-varios-turnos" in texto
    assert "> **Cliente:** el de 400" in texto
    assert "**Norma** (`aclaracion`)" in texto
    assert "cierre_variado" in texto
