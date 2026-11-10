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
