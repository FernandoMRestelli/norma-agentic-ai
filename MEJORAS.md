# Mejoras

Estado del proyecto y próximos pasos. Para retomar: «leé MEJORAS.md y seguimos con el punto X».
Se actualiza al cerrar cada PR que cambie el estado o la lista.

## Estado actual

- **Etapa 01 del [roadmap](docs/roadmap.md) (base compartida)**: completa. La API levanta con Docker o con
  Python local, responde `/health` y valida la firma del webhook de WhatsApp.
- **Contratos de datos**: borrador (`OrderDraft`) separado de la orden (`Order`), catálogo (`Product`, `StockCheck`).
- **Fuentes de datos del issue #2**: material original en Drive privado, inventario con referencias de Drive y
  planilla de seguimiento, fuera del repo. Ver [alcance y pendientes](docs/data/fuentes.md).
- **Flujo de trabajo**: ramas, PR, changelog, CI (lint, tests, build, secretos, reglas de PR) e imagen por release.
  Publicadas la 0.1.0, la 0.1.1 y la 0.2.0.
- **Agente**: grafo con LangGraph y cuatro skills (`tomar_pedido`, `confirmar_pedido`, `informar` y
  `derivar_o_cancelar`), pedidos por texto, foto y audio en Telegram, derivación a una persona y consulta de estado.
- **Controles en código**: la aceptación, los productos, las opciones, las cantidades, los precios y las medidas se
  comprueban antes de responder o registrar (RF-111 a RF-114, RF-120, RF-121). Lo que resuelve el sistema en lugar del
  modelo queda en `rescates` (RF-123).
- **Medición**: `evaluar_agente` (tres sets, con un grupo reservado), `evaluar_busqueda` (sin LLM, con piso en el CI) y
  `sensor_calidad` (uso real).
- **Planillas y dashboard**: catálogo y seguimiento en Google Sheets o CSV, dashboard operativo con token de operador.
- **Sin implementar**: el chat web (`POST /chat/messages` devuelve `501`) y el envío por WhatsApp.

## Qué mostró la evidencia

**Buscador solo, sin LLM (2026-11-24):** 124 consultas ficticias. Antes y después de RF-118: recall@3 0,87 → 0,94, MRR
0,867 → 0,937, únicos correctos 0,776 → 0,91 y productos que existen y se negaban 12 → 5; abstención 1,0 y falsos únicos
0, sin cambios. Lo que falta son necesidades dichas con palabras que ningún nombre tiene («teñir», «mechas»).

**Trazas locales (2026-11-24):** 28 turnos de prueba del equipo, con modelos distintos: el loop principal dio p50 2, p95 5
y máximo 8 iteraciones (el tope es 50, D-51), 22.500 tokens por turno, p95 de latencia 69 s y 21 % de turnos con error.
Es una muestra chica y mezcla modelos gratuitos: sirve para ver el orden de magnitud, no para comparar.

**Evaluación con Gemini (2026-11-05), incompleta:** de 14 casos de `mensajes.json`, solo 2 llegaron a evaluarse
(`texto-varios-productos` y `cantidad-en-palabras`, ambos correctos en 3 iteraciones). El resto falló por `503` de alta
demanda o por límite de requests del free tier, así que todavía no hay medición de cómo interpreta «dale». Esos 2 casos
son anteriores a las skills `aclarar`, `fuera_de_catalogo`, `corregir_pedido` e `informar` y al prompt actual. Con ellos,
una segunda corrida el mismo día no pudo evaluar ninguno de los 22 casos: el LLM falló en todos, y dos pruebas sueltas
justo antes devolvieron `503` y `504`.

**Tercera corrida (2026-11-05), ya con 3 skills:** tampoco evaluó ninguno de los 24 casos, con `--pausa 30`. Los
primeros errores fueron `GoogleAPIError` y después `429`: el free tier de `gemini-3.8-flash` permite 20 requests por día.
Cada caso hace varias llamadas, así que `--pausa` no alcanza: hace falta otra cuota o correr pocos casos por día con
`--caso`. Cuando corra, hay que mirar si el agente lee la BR de cada caso, ahora que están todas en `tomar_pedido`.

**Cuarta corrida (2026-11-10 y 2026-11-11), frente E de skills y respuestas, OpenRouter con el modelo por defecto
(`nemotron-3.5-lightning:free`), un intento por caso:**

- **Prompt de la 0.1.1 (2026-11-10):** evaluó 2 de 6 casos; el resto cortó por `OpenAIRateLimitError`. `saludo` salió
  correcto («¡Hola! ¿Buen día. En qué te ayudo?»). `texto-simple` salió incorrecto: escribió como texto suelto el
  detalle de precios que ya agrega el sistema, le dijo al cliente «Stock disponible: 24 unidades» y cerró con la frase
  fija «¿Querés sumar algo más o te confirmo el pedido?».
- **Prompt nuevo, primera versión (`8692e61bae`, 2026-11-11):** de 5 casos evaluados, 2 correctos (`saludo` y
  `conversacion-aclara-medida-y-acepta`) y 3 con buen estilo. `ambiguo-producto-y-presentacion` quedó sin respuesta del
  LLM. En `acepta-dale`, sin tool terminal, el modelo contestó «Pedido confirmado… Número de orden: 241003A»: inventó
  el número y no registró nada.
- **Por qué:** `respond_to_customer` y `armar_pedido` recién están disponibles después de `leer_skill`. Al reescribir
  saqué «nunca respondas con texto suelto», y el prompt además pedía buscar antes de leer la skill: el modelo buscaba
  y, al no tener con qué responder, escribía texto.
- **Con «nunca como texto suelto» de vuelta (`6a009bac6d`):** 2 de 5 correctos (`saludo` y `acepta-dale`, este con el
  número que da el sistema). `texto-simple`, `ambiguo-producto-y-presentacion` y `stock-insuficiente` siguen sin leer la
  skill ni usar la tool terminal.
- **Con la skill leída primero (`2cad7c65ba`, el del PR):** 1 de 3 correctos; los otros 3 cortaron por la cuota. Ahora
  el modelo lee la skill, pero igual contesta con texto suelto aunque tenga `respond_to_customer`.

Con un intento por caso, lo medido no alcanza para decir si el prompt nuevo es mejor que el de la 0.1.1. Cuando el
modelo usa las tools, la voz es la buscada: «¡Buen día! ¿Qué te preparo hoy?» (copia el ejemplo de la skill), «Listo,
te armo 2 shampoos Pantene de 400. ¿Te lo confirmás así?». Cuando no las usa, vuelven las negritas, «presentación», el
número de stock y dos preguntas por mensaje. Lo que falla es el uso de tools, como ya advierte `.env.example`. Falta
medir el prompt nuevo con un modelo que use tools (por ejemplo Gemini) o con más cuota, y con varias iteraciones por
caso: `python -m internal.evaluar_agente --pausa 30 --conversaciones data/evaluacion/conversaciones.md`.

- Los contratos de datos rechazan lo que tienen que rechazar (borrador sin ítems, cantidades no positivas,
  mensaje de audio sin archivo) y los precios no pierden centavos (`Decimal`).
- Las invariantes valen para todos los canales y tipos de mensaje: un borrador nace sin confirmar y el resumen
  muestra cada ítem.

## Limitaciones conocidas

- El stock sale de una planilla: se revalida al confirmar, pero Google Sheets no permite reservarlo.
- **Descuento de stock en base interna:** las órdenes activas en SQLite descuentan unidades del catálogo (`inventory_service`), evitando la sobreventa (*overselling*) entre clientes concurrentes incluso con el CSV estático o Google Sheet 1 de solo lectura.
- **Cancelación de órdenes expuesta (RF-20):** endpoint autenticado `POST /orders/{order_id}/cancel` (con `X-Operator-Token` o `Authorization: Bearer`) y función interna `order_tools.cancelar_pedido` disponibles, permitiendo al operador cancelar órdenes activas y liberando su stock comprometido en la base interna. La tool no está registrada en el agente conversacional para evitar cancelaciones de pedidos de terceros por chat sin validar autoría.
- SQLite en `data/` alcanza para el MVP con una sola instancia; dos procesos escribiendo a la vez no.
- No hay configuración de negocio fuera del código y del `.env`. Cuando aparezcan parámetros que no son secretos
  (por ejemplo el umbral de "poco stock" del dashboard), van en `config/` como JSON validado al arrancar,
  dentro de la imagen: cambiarlos será una release.
- El endpoint del chat web no tiene autenticación, y todavía no está implementado.
- **El control de la aceptación no cubre todo (D-47):** después de una respuesta informativa sin opciones, un «dale» lo
  decide el modelo. Y una búsqueda a la que el modelo le sumó un dato que el cliente no dijo pasa como definida (D-49).

## Próximos pasos

**En curso:** la [toma de pedidos por texto](docs/spec/toma-de-pedidos-por-texto.md), con cinco mejoras
implementadas en ramas apiladas (evaluación, grafo, renglones, CSV por línea y memoria), cada una para su PR a
`develop`. Lo que falta para cerrarla:

- **Medir.** Todavía no hay una corrida completa con un LLM real. La primera fija la línea de base, los umbrales de
  release (D-28), el modelo (D-21), el tope de iteraciones (D-51) y si se queda el orquestador (D-36):
  `python -m internal.evaluar_agente --set pedidos --repeticiones 3 --max-llamadas 600 --precio 0.10,0.50 --salida data/evaluacion/pedidos.json`,
  lo mismo con `--set mensajes` y `--set adversariales`, y con `--orquestador si`. Después,
  `python -m internal.comparar_corridas`.
- **Decidir** la ventana para agrupar mensajes seguidos (D-26): está implementada y apagada (`MESSAGE_GROUP_SECONDS`).
- **Probar** el volcado nuevo contra las planillas reales, con `python -m internal.verificar_claves` para que
  Sheet 2 sume las columnas nuevas.

Antes: el [plan de mejora del asistente](docs/plan-mejora-asistente.md), con cinco frentes (búsqueda tolerante a
errores, guardrails, guardado de pedidos, planillas y skills), ya integrados en `develop`.

1. **Ajustar los umbrales** del sensor con las primeras semanas de uso (D-52) y los de release con la primera corrida.
2. **Sumar consultas reales del equipo** (sin datos de clientes) a `consultas_busqueda.json`: las de hoy son inventadas.
3. **Decidir embeddings** (D-29) solo si las consultas de necesidad pesan en el uso real.
4. **Chat web**: conectar `POST /chat/messages` con el orquestador de la conversación.
5. **Despliegue**: por ahora cada integrante despliega en el Docker de su máquina (D-03, ver `CONTRIBUTING.md#producción`). Falta definir un servidor siempre prendido para el bot beta compartido.
