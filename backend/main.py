"""Punto de entrada de la API. Ejecutar con: uvicorn backend.main:app --reload"""

from fastapi import FastAPI

from backend.api import chat, whatsapp_webhook

app = FastAPI(title="Norma Agentic AI", version="0.1.0")

app.include_router(chat.router)
app.include_router(whatsapp_webhook.router)


@app.get("/health", tags=["health"])
def health() -> dict:
    return {"status": "ok"}
