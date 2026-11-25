"""Sensor de calidad en uso (RF-123): números y códigos de las trazas y de la base, sin texto."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from internal import sensor_calidad
from internal.config import get_settings
from internal.database.connection import SessionLocal
from internal.database.models import DraftRecord
from tests import test_observabilidad as obs
from tests.test_observabilidad import catalogo, configuracion_limpia, mlflow_local, tracking_uri  # noqa: F401


def _turno(kind="propuesta", **extra):
    return {"kind": kind, "iteraciones": 2, "latencia_ms": 5000, "tokens_entrada": 900, "tokens_salida": 100, **extra}


def test_medir_turnos():
    """CA-212: porcentajes sobre turnos, cada turno una vez aunque tenga dos rescates iguales."""
    turnos = [
        _turno(),
        _turno("error", errores=["tool_no_permitida"], iteraciones=9),
        _turno(rescates=["tolerado:opcion_sin_elegir", "tolerado:cantidad_distinta"]),
        _turno(rescates=["mensaje_fijo"], errores=["aceptacion_no_clara", "aceptacion_no_clara"]),
    ]
    medidas = sensor_calidad.medir_turnos(turnos, (1.0, 10.0))

    assert (medidas["turnos"], medidas["error_pct"], medidas["tolerado_pct"]) == (4, 25.0, 25.0)
    assert (medidas["mensaje_fijo_pct"], medidas["aceptacion_no_clara_pct"], medidas["cierre_pct"]) == (25.0, 25.0, 0.0)
    assert (medidas["iteraciones_p50"], medidas["iteraciones_p95"]) == (2, 9)
    assert (medidas["tokens_por_turno"], medidas["costo_usd"]) == (1000, 0.0076)
    assert medidas["errores"] == {"aceptacion_no_clara": 2, "tool_no_permitida": 1}
    assert sensor_calidad.medir_turnos([])["error_pct"] == 0.0


def test_medir_pedidos():
    """CA-212: las versiones de más de los pedidos confirmados son correcciones; las de los descartados no cuentan."""
    drafts = [("a", 2, "confirmado"), ("a", 1, "reemplazado"), ("b", 1, "confirmado"),
              ("c", 1, "reemplazado"), ("c", 2, "reemplazado"), ("c", 3, "descartado")]  # fmt: skip
    medidas = sensor_calidad.medir_pedidos(drafts, ["Recibido", "Cancelado"])
    assert medidas == {"propuestas": 3, "confirmadas_pct": 66.7, "correcciones_por_pedido": 0.5, "pedidos": 2,
                       "cancelados_pct": 50.0}  # fmt: skip
    assert sensor_calidad.medir_pedidos([], [])["correcciones_por_pedido"] == 0.0


def test_avisos():
    """CA-212: avisa lo que pasa el umbral, y solo con suficientes turnos o pedidos de lo que mide."""
    malas = {"turnos": 20, "error_pct": 30.0, "pedidos": 3, "cancelados_pct": 100.0}
    assert sensor_calidad.avisos(malas) == ["error_pct 30.0 > 5.0"]
    assert sensor_calidad.avisos({**malas, "turnos": 19}) == []
    assert sensor_calidad.avisos({**malas, "pedidos": 20})[-1] == "cancelados_pct 100.0 > 10.0"
    assert sensor_calidad.avisos({"turnos": 50, "error_pct": 5.0}) == []  # llegar al umbral no es pasarlo


def test_lee_las_trazas_de_mlflow(mlflow_local):  # noqa: F811
    """CA-213: los atributos de los turnos, de punta a punta con MLflow en un SQLite local y sin texto."""
    get_settings.cache_clear()
    antes = datetime.now(UTC) - timedelta(minutes=5)
    previos = len(sensor_calidad.leer_turnos(antes))
    obs._turno()
    obs._traza(mlflow_local)

    turnos = sensor_calidad.leer_turnos(antes)

    assert len(turnos) == previos + 1
    nuevo = next(t for t in turnos if t["kind"] == "sin_stock")
    assert (nuevo["iteraciones"], nuevo["tokens_entrada"]) == (4, 4800)
    assert nuevo["errores"] == ["tool_no_permitida"] and "rescates" in nuevo
    assert obs.TEXTO_CLIENTE not in str(turnos) and "secreto-77" not in str(turnos)
    assert sensor_calidad.leer_turnos(datetime.now(UTC) + timedelta(days=1)) == []


def test_sin_experimento_no_lo_crea(mlflow_local, monkeypatch):  # noqa: F811
    """CA-213: el sensor solo lee."""
    monkeypatch.setenv("MLFLOW_EXPERIMENT", "no-existe")
    get_settings.cache_clear()
    assert sensor_calidad.leer_turnos(datetime.now(UTC)) is None
    assert mlflow_local.get_experiment_by_name("no-existe") is None


@pytest.mark.parametrize(
    ("turnos", "salida"),
    [([_turno()] * 20, 0), ([_turno("error")] * 20, 1), ([_turno("error")] * 3, 0), (None, 2)],
)
def test_codigo_de_salida(monkeypatch, capsys, turnos, salida):
    """CA-213: sirve para correrlo en un cron: 1 si hay avisos, 2 si no pudo leer las trazas."""
    monkeypatch.setattr(sensor_calidad, "leer_turnos", lambda desde: turnos)
    monkeypatch.setattr(sensor_calidad, "leer_pedidos", lambda desde: sensor_calidad.medir_pedidos([], []))
    assert sensor_calidad.main(["--dias", "1", "--precio", "0.1,0.5"]) == salida
    assert ("⚠️" in capsys.readouterr().out) is (salida == 1)


def test_lee_la_base():
    """CA-213: las propuestas desde una fecha, sin su cliente ni sus notas."""
    with SessionLocal() as session:
        session.add(DraftRecord(draft_id="sensor-1", version=1, customer_id="c", channel="telegram", items=[],
                                notes="nota", status="confirmado"))  # fmt: skip
        session.commit()
    try:
        medidas = sensor_calidad.leer_pedidos(datetime.now(UTC) - timedelta(minutes=1))
        assert medidas["propuestas"] >= 1 and "nota" not in str(medidas)
        assert sensor_calidad.leer_pedidos(datetime.now(UTC) + timedelta(days=1))["propuestas"] == 0
    finally:
        with SessionLocal() as session:
            session.query(DraftRecord).filter_by(draft_id="sensor-1").delete()
            session.commit()


def test_una_base_sin_tablas_no_corta(monkeypatch, tmp_path, capsys):
    """CA-213: el sensor no migra la base; las medidas de los turnos salen igual."""
    motor = create_engine(f"sqlite:///{tmp_path / 'vacia.db'}")
    monkeypatch.setattr(sensor_calidad, "SessionLocal", sessionmaker(bind=motor))
    assert sensor_calidad.leer_pedidos(datetime.now(UTC))["propuestas"] == 0
    assert "No se pudo leer la base" in capsys.readouterr().out
