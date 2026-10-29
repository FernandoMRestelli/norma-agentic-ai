from backend.schemas.order_schema import Channel, IncomingMessage
from backend.services.message_service import prepare


def test_prepare_text_normalizes_whitespace():
    message = IncomingMessage(customer_id="c-1", channel=Channel.WEB, text="  5 shampoos   Pantene\n por favor ")
    assert prepare(message) == "5 shampoos Pantene por favor"


# TODO: casos de interpretation_service (cantidades en palabras, presentaciones, productos ambiguos)
