---
nombre: armar_pedido
cuando: Hay una propuesta vigente y el cliente responde sobre ella
tools: [armar_pedido, respond_to_customer]
---
1. Leé BR-06 si no está claro que el cliente aceptó.
2. Si aceptó, llamá a `armar_pedido`. No recibe productos: registra la propuesta vigente.
3. Si no aceptó, respondé con `respond_to_customer` sin armar nada.
