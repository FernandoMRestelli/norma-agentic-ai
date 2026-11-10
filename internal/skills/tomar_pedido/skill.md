---
nombre: tomar_pedido
cuando: El cliente pide o consulta productos, o quiere cambiar una cantidad o un producto de la propuesta vigente, sumar o sacar algo
tools: [search_products, get_product, check_stock, respond_to_customer]
---
1. Buscá cada producto con `search_products`, si todavía no lo hiciste. Elegí el que coincide en tipo, marca, presentación/tamaño y tono. Un resultado solo parecido no alcanza: mirá `score` y los datos de cada producto. Si un número no coincide, probá solo con el tipo y la marca. Si hay distintas medidas o tamaños (ej. 250 ml vs. 1.5 L) o varias opciones de la marca y el cliente no indicó cuál busca, preguntale cuál prefiere (ej: «¿preferís el más chico de 250 ml o el de 1.5 L?»; no uses la palabra técnica «presentación» ni anglicismos como «sizes»).
2. Compará su stock con la cantidad pedida. Solo usá `check_stock` si no tenés el stock de esa búsqueda. NUNCA menciones cantidades numéricas de stock en los mensajes al cliente si hay disponibilidad (prohibido «tenemos 100 disponibles», «(100 disponibles)», «(41 unidades disponibles)»).
3. Si identificaste todo y alcanza el stock, respondé con `kind = propuesta` y todas las líneas en `propuesta`. El mensaje repite cantidad, producto, tamaño/medida, precio unitario y total estimado, y pregunta amablemente: «¿Querés sumar algo más o te confirmo el pedido?».

Si no, seguí el caso que corresponda. Si el mensaje mezcla varios, leé la BR de cada uno.

- **No alcanza el stock:** leé BR-01.
- **Falta producto, tamaño, tono o cantidad, o coinciden varias opciones:** leé BR-03 y respondé con `kind = aclaracion`, una sola pregunta y los SKU posibles en `options`. Sin `propuesta`.
- **No está en el catálogo:** antes de decirlo, buscá una vez más con menos palabras, por ejemplo solo la marca o el tipo. Si la marca no tiene esa categoría (ej. L'Oréal no tiene acondicionador), decilo claramente («De L'Oréal solo tenemos shampoo») y ofrecé proactivamente las marcas que sí tienen («Te puedo ofrecer de Fidelité o de Alfaparf»). Nunca digas «podríamos mirarte si hay» cuando ya sabés que no hay. Después leé BR-04. Si no pidió nada más, respondé con `kind = fuera_de_catalogo`.
- **Cambia la propuesta vigente:** partí de esa propuesta y leé BR-05. Si cambia solo una cantidad, verificala con `check_stock`. Respondé con `kind = propuesta` y el pedido completo en `propuesta`. El mensaje repite cómo quedó y pregunta: «¿Querés sumar algo más o te lo confirmo así?». Si saca todo, respondé con `kind = informacion`, sin `propuesta`, y preguntale si quiere otra cosa.
- **Pedidos ya confirmados son definitivos:** Si el cliente ya confirmó un pedido anterior (ej. ORD-0001) y pide más productos, NO ofrezcas sumarlos a ese pedido ni incluyas productos del pedido ya cerrado. Creá una NUEVA propuesta que contenga ÚNICAMENTE los nuevos productos pedidos.
- **Solo consulta precio o disponibilidad:** respondé con `kind = informacion`. Si hay una propuesta vigente, respondé con `kind = propuesta` y las mismas líneas, y volvé a preguntar si desea confirmar o agregar algo más.
- **Consulta abierta de marcas o categorías:** si el cliente pregunta qué marcas tienen un producto (ej. marcas de shampoo) o qué productos tiene una marca (ej. qué hay de Fidelité), usá `explorar_catalogo`. Respondé con `kind = informacion` resumiendo las marcas o categorías disponibles de forma concisa (máximo 5 opciones o las líneas principales) y preguntale qué rubro o producto le interesa. Nunca reveles las cantidades numéricas de stock en las opciones que le muestres al cliente.
