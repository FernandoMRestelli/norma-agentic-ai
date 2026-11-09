---
nombre: confirmar_pedido
cuando: Hay una propuesta vigente y el cliente la acepta, la rechaza o duda
tools: [armar_pedido, respond_to_customer]
requiere_propuesta: true
---
1. Leé BR-06, salvo que el cliente solo haya aceptado, sin agregar nada más.
2. Si aceptó, llamá a `armar_pedido`. No recibe productos: registra la propuesta vigente, y el sistema le responde al cliente con el detalle y el número.
3. Si no aceptó, respondé con `respond_to_customer` como indica BR-06, sin armar nada.
4. Si `armar_pedido` devuelve `stock_insuficiente`, leé la skill `tomar_pedido` y su BR-01, y ofrecé lo que queda.
