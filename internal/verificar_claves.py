"""Verifica que las claves de .env funcionen: python -m internal.verificar_claves

Llama a Telegram (getMe), al LLM con un mensaje mínimo y, si están configuradas, a las planillas de Google
(Sheet 1 y Sheet 2). No muestra ninguna clave ni datos de las planillas: solo cuántas filas leyó.
"""

import logging

from internal.agent.llm import get_chat_model
from internal.config import get_settings
from internal.services import google_sheets_client, sheets_service, telegram_service


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


def _google_hint(exc: Exception, permiso: str) -> str:
    """El error y qué hacer, con el correo de la cuenta de servicio si se pudo crear el cliente."""
    texto = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
    try:
        email = google_sheets_client.get_client().service_account_email
    except Exception:
        return f"{texto}. Revisá GOOGLE_CREDENTIALS_FILE"
    return f"{texto}. Compartí la planilla como {permiso} con {email}"


def check_catalog_sheet() -> bool:
    if not sheets_service.catalog_from_sheet():
        print("⏭️  Sheet 1: sin CATALOG_SHEET_ID, el catálogo sale de CATALOG_FILE")
        return True
    try:
        products = sheets_service.read_catalog_sheet()
    except Exception as exc:  # gspread, google-auth y la red lanzan sus propios errores
        print(f"❌ Sheet 1 (catálogo): {_google_hint(exc, 'Lector')}")
        return False
    print(f"✅ Sheet 1 (catálogo): productos leídos: {len(products)}")
    return True


def check_orders_sheet() -> bool:
    if not sheets_service.orders_to_sheet():
        print("⏭️  Sheet 2: sin ORDERS_SHEET_ID, los pedidos van solo a ORDERS_FILE")
        return True
    try:
        count = sheets_service.check_orders_sheet()
    except Exception as exc:
        print(f"❌ Sheet 2 (pedidos): {_google_hint(exc, 'Editor')}")
        return False
    print(f"✅ Sheet 2 (pedidos): se puede leer y editar; pedidos cargados: {count}")
    return True


def main() -> int:
    logging.getLogger("httpx").setLevel(logging.WARNING)
    results = [check_telegram(), check_llm(), check_catalog_sheet(), check_orders_sheet()]
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
