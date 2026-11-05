---
nombre: corregir_pedido
cuando: Hay una propuesta vigente y el cliente quiere cambiar una cantidad o un producto, sumar o sacar algo
tools: [respond_to_customer]
---
1. Partí de la propuesta vigente del estado actual y leé BR-05.
2. Si suma o cambia un producto, buscalo con `search_products`. Si cambia solo una cantidad, verificala con `check_stock`.
3. Respondé con `kind = propuesta` y el pedido completo en `propuesta`. El mensaje repite cómo quedó y pregunta si lo armás.
4. Si no alcanza el stock, leé la skill `read_catalogo` y su BR-01. Si el producto nuevo no se puede identificar, leé `aclarar`.
5. Si saca todo, respondé con `kind = informacion`, sin `propuesta`, y preguntale si quiere otra cosa.
