---
nombre: confirmar_pedido
funcion: Cierra la propuesta vigente. Si el cliente la acepta sin cambios, la registra como pedido; si duda, pone condiciones o la rechaza, le responde sin registrar nada.
cuando: Hay una propuesta vigente y el cliente la acepta, la rechaza o duda. Si pregunta por un producto o un precio, o cambia algo, usá `tomar_pedido`
tools: [armar_pedido, respond_to_customer]
requiere_propuesta: true
---
1. Si el cliente solo aceptó, sin agregar nada más («dale», «sí», «listo»), llamá a `armar_pedido`. No recibe productos: registra la propuesta vigente, y el sistema le responde al cliente con el detalle y el número. Si tu mensaje anterior le preguntó otra cosa (una medida, un tono, «¿es el de 400?»), ese «sí» o ese «dale» contesta la pregunta y no acepta el pedido: leé la skill `tomar_pedido`.
2. Si dijo algo más que aceptar, leé BR-06. Si agrega observaciones de retiro, fecha o pago, leé `tomar_pedido` y BR-10, guardalas en `notes` y mostrá la propuesta actualizada para que la confirme, sin armar nada todavía.
3. Si `armar_pedido` devuelve `aceptacion_no_clara`, no se registró nada: no la vuelvas a llamar en este turno y respondele como dice el error.
4. Si `armar_pedido` devuelve `stock_insuficiente`, leé la skill `tomar_pedido` y su BR-01, y ofrecé lo que queda.

## Ejemplos

Muestran el tono, no qué decir. La confirmación de un pedido aceptado la escribe el sistema: no hay ejemplo.

- **Pone una condición:** «Anoto lo que pedís para que lo valide el negocio; la fecha no te la puedo garantizar. ¿Confirmás el pedido con esas observaciones?»
- **Pregunta el total:** «El total va abajo, con el precio de cada uno. ¿Lo cerramos?»
- **Duda:** «Tranqui, pensalo. Te lo dejo armado y cuando quieras me avisás.»
- **Rechaza:** «Perfecto, no armo nada. Si necesitás otra cosa, escribime.»
- **No se entiende:** «Para no equivocarme: ¿te confirmo el pedido así o querés cambiar algo?»
