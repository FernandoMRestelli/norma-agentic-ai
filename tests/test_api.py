import hashlib
import hmac

import pytest
from fastapi.testclient import TestClient

from backend.config import get_settings
from backend.main import app

client = TestClient(app)


@pytest.fixture
def whatsapp_settings(monkeypatch):
    """Configura WhatsApp para el test y limpia la caché de settings aunque el test falle."""
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "secreto")
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "app-secret")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def sign(body: bytes, secret: str = "app-secret") -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_whatsapp_verify(whatsapp_settings):
    params = {"hub.mode": "subscribe", "hub.verify_token": "secreto", "hub.challenge": "123"}
    response = client.get("/webhook/whatsapp", params=params)
    assert response.status_code == 200
    assert response.text == "123"

    params["hub.verify_token"] = "otro"
    assert client.get("/webhook/whatsapp", params=params).status_code == 403


def test_whatsapp_post_accepts_valid_signature(whatsapp_settings):
    body = b'{"entry": []}'
    response = client.post("/webhook/whatsapp", content=body, headers={"X-Hub-Signature-256": sign(body)})
    assert response.status_code == 200


@pytest.mark.parametrize("headers", [{}, {"X-Hub-Signature-256": sign(b'{"entry": []}', "otro-secret")}])
def test_whatsapp_post_rejects_invalid_signature(whatsapp_settings, headers):
    response = client.post("/webhook/whatsapp", content=b'{"entry": []}', headers=headers)
    assert response.status_code == 403


def test_chat_rejects_text_message_without_text():
    response = client.post("/chat/messages", json={"customer_id": "c-1", "channel": "web", "text": "  "})
    assert response.status_code == 422


def test_resolve_draft_requires_version():
    """CA-14: POST /chat/drafts/{id}/confirmar requiere query param version (ge=1)."""
    # Sin version -> 422
    response_no_ver = client.post("/chat/drafts/d-1/confirmar")
    assert response_no_ver.status_code == 422

    # version=0 -> 422
    response_v0 = client.post("/chat/drafts/d-1/confirmar?version=0")
    assert response_v0.status_code == 422

    # version=1 -> 501 (endpoint todavía no implementado, pero valida esquema)
    response_valid = client.post("/chat/drafts/d-1/confirmar?version=1")
    assert response_valid.status_code == 501


def test_chat_rejects_insecure_media_url():
    """CA-04, RNF-02: POST /chat/messages con media_url insegura responde 422."""
    response = client.post(
        "/chat/messages",
        json={
            "customer_id": "c-1",
            "channel": "web",
            "type": "audio",
            "media_url": "file:///etc/passwd",
        },
    )
    assert response.status_code == 422

