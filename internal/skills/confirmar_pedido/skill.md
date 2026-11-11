---
nombre: confirmar_pedido
funcion: Cierra la propuesta vigente. Si el cliente la acepta sin cambios, la registra como pedido; si duda, pone condiciones o la rechaza, le responde sin registrar nada.
cuando: Hay una propuesta vigente y el cliente la acepta, la rechaza o duda. Si pregunta por un producto o un precio, o cambia algo, usá `tomar_pedido`
tools: [armar_pedido, respond_to_customer]
requiere_propuesta: true
---
1. Si el cliente solo aceptó, sin agregar nada más («dale», «sí», «listo»), llamá a `armar_pedido`. No recibe productos: registra la propuesta vigente, y el sistema le responde al cliente con el detalle y el número.
2. Si dijo algo más que aceptar, leé BR-06 y respondé con `respond_to_customer` como indica, sin armar nada.
3. Si `armar_pedido` devuelve `stock_insuficiente`, leé la skill `tomar_pedido` y su BR-01, y ofrecé lo que queda.

## Ejemplos

Muestran el tono, no qué decir. La confirmación de un pedido aceptado la escribe el sistema: no hay ejemplo.

- **Pone una condición:** «La fecha de entrega no la puedo coordinar por acá. El pedido te lo dejo armado como está: ¿lo confirmo?»
- **Pregunta el total:** «El total va abajo, con el precio de cada uno. ¿Lo cerramos?»
- **Duda:** «Tranqui, pensalo. Te lo dejo armado y cuando quieras me avisás.»
- **Rechaza:** «Perfecto, no armo nada. Si necesitás otra cosa, escribime.»
- **No se entiende:** «Para no equivocarme: ¿te confirmo el pedido así o querés cambiar algo?»
