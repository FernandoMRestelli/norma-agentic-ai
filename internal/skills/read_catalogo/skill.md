---
nombre: read_catalogo
cuando: El cliente pide o consulta un producto
tools: [search_products, get_product, check_stock, respond_to_customer]
---
1. Buscá cada producto con `search_products`.
2. Si pidió una cantidad, verificala con `check_stock`.
3. Si no alcanza el stock, leé BR-01 antes de responder.
4. Si alcanza, respondé con `kind = propuesta` y los productos en `propuesta`, preguntando si le armás el pedido.
