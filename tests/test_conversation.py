import pytest

from backend.schemas.order_schema import Channel, IncomingMessage, MessageType
from backend.services import conversation_service


@pytest.mark.parametrize("tipo", [MessageType.AUDIO, MessageType.IMAGE])
def test_audio_e_imagen_piden_el_pedido_escrito(tipo):
    """CA-33 / RF-32: sin modelo de visión, audio e imagen reciben el pedido de escribirlo."""
    message = IncomingMessage(customer_id="telegram:1", channel=Channel.TELEGRAM, type=tipo, media_url="telegram:X")
    reply = conversation_service.handle_message(message)
    assert reply.text == conversation_service.WRITE_IT_PLEASE
    assert reply.draft_id is None
