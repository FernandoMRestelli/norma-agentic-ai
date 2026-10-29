import pytest
from pydantic import ValidationError

from backend.schemas.order_schema import Channel, OrderDraft, OrderItem
from backend.services.validation_service import build_summary


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


def test_summary_lists_items():
    summary = build_summary(make_draft())
    assert "5 x Shampoo Pantene 400 ml" in summary
