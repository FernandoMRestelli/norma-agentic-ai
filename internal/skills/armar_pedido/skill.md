---
nombre: armar_pedido
cuando: Hay una propuesta vigente y el cliente la acepta, la rechaza o duda
tools: [armar_pedido, respond_to_customer]
---
1. Leé BR-06 si no está claro que el cliente aceptó.
2. Si aceptó, llamá a `armar_pedido`. No recibe productos: registra la propuesta vigente, y el sistema le responde al cliente con el detalle y el número.
3. Si no aceptó, respondé con `respond_to_customer` como indica BR-06, sin armar nada.
4. Si `armar_pedido` devuelve `stock_insuficiente`, leé la skill `read_catalogo` y su BR-01, y ofrecé lo que queda.
