"""Bot de Telegram por long polling, para desarrollo y la beta: python -m backend.telegram_polling

No necesita URL pública. Un solo proceso por token: si otro ya hace polling con el mismo bot, Telegram responde
409 Conflict (para desarrollar en paralelo, cada uno usa su propio bot). Ctrl+C para salir.
"""

import logging
import time

from backend.config import get_settings
from backend.services import telegram_service

logger = logging.getLogger("backend.telegram_polling")

POLL_SECONDS = 25
RETRY_SECONDS = 5


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # loguea cada URL, y la URL tiene el token

    client = telegram_service.get_client()
    bot = client.get_me()
    client.delete_webhook()  # con un webhook configurado, getUpdates no entrega nada
    allowed = get_settings().telegram_allowed_ids
    logger.info("Bot @%s escuchando, %d usuarios en la lista blanca", bot["username"], len(allowed))
    if not allowed:
        logger.warning("TELEGRAM_ALLOWED_USER_IDS está vacío: el bot solo va a responder con el id de quien escriba")

    offset = None
    while True:
        try:
            updates = client.get_updates(offset, POLL_SECONDS)
        except telegram_service.TelegramError as exc:
            logger.warning("%s; reintento en %d s", exc, RETRY_SECONDS)
            time.sleep(RETRY_SECONDS)
            continue
        for update in updates:
            offset = update["update_id"] + 1
            telegram_service.handle_update(update, client)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except (ValueError, telegram_service.TelegramError) as exc:
        raise SystemExit(f"❌ {exc}") from None
