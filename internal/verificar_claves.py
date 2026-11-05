"""Verifica que las claves de .env funcionen: python -m internal.verificar_claves

Llama a Telegram (getMe) y al LLM con un mensaje mínimo. No muestra ninguna clave.
"""

import logging

from internal.agent.llm import get_chat_model
from internal.config import get_settings
from internal.services import telegram_service


def check_telegram() -> bool:
    try:
        bot = telegram_service.get_client().get_me()
        allowed = get_settings().telegram_allowed_ids  # falla si hay algo que no es un número
    except (ValueError, telegram_service.TelegramError) as exc:
        print(f"❌ Telegram: {exc}")
        return False
    print(f"✅ Telegram: bot @{bot['username']}, {len(allowed)} usuarios en la lista blanca")
    if not allowed:
        print("   Falta TELEGRAM_ALLOWED_USER_IDS: escribile al bot con el polling andando y te dice tu id")
    return True


def check_llm() -> bool:
    try:
        response = get_chat_model().invoke("Respondé solo con la palabra OK.")
    except Exception as exc:  # cada proveedor lanza sus propios errores
        print(f"❌ LLM: {exc}")
        return False
    settings = get_settings()
    print(f"✅ LLM: {settings.llm_provider} / {settings.llm_model} respondió «{response.text.strip()}»")
    return True


def main() -> int:
    logging.getLogger("httpx").setLevel(logging.WARNING)
    results = [check_telegram(), check_llm()]
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
