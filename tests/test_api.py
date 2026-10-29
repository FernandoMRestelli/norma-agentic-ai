from fastapi.testclient import TestClient

from backend.config import get_settings
from backend.main import app

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_whatsapp_verify(monkeypatch):
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "secreto")
    get_settings.cache_clear()
    params = {"hub.mode": "subscribe", "hub.verify_token": "secreto", "hub.challenge": "123"}
    response = client.get("/webhook/whatsapp", params=params)
    assert response.status_code == 200
    assert response.text == "123"

    params["hub.verify_token"] = "otro"
    assert client.get("/webhook/whatsapp", params=params).status_code == 403
    get_settings.cache_clear()
