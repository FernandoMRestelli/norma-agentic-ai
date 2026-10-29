"""Tools de borrador que el agente puede invocar.

El agente solo crea o modifica BORRADORES. No existe una tool para confirmar:
la confirmación la dispara el cliente y la procesa order_service en el backend.
"""

from typing import List, Optional

from backend.schemas.order_schema import OrderDraft, OrderItem


def create_order_draft(customer_id: str, items: List[OrderItem], notes: Optional[str] = None) -> OrderDraft:
    """Crea un borrador validando cada SKU contra el catálogo."""
    raise NotImplementedError


def update_order_draft(draft_id: str, items: List[OrderItem], notes: Optional[str] = None) -> OrderDraft:
    """Reemplaza las líneas de un borrador pendiente tras una corrección del cliente."""
    raise NotImplementedError
