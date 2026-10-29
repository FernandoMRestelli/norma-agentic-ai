# Mejoras

Estado del proyecto y próximos pasos. Para retomar: «leé MEJORAS.md y seguimos con el punto X».
Se actualiza al cerrar cada PR que cambie el estado o la lista.

## Estado actual

- **Etapa 01 del [roadmap](docs/roadmap.md) (base compartida)**: completa. La API levanta con Docker o con
  Python local, responde `/health` y valida la firma del webhook de WhatsApp.
- **Contratos de datos**: borrador (`OrderDraft`) separado de la orden (`Order`), catálogo (`Product`, `StockCheck`).
- **Flujo de trabajo**: ramas, PR, changelog, CI (lint, tests, build, secretos, reglas de PR) e imagen por release.
  Todavía no hay ninguna release publicada ni producción corriendo.
- **Esqueletos sin implementar**: agente, tools de catálogo y stock, Google Sheets, base interna, chat y frontend
  (devuelven `501` o `NotImplementedError`).

## Qué mostró la evidencia

Todavía no hay uso real: lo único medido son los tests.

- Los contratos de datos rechazan lo que tienen que rechazar (borrador sin ítems, cantidades no positivas,
  mensaje de audio sin archivo) y los precios no pierden centavos (`Decimal`).
- Las invariantes valen para todos los canales y tipos de mensaje: un borrador nace sin confirmar y el resumen
  muestra cada ítem.

## Limitaciones conocidas

- El stock sale de una planilla: se revalida al confirmar, pero Google Sheets no permite reservarlo.
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
3. **Elegir el proveedor de LLM** e implementar `llm_client`; tests del agente con `FakeLLM` (pedido ambiguo → pide aclaración).
4. **Base interna**: tablas de borradores y órdenes con `draft_id` único para que un reintento no duplique órdenes.
5. **Despliegue**: definir el servidor de producción, crear `~/norma-prod` y hacer el primer despliegue con la imagen de `0.1.0`.
