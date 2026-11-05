---
nombre: read_catalogo
cuando: El cliente pide o consulta productos que el catálogo identifica sin dudas
tools: [search_products, get_product, check_stock, respond_to_customer]
---
1. Elegí de `search_products` el producto que coincide en tipo, marca, presentación y tono. Un resultado solo parecido no alcanza: mirá `score` y los datos de cada producto.
2. Compará su stock con la cantidad pedida. Solo usá `check_stock` si no tenés el stock de esa búsqueda.
3. Si alcanza para todo el pedido, respondé con `kind = propuesta` y todas las líneas en `propuesta`. El mensaje repite cantidad, producto y presentación, y pregunta si le armás el pedido.
4. Si alguna línea no alcanza, leé BR-01 antes de responder.
5. Si solo consulta precio o disponibilidad, respondé con `kind = informacion`. Si hay una propuesta vigente, respondé con `kind = propuesta` y las mismas líneas, y volvé a preguntar si la armás.
6. Si algún producto no se puede identificar, leé también la skill `aclarar`. Si no está en el catálogo, leé `fuera_de_catalogo`.
