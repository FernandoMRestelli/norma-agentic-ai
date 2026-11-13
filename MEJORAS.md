# Mejoras

Estado del proyecto y próximos pasos. Para retomar: «leé MEJORAS.md y seguimos con el punto X».
Se actualiza al cerrar cada PR que cambie el estado o la lista.

## Estado actual

- **Etapa 01 del [roadmap](docs/roadmap.md) (base compartida)**: completa. La API levanta con Docker o con
  Python local, responde `/health` y valida la firma del webhook de WhatsApp.
- **Contratos de datos**: borrador (`OrderDraft`) separado de la orden (`Order`), catálogo (`Product`, `StockCheck`).
- **Fuentes de datos del issue #2**: material original en Drive privado, inventario con referencias de Drive,
  catálogo de 1.090 productos con stock ficticio de prueba y planilla vacía de seguimiento preparados fuera del repo.
  La cuenta de servicio tiene permisos por archivo; falta probar la autenticación del backend. La evaluación de
  los pedidos se difiere hasta disponer del agente. Ver [alcance y pendientes](docs/data/fuentes.md).
- **Flujo de trabajo**: ramas, PR, changelog, CI (lint, tests, build, secretos, reglas de PR) e imagen por release.
  Todavía no hay ninguna release publicada ni producción corriendo.
- **Primer loop del agente**: grafo con LangGraph, skills y BR, y pedido por conversación en Telegram. El catálogo
  sale de `data_example/catalogo.csv` y el pedido queda en la base local. Son 3 skills: `tomar_pedido`,
  `confirmar_pedido` e `informar`.
- **Esqueletos sin implementar**: Google Sheets, chat y frontend (devuelven `501` o
  `NotImplementedError`).

## Qué mostró la evidencia

Todavía no hay uso real: lo único medido son los tests.

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
- No hay autenticación en el dashboard ni en los endpoints del chat.

## Próximos pasos

**En curso:** la [toma de pedidos por texto](docs/spec/toma-de-pedidos-por-texto.md), con cinco mejoras
implementadas en ramas apiladas (evaluación, grafo, renglones, CSV por línea y memoria), cada una para su PR a
`develop`. Lo que falta para cerrarla:

- **Medir.** Todavía no hay una corrida completa con un LLM real. La primera fija la línea de base y los umbrales
  de release: `python -m internal.evaluar_agente --set pedidos --repeticiones 3 --conversaciones data/evaluacion/pedidos.md`.
  Hace falta un modelo que use tools de forma confiable y con cuota para los 11 casos (D-21).
- **Decidir** la ventana para agrupar mensajes seguidos (D-26): está implementada y apagada (`MESSAGE_GROUP_SECONDS`).
- **Probar** el volcado nuevo contra las planillas reales, con `python -m internal.verificar_claves` para que
  Sheet 2 sume las columnas nuevas.

Antes: el [plan de mejora del asistente](docs/plan-mejora-asistente.md), con cinco frentes (búsqueda tolerante a
errores, guardrails, guardado de pedidos, planillas y skills), ya integrados en `develop`.

1. **Primera release `0.1.0`**: `release/0.1.0` → `main`, tag `v0.1.0`, backport, y configurar la
   protección de ramas descrita en [CONTRIBUTING.md](CONTRIBUTING.md#protección-de-ramas).
2. **Etapa 02, datos del negocio**: `sheets_service.get_catalog()` con gspread y búsqueda por SKU y por nombre
   aproximado en `catalog_tools`; tests con `datos_sinteticos.catalogo()` y el helper `sin_stock`.
3. **Elegir el proveedor de LLM** para producción; tests del agente con un modelo falso (pedido ambiguo → pide aclaración).
4. **Base interna**: tablas de borradores y órdenes con `draft_id` único para que un reintento no duplique órdenes.
5. **Despliegue**: por ahora cada integrante despliega en el Docker de su máquina (D-03, ver `CONTRIBUTING.md#producción`). Falta definir un servidor siempre prendido para el bot beta compartido.
