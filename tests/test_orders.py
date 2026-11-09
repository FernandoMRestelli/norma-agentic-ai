import ipaddress
from datetime import datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from internal.schemas.catalog_schema import StockCheck
from internal.schemas.order_schema import (
    MAX_QUANTITY,
    Channel,
    IncomingMessage,
    MessageType,
    Order,
    OrderDraft,
    OrderItem,
)
from internal.services.validation_service import build_order_message, build_summary


def make_draft(**overrides) -> OrderDraft:
    data = {
        "draft_id": "d-1",
        "customer_id": "c-1",
        "channel": Channel.WEB,
        "items": [OrderItem(sku="SH-400", product_name="Shampoo Pantene", presentation="400 ml", quantity=5)],
    }
    data.update(overrides)
    return OrderDraft(**data)


def test_draft_requires_items():
    with pytest.raises(ValidationError):
        make_draft(items=[])


def test_item_quantity_must_be_positive():
    with pytest.raises(ValidationError):
        OrderItem(sku="SH-400", product_name="Shampoo Pantene", quantity=0)


@pytest.mark.parametrize(
    "item_kwargs",
    [
        {"sku": "", "product_name": "Shampoo Pantene"},
        {"sku": "   ", "product_name": "Shampoo Pantene"},
        {"sku": "SH-400", "product_name": ""},
        {"sku": "SH-400", "product_name": "   "},
    ],
)
def test_item_rechaza_sku_y_nombre_vacios(item_kwargs):
    """RF-08: cada línea debe tener un SKU y un nombre de producto válidos no vacíos."""
    with pytest.raises(ValidationError):
        OrderItem(quantity=1, **item_kwargs)


def test_item_cantidad_tiene_tope():
    """RNF-02: cantidad tiene tope en MAX_QUANTITY para evitar números absurdos del LLM."""
    item = OrderItem(sku="SH-400", product_name="Shampoo Pantene", quantity=MAX_QUANTITY)
    assert item.quantity == MAX_QUANTITY

    with pytest.raises(ValidationError):
        OrderItem(sku="SH-400", product_name="Shampoo Pantene", quantity=MAX_QUANTITY + 1)


@pytest.mark.parametrize("customer_id", ["", "   "])
def test_customer_id_no_puede_ser_vacio(customer_id):
    """customer_id no puede ser vacío ni consistir únicamente de espacios en blanco."""
    with pytest.raises(ValidationError):
        IncomingMessage(customer_id=customer_id, channel=Channel.WEB, text="hola")

    with pytest.raises(ValidationError):
        OrderDraft(
            draft_id="d-1",
            customer_id=customer_id,
            channel=Channel.WEB,
            items=[OrderItem(sku="SH-400", product_name="Shampoo Pantene", quantity=1)],
        )

    with pytest.raises(ValidationError):
        Order(
            order_id="ORD-0001",
            draft_id="d-1",
            created_at=datetime.now(),
            customer_id=customer_id,
            channel=Channel.WEB,
            items=[OrderItem(sku="SH-400", product_name="Shampoo Pantene", quantity=1)],
        )


def test_item_y_mensaje_recortan_espacios():
    """Recorta espacios en blanco en campos de texto y claves."""
    item = OrderItem(sku="  SH-400  ", product_name="  Shampoo Pantene  ", quantity=2)
    assert item.sku == "SH-400"
    assert item.product_name == "Shampoo Pantene"

    msg = IncomingMessage(customer_id="  c-1  ", channel=Channel.WEB, text="hola")
    assert msg.customer_id == "c-1"


def test_summary_lists_items():
    summary = build_summary(make_draft())
    assert "5 x Shampoo Pantene 400 ml" in summary


def test_summary_includes_prices_and_total():
    """D-04: el resumen muestra precio unitario y total estimado cuando están disponibles."""
    draft = make_draft(
        items=[
            OrderItem(
                sku="SH-400",
                product_name="Shampoo Pantene",
                presentation="400 ml",
                quantity=2,
                unit_price=Decimal("100.50"),
            ),
            OrderItem(
                sku="AC-400",
                product_name="Acondicionador Pantene",
                presentation="400 ml",
                quantity=1,
                unit_price=Decimal("200.00"),
            ),
        ]
    )
    summary = build_summary(draft)
    assert "2 x Shampoo Pantene 400 ml ($100.50 c/u = $201.00)" in summary
    assert "1 x Acondicionador Pantene 400 ml ($200.00)" in summary
    assert "Total estimado: $401.00" in summary


def test_order_message_includes_prices_and_total():
    """D-04: el mensaje de orden registrada muestra precios y total confirmado."""
    order = Order(
        order_id="ORD-0001",
        draft_id="d-1",
        created_at=datetime.now(),
        customer_id="c-1",
        channel=Channel.WEB,
        items=[
            OrderItem(
                sku="SH-400",
                product_name="Shampoo Pantene",
                presentation="400 ml",
                quantity=3,
                unit_price=Decimal("150.00"),
            ),
        ],
    )
    msg = build_order_message(order)
    assert "Listo, pedido hecho:" in msg
    assert "3 x Shampoo Pantene 400 ml ($150.00 c/u = $450.00)" in msg
    assert "Total: $450.00" in msg
    assert "Tu número es ORD-0001." in msg


def test_summary_and_order_message_sin_precios_o_precio_cero():
    """D-04, RF-11: si los items no tienen precio (None) o es $0, no se muestran paréntesis de precio ni total."""
    draft = make_draft(
        items=[
            OrderItem(
                sku="SH-400",
                product_name="Shampoo Pantene",
                presentation="400 ml",
                quantity=2,
                unit_price=None,
            ),
            OrderItem(
                sku="AC-400",
                product_name="Acondicionador Pantene",
                presentation="400 ml",
                quantity=1,
                unit_price=Decimal("0"),
            ),
        ]
    )
    summary = build_summary(draft)
    assert "• 2 x Shampoo Pantene 400 ml" in summary
    assert "• 1 x Acondicionador Pantene 400 ml" in summary
    assert "c/u" not in summary
    assert "$" not in summary
    assert "Total estimado:" not in summary

    order = Order(
        order_id="ORD-0002",
        draft_id="d-2",
        created_at=datetime.now(),
        customer_id="c-1",
        channel=Channel.WEB,
        items=draft.items,
    )
    msg = build_order_message(order)
    assert "• 2 x Shampoo Pantene 400 ml" in msg
    assert "• 1 x Acondicionador Pantene 400 ml" in msg
    assert "c/u" not in msg
    assert "Total:" not in msg
    assert "Tu número es ORD-0002." in msg


def test_summary_and_order_message_precios_mixtos():
    """D-04, RF-11: si solo algunos items tienen precio > 0, se detallan y el total suma solo los válidos."""
    items = [
        OrderItem(
            sku="SH-400",
            product_name="Shampoo Pantene",
            presentation="400 ml",
            quantity=2,
            unit_price=Decimal("150.00"),
        ),
        OrderItem(
            sku="AC-400",
            product_name="Acondicionador Pantene",
            presentation="400 ml",
            quantity=1,
            unit_price=None,
        ),
    ]
    draft = make_draft(items=items)
    summary = build_summary(draft)
    assert "• 2 x Shampoo Pantene 400 ml ($150.00 c/u = $300.00)" in summary
    assert "• 1 x Acondicionador Pantene 400 ml" in summary
    assert "Total estimado: $300.00" in summary

    order = Order(
        order_id="ORD-0003",
        draft_id="d-3",
        created_at=datetime.now(),
        customer_id="c-1",
        channel=Channel.WEB,
        items=items,
    )
    msg = build_order_message(order)
    assert "• 2 x Shampoo Pantene 400 ml ($150.00 c/u = $300.00)" in msg
    assert "• 1 x Acondicionador Pantene 400 ml" in msg
    assert "Total: $300.00" in msg


def test_unit_price_is_decimal():
    item = OrderItem(sku="SH-400", product_name="Shampoo Pantene", quantity=3, unit_price="0.10")
    assert item.unit_price == Decimal("0.10")
    assert item.unit_price * 3 == Decimal("0.30")


def test_order_draft_version():
    """C-13 / CA-14: un OrderDraft nace con version=1; version < 1 es inválido."""
    draft = make_draft()
    assert draft.version == 1

    draft_v2 = make_draft(version=2)
    assert draft_v2.version == 2

    with pytest.raises(ValidationError):
        make_draft(version=0)


def test_order_sheets_synced_default():
    """RF-19 / RNF-04: una Order nace con sheets_synced=False por defecto."""
    order = Order(
        order_id="ORD-0001",
        draft_id="d-1",
        created_at=datetime.now(),
        customer_id="c-1",
        channel=Channel.WEB,
        items=[OrderItem(sku="SH-400", product_name="Shampoo Pantene", quantity=1)],
    )
    assert order.sheets_synced is False


@pytest.mark.parametrize(
    "data",
    [
        {"type": MessageType.TEXT},
        {"type": MessageType.TEXT, "text": "   "},
        {"type": MessageType.AUDIO},
        {"type": MessageType.IMAGE, "text": "foto"},
    ],
)
def test_incoming_message_requires_content_for_its_type(data):
    with pytest.raises(ValidationError):
        IncomingMessage(customer_id="c-1", channel=Channel.WHATSAPP, **data)


@pytest.mark.parametrize(
    "media_url",
    [
        "file:///etc/passwd",
        "http://example.com/audio.ogg",
        "https://localhost/audio.ogg",
        "https://localhost./audio.ogg",
        "https://foo.localhost/audio.ogg",
        "https://localhost:8443/audio.ogg",
        "https://127.0.0.1/audio.ogg",
        "https://127.0.0.1:8000/audio.ogg",
        "https://127.1/audio.ogg",
        f"https://{int(ipaddress.IPv4Address('127.0.0.1'))}/audio.ogg",
        "https://0x7f000001/audio.ogg",
        "https://0x7f.0.0.1/audio.ogg",
        "https://017700000001/audio.ogg",
        "https://0177.0.0.1/audio.ogg",
        "https://169.254.169.254/latest/meta-data/",
        f"https://{int(ipaddress.IPv4Address('169.254.169.254'))}/latest/meta-data/",
        "https://metadata.google.internal/computeMetadata/v1/",
        "https://service.local/audio.ogg",
        "https://backend.internal/audio.ogg",
        "https://10.0.0.5/audio.ogg",
        "https://192.168.1.1/image.jpg",
        "https://172.16.0.1/image.jpg",
        "https://0.0.0.0/audio.ogg",
        "https://4294967296/audio.ogg",
        "https://[::1]/audio.ogg",
        "x",
        "ftp://example.com/audio.ogg",
        "https://" + "a" * 2050 + ".com",
    ],
)
def test_incoming_message_rejects_insecure_media_url(media_url: str):
    """CA-04, RNF-02: rechaza URLs que no sean HTTPS o apunten a direcciones locales/privadas (anti-SSRF)."""
    with pytest.raises(ValidationError):
        IncomingMessage(
            customer_id="c-1",
            channel=Channel.WEB,
            type=MessageType.AUDIO,
            media_url=media_url,
        )


@pytest.mark.parametrize(
    "media_url",
    [
        "https://example.com/audio.ogg",
        "https://cdn.norma.com.ar/images/prod.jpg",
        "https://sub.domain.co.uk:8443/path/file.ogg?token=123#frag",
        "https://8.8.8.8/audio.mp3",
        "https://deadbeef.de/image.jpg",
    ],
)
def test_incoming_message_accepts_valid_media_url(media_url: str):
    """CA-04, RNF-02: acepta URLs HTTPS con dominios públicos válidos."""
    msg = IncomingMessage(
        customer_id="c-1",
        channel=Channel.WEB,
        type=MessageType.AUDIO,
        media_url=media_url,
    )
    assert msg.media_url == media_url


def test_incoming_message_accepts_telegram_reference_in_telegram():
    """CA-33: en Telegram, media_url guarda la referencia al archivo y no la URL de descarga, que tiene el token."""
    msg = IncomingMessage(
        customer_id="telegram:1",
        channel=Channel.TELEGRAM,
        type=MessageType.AUDIO,
        media_url="telegram:AwACAgEAAxk-Bc_1",
    )
    assert msg.media_url == "telegram:AwACAgEAAxk-Bc_1"


@pytest.mark.parametrize("channel", [Channel.WEB, Channel.WHATSAPP])
def test_incoming_message_rejects_telegram_reference_in_other_channels(channel: Channel):
    """CA-04, CA-33: la referencia de Telegram es una excepción solo de ese canal."""
    with pytest.raises(ValidationError):
        IncomingMessage(customer_id="c-1", channel=channel, type=MessageType.AUDIO, media_url="telegram:AUDIO1")


@pytest.mark.parametrize(
    "media_url",
    [
        "telegram:",
        "telegram:con espacio",
        "telegram://169.254.169.254/latest/meta-data/",
        "telegram:" + "a" * 2050,
        "file:///etc/passwd",
        "https://169.254.169.254/latest/meta-data/",
    ],
)
def test_incoming_message_telegram_keeps_rejecting_insecure_media_url(media_url: str):
    """CA-04, CA-33: la excepción cubre solo `telegram:<file_id>`; el resto sigue con la validación anti-SSRF."""
    with pytest.raises(ValidationError):
        IncomingMessage(customer_id="telegram:1", channel=Channel.TELEGRAM, type=MessageType.AUDIO, media_url=media_url)


def test_stock_check_serializes_is_enough():
    assert StockCheck(sku="SH-400", requested=5, available=3).model_dump()["is_enough"] is False
