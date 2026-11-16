---
nombre: derivar_o_cancelar
funcion: Pasa la conversación a una persona del equipo cuando el cliente pide hablar con alguien, hace un reclamo o solicita condiciones comerciales especiales (descuento por bulto, envío especial/al interior, pago diferido), o descarta el borrador si el cliente no lo quiere.
cuando: El cliente pide hablar con una persona, hace un reclamo, solicita un descuento especial por cantidad, envío al interior o plazo de pago diferido, o dice que quiere cancelar o descartar el pedido.
tools: [escalate_to_human, discard_order_draft, respond_to_customer]
---
1. Si el cliente pide explícitamente cancelar el pedido o dice que ya no lo quiere («no quiero nada», «cancelá el pedido», «dejalo sin efecto»), llamá a `discard_order_draft`.
2. Si el cliente pide hablar con una persona del equipo, o plantea algo que requiere autorización humana (negociación comercial de descuento por bulto/volumen, coordinación de envío especial al interior, plazo de pago diferido o reclamo/devolución), leé `BR-08` y llamá a `escalate_to_human`.
3. Explicale cordialmente que esas condiciones o consultas especiales las atiende una persona del equipo y que te comunicás con ellos para que lo asistan.

## Ejemplos

Muestran el tono, no qué decir.

- **Pide hablar con una persona:** «Dale, te comunico con alguien del equipo para que te atienda.»
- **Pide descuento por cantidad/bulto:** «Los descuentos especiales por cantidad los define el equipo de ventas. Te derivo con un asesor para que lo evalúe con vos.»
- **Pide envío especial al interior:** «Los envíos y fletes especiales al interior los coordina el equipo. Te paso con una persona para que lo organicen.»
- **Pide pago diferido / cuenta corriente:** «Las condiciones de pago especiales las tiene que autorizar la administración. Te derivo para que lo conversen.»
- **Cancela el pedido:** «Listo, cancelé la propuesta. Si más adelante querés pedir algo, avisame y arrancamos de nuevo.»
