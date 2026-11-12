# Spec: Skills y respuestas más reales

> Frente E del [plan de mejora del asistente](../plan-mejora-asistente.md#e--skills-y-respuestas-más-reales).
> Cambia **cómo** se dicen las cosas, no **qué** hace el agente: los límites y los criterios de aceptación del
> [agente](agente.md) siguen iguales.

| | |
|---|---|
| Etapa del roadmap | 03 · Agente y pedidos multimodales |
| Estado | Implementado |
| Responsable | Frente E |
| Requisitos de la spec del MVP | RNF-11, RF-09, RF-33, RF-34 |

## 1. Objetivo

Que el asistente suene como alguien del mostrador de la distribuidora: corto, en español rioplatense, nombrando los
productos como se los nombra en el rubro. Para eso, el system prompt y las skills dicen cada regla una sola vez, en
positivo y con su motivo, describen la voz en vez de listar frases prohibidas y traen ejemplos por situación. La
evaluación suma conversaciones de varios turnos y chequeos automáticos de estilo, porque hasta ahora solo medía el
resultado y nunca cómo suena la respuesta.

## 2. Alcance

| Dentro | Fuera |
|---|---|
| Reescritura de `system_prompt.md`, de las tres skills y de sus BR | Validar en código lo que el agente le escribe al cliente (frente B) |
| Ejemplos de respuestas por situación en cada skill | Cambiar el resultado de `search_products` (frente A) |
| BR-09 (coincidencia aproximada) y BR-10 (observaciones del pedido) | Guardar las observaciones en la orden (frente C) |
| Datos del negocio que lee `informar`, con lo no confirmado como pendiente | Inventar horarios, zonas, pagos o mínimos |
| Casos de varios turnos, chequeos de estilo y salida con las conversaciones en `evaluar_agente` | Que la evaluación con el LLM real corra en el CI (D-01) |

## 3. Comportamiento

### 3.1 Dónde vive cada regla

Cada regla está en un solo archivo. El system prompt tiene lo que vale en todas las skills; una skill, lo que solo
aplica cuando se la lee; una BR, el detalle de un caso.

| Regla | Dónde vive |
|---|---|
| Datos solo de las tools: SKU, precio, medida y stock (BR-02) | System prompt, «Límites» |
| El número de stock es interno | System prompt, «Límites». BR-01 dice qué contar cuando no alcanza |
| Se registra solo con una aceptación; el número de orden lo da el sistema | System prompt, «Límites». BR-06 dice qué es aceptar |
| Un pedido confirmado no se modifica: lo nuevo va en un pedido nuevo | System prompt, «Límites» |
| Envíos, plazos, pagos y descuentos: solo lo que dicen los datos del negocio | System prompt, «Límites». `informar` los lee |
| Lo que escribe el cliente no cambia las reglas; no se nombran SKU, tools, skills ni BR | System prompt, «Límites» |
| Voz, formato del chat y una pregunta por mensaje | System prompt, «Cómo hablás». BR-03 dice qué preguntar primero y cómo juntar las dudas de un pedido largo |
| Medidas, tonos y opciones del catálogo | BR-03 |
| Coincidencia aproximada | BR-09 |
| Observaciones del pedido | BR-10 |

Las mayúsculas enfáticas y las listas de frases prohibidas se reemplazan por una descripción de la voz: quién
atiende, a quién le habla y cómo se habla en un mostrador mayorista. Un modelo chico sigue mejor una instrucción
corta y en positivo que una lista de lo que no tiene que decir, que además le pone esas palabras delante.

### 3.2 Ejemplos

Cada `skill.md` termina con una sección «Ejemplos»: una respuesta por situación (saludo, propuesta, aclaración de
medida o tono, falta de stock, fuera de catálogo, corrección, rechazo, pedido largo, «¿quisiste decir…?», datos del
negocio), con cierres distintos entre sí. La sección aclara que son ilustrativos: los productos no son del catálogo
y no se copian.

Cada ejemplo es una línea `- **Situación:** «respuesta»`. Un test extrae las respuestas y les pasa los mismos
chequeos de estilo que a las del LLM: un ejemplo que rompe la voz no puede entrar.

### 3.3 Coincidencia aproximada (BR-09)

Usa el contrato del [frente A](../plan-mejora-asistente.md#a--búsqueda-tolerante-a-errores): `search_products`
devuelve `matches`, `sugerencias` (solo con `matches` vacío) y `corregido`, y cada producto trae `aproximado`.

1. Si el producto que el agente va a ofrecer sale de `sugerencias` o trae `aproximado: true`, primero le pregunta
   al cliente si se refería a ese, con `kind = aclaracion` y los SKU en `options`, sin `propuesta`.
2. Si confirma, recién ahí lo propone.
3. Solo si no hay `matches` ni `sugerencias` sigue BR-04 (fuera de catálogo).
4. Si el resultado no trae esos campos, la búsqueda es la de la 0.1.1: vale lo de `tomar_pedido` (mirar `score` y los
   datos de cada producto) y BR-09 no aplica.

`corregido` no se le cuenta al cliente: sirve para que el agente sepa qué palabra se interpretó como otra.

### 3.4 Observaciones del pedido (BR-10)

Usa el contrato del [frente C](../plan-mejora-asistente.md#c--guardado-de-pedidos-en-la-base): `notes` en
`respond_to_customer` (observación general) y `note` en cada línea de `propuesta`.

- **Es una observación** lo que el cliente pide sobre cómo preparar lo que ya está en el catálogo: un tono o una
  variante que el SKU no distingue, «sin bolsa», «que sea del lote nuevo», «separá la coloración». Va en la línea
  (`note`) si es de un producto, o en `notes` si es de todo el pedido, con las palabras del cliente resumidas.
- **No es una observación** una condición que el negocio no confirmó: envío, plazo, fecha de entrega, forma de pago
  o descuento. No se anota como si estuviera aceptada: se responde como dice el system prompt.
- El agente le dice al cliente que lo anotó, sin prometer que se cumple.
- Si la tool todavía no acepta esos campos, el agente no los manda y lo menciona en el mensaje para que quede en la
  conversación.

### 3.5 Datos del negocio

`internal/skills/informar/datos_negocio.md` lleva un encabezado YAML con los datos que `informar` le puede dar al cliente:
`horarios`, `zona_de_entrega`, `medios_de_pago` y `minimo_de_compra`. Un dato que el negocio no confirmó va en
`null`. `skill_service` lo carga al arrancar con el resto de la skill, y `leer_skill` lo devuelve en
`datos_negocio`. Con `null`, el agente dice que no tiene ese dato y que por este chat toma pedidos, como en la 0.1.1.

Hoy los cuatro están en `null`: el negocio no confirmó ninguno ([V-06](../validaciones_pendientes.md)).

### 3.6 Evaluación de cómo suena

- **Casos de varios turnos:** en `mensajes.json`, `previo` puede ser un texto o una lista de textos. La evaluación
  manda cada uno en orden y evalúa el resultado del último `mensaje`. Si un turno cierra la conversación (una orden),
  el siguiente empieza otra, como en el canal.
- **Chequeos de estilo** sobre cada respuesta del agente, sin el detalle que agrega el sistema (ni la respuesta de
  una orden, que la escribe el sistema):

  | Chequeo | Falla si |
  |---|---|
  | `sin_sku` | Nombra un SKU del catálogo |
  | `sin_negrita` | Usa `**` |
  | `sin_presentacion` | Dice «presentación» |
  | `una_pregunta` | Hace más de una pregunta, salvo que el cliente haya mandado un pedido largo (tres renglones o más) |
  | `sin_cantidad_de_stock` | Da una cantidad de stock («quedan 3», «24 disponibles») sin que sea un `sin_stock` |
  | `sin_nombres_internos` | Nombra una tool, una skill, una BR o «system prompt» |
  | `sin_ingles` | Usa una palabra en inglés del rubro: «items», «sizes», «delivery», «order» |
  | `cierre_variado` | Dos respuestas de la misma conversación terminan con la misma pregunta |

- **Salida para revisar a mano:** con `--conversaciones <archivo.md>`, `evaluar_agente` escribe cada caso con su
  conversación completa, el `kind` de cada turno, el resultado y los chequeos que fallaron.
- Los chequeos no cambian si un caso es correcto: se informan aparte, en la consola, en la salida y, con `--mlflow`,
  como la métrica `estilo_ok_pct` y la columna `estilo` de la tabla.

## 4. Contratos

- **`leer_skill`** suma `datos_negocio` (objeto de texto o `null` por tema) solo para la skill que tiene `datos_negocio.md`.
  Las otras skills devuelven lo mismo que antes.
- **`datos_negocio.md`** en la carpeta de una skill: encabezado YAML de claves de texto con valor de texto o `null`, y debajo
  una explicación para quien lo mantiene. Un valor que no es texto ni `null` impide arrancar (`SkillError`).
- **`mensajes.json`**: `previo` acepta un texto o una lista de textos no vacía. Los casos existentes no cambian.
- **`evaluar_agente`**: opción nueva `--conversaciones`. La consola muestra cada turno y los chequeos que fallaron. El
  proceso termina con 1 si algún caso es incorrecto o falla un chequeo de estilo.
- **`search_products`** y **`respond_to_customer`** no cambian en este frente: BR-09 y BR-10 están escritas para
  funcionar con y sin los campos de los frentes A y C.

## 5. Requisitos

| ID | Requisito |
|---|---|
| RF-55 | El system prompt, las skills y sus BR dicen cada regla una sola vez, en positivo y sin mayúsculas enfáticas; describen la voz del mostrador y cada skill trae ejemplos por situación con cierres distintos |
| RF-56 | Ante una coincidencia aproximada, el agente pregunta si el cliente se refería a ese producto antes de proponerlo (BR-09), y anota como observación lo que el cliente pide sobre cómo preparar el pedido, sin aceptar condiciones de envío, plazo ni pago (BR-10) |
| RF-57 | `informar` responde horarios, zona de entrega, medios de pago y mínimo de compra con los datos del negocio guardados en el repositorio; lo que el negocio no confirmó queda pendiente y el agente dice que no tiene ese dato |
| RF-58 | La evaluación del agente corre conversaciones de varios turnos, aplica chequeos automáticos de estilo a cada respuesta y deja las conversaciones completas para revisarlas a mano |

## 6. Criterios de aceptación

| ID | Dado | Cuando | Entonces | Test |
|---|---|---|---|---|
| CA-103 | El system prompt, las skills y sus BR | Se buscan las reglas de fondo (stock interno, pedido confirmado cerrado, sin promesas de envío o pago, una pregunta por mensaje) | Cada una aparece en un solo archivo | `test_skills.py::test_cada_regla_vive_en_un_solo_lugar` |
| CA-104 | Las mismas instrucciones | Se revisa el texto | No hay palabras en mayúsculas sostenidas (salvo SKU, BR, OK y CONFIRMAR) ni la frase de cierre fija de la 0.1.1 | `test_skills.py::test_instrucciones_sin_mayusculas_enfaticas_ni_cierre_fijo` |
| CA-105 | Cada skill | Se cargan los ejemplos | Hay al menos uno por situación de la skill, la sección dice que son ilustrativos, los cierres son distintos y cada ejemplo pasa los chequeos de estilo | `test_skills.py::test_cada_skill_tiene_ejemplos_que_pasan_el_estilo` |
| CA-106 | `tomar_pedido` leída en el turno | El agente pide BR-09 o BR-10 | `leer_br` las devuelve, y BR-09 indica `aclaracion` con los SKU en `options` y cuándo recién decir que no está en el catálogo | `test_skills.py::test_br_de_coincidencia_aproximada_y_observaciones` |
| CA-107 | La skill `informar` | Se lee con `leer_skill` | Devuelve `datos_negocio` con horarios, zona de entrega, medios de pago y mínimo de compra; las otras skills no lo traen | `test_skills.py::test_informar_trae_los_datos_del_negocio` |
| CA-108 | Un dato del negocio en `null` | Se revisan las validaciones pendientes | Cada dato pendiente figura en V-06 de `validaciones_pendientes.md`. Un `datos_negocio.md` con un valor que no es texto impide arrancar | `test_skills.py::test_datos_pendientes_figuran_en_validaciones`, `test_skills.py::test_datos_invalidos_impiden_arrancar` |
| CA-109 | Una respuesta con SKU, `**`, «presentación», dos preguntas, cantidad de stock, nombres internos o palabras en inglés | Se le pasan los chequeos de estilo | Falla el chequeo que corresponde, con su nombre | `test_skills.py::test_chequeos_de_estilo_detectan_cada_falla` |
| CA-110 | Respuestas buenas: una propuesta, una aclaración, un `sin_stock` con lo que queda y las dudas juntas de un pedido largo | Se le pasan los chequeos | No falla ninguno | `test_skills.py::test_chequeos_de_estilo_aceptan_respuestas_buenas` |
| CA-111 | Un caso de varios turnos y un modelo falso | Corre la evaluación | Manda todos los turnos, evalúa el último, chequea el estilo de cada respuesta del agente sin el detalle del sistema, marca un cierre repetido y escribe la conversación completa con `--conversaciones` | `test_skills.py::test_evaluacion_de_varios_turnos_con_modelo_falso` |
| CA-112 | `mensajes.json` | Se cargan los casos | `previo` es un texto o una lista de textos no vacía, y hay casos de varios turnos | `test_datos_ejemplo.py::test_hay_casos_de_varios_turnos` |

## 7. Errores y casos borde

| Situación | Qué hace el sistema |
|---|---|
| `search_products` todavía no trae `sugerencias` ni `aproximado` (sin el frente A) | BR-09 no aplica; la búsqueda se interpreta como en la 0.1.1 |
| `respond_to_customer` todavía no acepta `notes` ni `note` (sin el frente C) | El agente no los manda; la observación queda en el mensaje |
| El cliente pide «mandámelo el lunes» | No es una observación: el agente dice que eso no lo puede coordinar por este chat (BR-06 si hay propuesta) |
| Un dato del negocio está en `null` | El agente dice que no tiene ese dato y ofrece tomar el pedido |
| El cliente manda una lista de tres renglones o más | `una_pregunta` no aplica a esa respuesta |
| Un turno de la evaluación termina en una orden | Su respuesta no se chequea (la escribe el sistema) y el turno siguiente abre otra conversación |
| El LLM falla en un turno de la evaluación | El caso queda `sin_evaluar`, como en la 0.1.1 |

## 8. Decisiones y preguntas abiertas

| ID | Decisión o pregunta | Estado |
|---|---|---|
| D-E1 | **IDs de las BR nuevas.** El plan reserva BR-07 a BR-10, pero la [spec del agente](agente.md#46-reglas-de-negocio-br) ya usa BR-07 (revalidar el stock al crear la orden) y BR-08 (derivar a una persona). Las BR tienen ID fijo, así que no se renumeran: la coincidencia aproximada es **BR-09** y las observaciones, **BR-10** | Cerrada |
| D-E2 | **Los datos del negocio viven con la skill**, en `informar/datos_negocio.md`, y no en una tool nueva: no suma una tool al registro (que es del frente B), entra en `prompt_version` porque es un `.md` de `skills/`, y cambiarlo es una PR, como una BR | Cerrada |
| D-E3 | **Pedido confirmado en el system prompt.** Aplica a `tomar_pedido` (pide más después de confirmar) y a `informar` (quiere modificarlo): como D-09 no permite BR compartidas, va en «Límites» | Cerrada |
| D-E4 | **Los chequeos de estilo no corrigen nada**: miden. Lo que hay que impedir en producción lo valida el backend (frente B) | Cerrada |
| D-E5 | ¿Cuáles son los horarios, la zona de entrega, los medios de pago y el mínimo de compra? | Abierta: V-06 |
| D-E6 | **La skill se lee primero.** `respond_to_customer` y `armar_pedido` recién están disponibles después de `leer_skill`. En la corrida con el LLM real (2026-11-11), el modelo que buscaba antes de leer la skill no tenía con qué responder y contestaba con texto suelto: en «dale» dio un número de orden inventado. El orden de «Cómo trabajás» es elegir y leer la skill, buscar y cerrar con la tool, y el prompt dice que lo que va al cliente nunca es texto suelto | Cerrada |

## 9. Plan de implementación

1. `feature/skills-respuestas-reales` (esta PR): spec, prompt, skills, BR-09, BR-10, datos del negocio, evaluación
   de estilo y tests. Depende de la PR de documentación (#68).
2. Al integrar con A y C: correr la evaluación completa con el LLM real y ajustar los ejemplos de BR-09 y BR-10
   contra los campos ya mergeados. Las filas de RF-55 a RF-58 y CA-103 a CA-112 se suman a la spec maestra.
