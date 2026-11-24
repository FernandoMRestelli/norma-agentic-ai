# Validaciones pendientes

Supuestos que el agente ya usa pero que nadie del negocio confirmó todavía. Cada entrada dice qué asumimos, dónde está aplicado y qué cambia si resulta ser otra cosa. Cuando se confirme o se descarte, borrá la entrada y ajustá lo que corresponda.

Las dudas salen de pedidos reales. No copies acá nombres de clientes, teléfonos ni pedidos completos: describí el formato con ejemplos ficticios.

## V-01. Notación de coloración `tono/cantidad`

- **Qué vimos:** en los pedidos por WhatsApp, debajo de una marca de coloración aparecen renglones como «7/3», «821/2», «68c/2» o «1/2».
- **Qué asumimos:** el número antes de la barra es el tono sin punto y el de después es la cantidad. «7/3» es tono 7, 3 unidades; «821/2» es tono 8.21, 2 unidades.
- **Dónde está aplicado:** `internal/agent/system_prompt.md`, en la descripción de cómo escriben los clientes.
- **Si es otra cosa:** corregir el ejemplo del system prompt. Si la barra no separa la cantidad, el agente estaría pidiendo cantidades equivocadas en toda la coloración.
- **Cómo validarlo:** preguntarle al negocio cómo leen ellos esos renglones y si todos los clientes usan la misma notación.
- **Relacionado:** aunque el supuesto sea correcto, hoy el tono no se guarda. Cada coloración es un solo SKU sin tono y la línea del pedido (`ProposedItem`) tiene solo `sku` y `quantity`.

## V-02. Renglones de coloración sin barra

- **Qué vimos:** algunos renglones de coloración no tienen barra, por ejemplo «766».
- **Qué asumimos:** nada todavía. Puede ser tono 7.66 con 1 unidad, o un error de tipeo.
- **Dónde está aplicado:** en ningún lado.
- **Cómo validarlo:** preguntarle al negocio qué hacen cuando llega un renglón así.

## V-03. Varios pedidos en un mismo chat

- **Qué vimos:** un cliente manda un pedido largo y después «Pedido 2» o «Pedido <localidad>» con otra lista, en mensajes separados.
- **Qué asumimos:** nada todavía. El agente hoy arma una sola propuesta por conversación.
- **Si son pedidos distintos:** puede que vayan a otro destino o sucursal del cliente, y habría que registrarlos por separado.
- **Cómo validarlo:** preguntarle al negocio si los cargan como pedidos distintos y qué indica la localidad.

## V-04. Primera línea suelta del pedido

- **Qué vimos:** pedidos que empiezan con una palabra suelta antes de la primera marca (por ejemplo, el nombre de un comercio).
- **Qué asumimos:** nada todavía.
- **Si es el nombre del comercio o de un transporte:** podría servir para identificar al cliente o el envío.
- **Cómo validarlo:** preguntarle al negocio qué significa esa línea en los pedidos que reciben.

## V-05. Productos pedidos que no están en el catálogo

- **Qué vimos:** en un pedido real aparecen productos que no encontramos en `data/catalogo.csv`: la coloración Color Master (solo está el polvo decolorante), la coloración Inoa de L'Oréal y varias cremas de Skala.
- **Qué asumimos:** que no están en el catálogo, así que el agente los informa como fuera de catálogo (BR-04).
- **Si el catálogo está incompleto o usa otros nombres:** el agente rechazaría productos que el negocio sí vende.
- **Cómo validarlo:** revisar con el negocio si esos productos existen con otro nombre o si faltan en el catálogo.

## V-06. Datos del negocio que puede dar el agente

- **Qué vimos:** los clientes preguntan horarios, envíos y formas de pago, y el agente responde que no tiene ese dato porque no hay ninguna fuente con los datos del negocio.
- **Qué asumimos:** nada. `internal/skills/informar/datos_negocio.md` tiene los cuatro temas en `null`, y con `null` el agente sigue diciendo que no tiene ese dato.
- **Qué falta confirmar:**
  - `horarios`: días y horarios de atención y de retiro.
  - `zona_de_entrega`: si hacen envíos, a qué zonas, y si se retira en el local.
  - `medios_de_pago`: con qué se puede pagar (efectivo, transferencia, cuenta corriente…).
  - `minimo_de_compra`: si hay un monto o una cantidad mínima por pedido.
- **Dónde está aplicado:** la skill `informar` los lee con `leer_skill` (`datos_negocio`).
- **Si se confirma:** completar el dato en `datos_negocio.md`, en una línea como se la diría al cliente, y borrar ese tema de esta lista. Si el mínimo de compra se confirma, decidir también si el backend tiene que rechazar una propuesta que no llega.
- **Cómo validarlo:** preguntarle al negocio cada dato y si cambia según el cliente (por ejemplo, cuenta corriente solo para clientes habituales).
