---
nombre: tomar_pedido
cuando: El cliente pide o consulta productos, o quiere cambiar una cantidad o un producto de la propuesta vigente, sumar o sacar algo
tools: [search_products, get_product, check_stock, respond_to_customer]
---
1. Buscá cada producto con `search_products`, si todavía no lo hiciste. Elegí el que coincide en tipo, marca, presentación y tono. Un resultado solo parecido no alcanza: mirá `score` y los datos de cada producto. Si un número no coincide, probá solo con el tipo y la marca.
2. Compará su stock con la cantidad pedida. Solo usá `check_stock` si no tenés el stock de esa búsqueda.
3. Si identificaste todo y alcanza el stock, respondé con `kind = propuesta` y todas las líneas en `propuesta`. El mensaje repite cantidad, producto, presentación, precio unitario y total estimado, y pregunta si le armás el pedido.

Si no, seguí el caso que corresponda. Si el mensaje mezcla varios, leé la BR de cada uno.

- **No alcanza el stock:** leé BR-01.
- **Falta producto, presentación, tono o cantidad, o coinciden varias opciones:** leé BR-03 y respondé con `kind = aclaracion`, una sola pregunta y los SKU posibles en `options`. Sin `propuesta`.
- **No está en el catálogo:** antes de decirlo, buscá una vez más con menos palabras, por ejemplo solo la marca o el tipo. Después leé BR-04. Si no pidió nada más, respondé con `kind = fuera_de_catalogo`.
- **Cambia la propuesta vigente:** partí de esa propuesta y leé BR-05. Si cambia solo una cantidad, verificala con `check_stock`. Respondé con `kind = propuesta` y el pedido completo en `propuesta`. El mensaje repite cómo quedó y pregunta si lo armás. Si saca todo, respondé con `kind = informacion`, sin `propuesta`, y preguntale si quiere otra cosa.
- **Solo consulta precio o disponibilidad:** respondé con `kind = informacion`. Si hay una propuesta vigente, respondé con `kind = propuesta` y las mismas líneas, y volvé a preguntar si la armás.
