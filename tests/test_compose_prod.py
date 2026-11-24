"""El compose de producción levanta la API y el bot con la imagen de la release (CONTRIBUTING.md#producción)."""

from pathlib import Path

import pytest
import yaml

COMPOSE = Path(__file__).resolve().parents[1] / "docker-compose.prod.yml"

# Adentro del contenedor de desarrollo (`docker compose run --rm api pytest`) el compose no está montado
pytestmark = pytest.mark.skipif(not COMPOSE.exists(), reason="docker-compose.prod.yml no está en este entorno")


def _servicios() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]


def test_el_bot_corre_la_misma_imagen_que_la_api():
    servicios = _servicios()
    assert servicios["bot"]["image"] == servicios["api"]["image"]
    assert "${VERSION" in servicios["bot"]["image"]  # nunca una imagen sin versión fija


def test_el_bot_usa_polling_y_no_publica_puertos():
    bot = _servicios()["bot"]
    assert bot["command"] == ["python", "-m", "internal.telegram_polling"]
    assert "ports" not in bot
    assert bot["restart"] == "unless-stopped"


def test_el_bot_comparte_datos_y_entorno_con_la_api():
    servicios = _servicios()
    api, bot = servicios["api"], servicios["bot"]
    assert "./data:/app/data" in bot["volumes"]
    assert set(api["volumes"]) == set(bot["volumes"])
    assert bot["environment"] == api["environment"]
    assert bot["environment"]["APP_ENV"] == "production"


def test_produccion_usa_el_catalogo_real_y_traza_sin_texto():
    """CA-167: el compose fija el catálogo de data/ y las trazas sin el texto del cliente, diga lo que diga el .env."""
    entorno = _servicios()["bot"]["environment"]
    assert entorno["CATALOG_FILE"] == "data/catalogo.csv"
    assert entorno["MLFLOW_ENABLED"] == "true"
    assert entorno["MLFLOW_TRACKING_URI"] == "http://mlflow:5000"
    assert entorno["MLFLOW_TRACE_TEXT"] == "false"


def test_mlflow_se_publica_solo_en_la_maquina_y_no_ve_los_datos():
    """CA-167: MLflow no tiene login: solo 127.0.0.1, con versión fija y sin acceso a data/ ni a credentials/."""
    mlflow = _servicios()["mlflow"]
    assert all(puerto.startswith("127.0.0.1:") for puerto in mlflow["ports"])
    assert mlflow["volumes"] == ["./mlflow:/mlflow"]
    assert not mlflow["image"].endswith(":latest")
    assert "depends_on" not in mlflow and "mlflow" not in _servicios()["bot"]["depends_on"]


def test_la_imagen_lleva_la_pagina_del_dashboard():
    """Sin frontend/ en la imagen, /dashboard/ui responde 404 en producción."""
    raiz = COMPOSE.parent
    assert "COPY frontend/ frontend/" in (raiz / "Dockerfile").read_text(encoding="utf-8")
    assert "frontend/" not in (raiz / ".dockerignore").read_text(encoding="utf-8").splitlines()
