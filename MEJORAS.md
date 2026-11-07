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
  sale de `data_example/catalogo.csv` y el pedido queda en la base local.
- **Esqueletos sin implementar**: Google Sheets, chat y frontend (devuelven `501` o
  `NotImplementedError`).

## Qué mostró la evidencia

Todavía no hay uso real: lo único medido son los tests.

**Evaluación con Gemini (2026-11-05), incompleta:** de 14 casos de `mensajes.json`, solo 2 llegaron a evaluarse
(`texto-varios-productos` y `cantidad-en-palabras`, ambos correctos en 3 iteraciones). El resto falló por `503` de alta
demanda o por límite de requests del free tier, así que todavía no hay medición de cómo interpreta «dale». Esos 2 casos
son anteriores a las skills `aclarar`, `fuera_de_catalogo`, `corregir_pedido` e `informar` y al prompt actual. Con ellos,
una segunda corrida el mismo día no pudo evaluar ninguno de los 22 casos: el LLM falló en todos, y dos pruebas sueltas
justo antes devolvieron `503` y `504`. Hay que repetirla con `python -m internal.evaluar_agente --pausa 30`.

- Los contratos de datos rechazan lo que tienen que rechazar (borrador sin ítems, cantidades no positivas,
  mensaje de audio sin archivo) y los precios no pierden centavos (`Decimal`).
- Las invariantes valen para todos los canales y tipos de mensaje: un borrador nace sin confirmar y el resumen
  muestra cada ítem.

## Limitaciones conocidas

- El stock sale de una planilla: se revalida al confirmar, pero Google Sheets no permite reservarlo.
- **Descuento de stock en base interna:** las órdenes activas en SQLite descuentan unidades del catálogo (`inventory_service`), evitando la sobreventa (*overselling*) entre clientes concurrentes incluso con el CSV estático o Google Sheet 1 de solo lectura.
- **Cancelación de órdenes expuesta (RF-20):** endpoint `POST /orders/{order_id}/cancel` y tool `cancelar_pedido` disponibles, permitiendo cancelar órdenes activas y liberando su stock comprometido en la base interna.
- SQLite en `data/` alcanza para el MVP con una sola instancia; dos procesos escribiendo a la vez no.
- No hay configuración de negocio fuera del código y del `.env`. Cuando aparezcan parámetros que no son secretos
  (por ejemplo el umbral de "poco stock" del dashboard), van en `config/` como JSON validado al arrancar,
  dentro de la imagen: cambiarlos será una release.
- No hay autenticación en el dashboard ni en los endpoints del chat.

## Próximos pasos

1. **Primera release `0.1.0`**: `release/0.1.0` → `main`, tag `v0.1.0`, backport, y configurar la
   protección de ramas descrita en [CONTRIBUTING.md](CONTRIBUTING.md#protección-de-ramas).
2. **Etapa 02, datos del negocio**: `sheets_service.get_catalog()` con gspread y búsqueda por SKU y por nombre
   aproximado en `catalog_tools`; tests con `datos_sinteticos.catalogo()` y el helper `sin_stock`.
3. **Elegir el proveedor de LLM** para producción; tests del agente con un modelo falso (pedido ambiguo → pide aclaración).
4. **Base interna**: tablas de borradores y órdenes con `draft_id` único para que un reintento no duplique órdenes.
5. **Despliegue**: definir el servidor de producción, crear `~/norma-prod` y hacer el primer despliegue con la imagen de `0.1.0`.
