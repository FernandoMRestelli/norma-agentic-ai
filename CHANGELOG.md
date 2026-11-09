# Changelog

Todos los cambios relevantes del proyecto se registran en este archivo.

El formato sigue [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/)
y el proyecto usa [versionado semántico](https://semver.org/lang/es/).
Cómo mantenerlo: ver [CONTRIBUTING.md](CONTRIBUTING.md#changelog).

## [Sin publicar]

### Agregado

- Endpoints HTTP `POST /orders/{order_id}/cancel` y `GET /orders/{order_id}` protegidos con credencial de operador (`operator_token`), y tool `cancelar_pedido` para cancelar órdenes activas y liberar stock comprometido (RF-20, CA-48 a CA-52).
- Soporte para catálogo local configurable mediante variable `CATALOG_FILE` en `Settings` con fallback a `data_example/catalogo.csv` (etapa 02, RF-07, CA-53).
- Presentación de precios unitarios, subtotales y total en el resumen del borrador, confirmación de orden y propuesta del agente (cierre de decisión D-04, RF-11).
- Descuento transaccional de stock en `internal/services/inventory_service.py`: las órdenes activas en SQLite descuentan unidades del catálogo neto disponible, evitando la sobreventa (*overselling*) entre clientes concurrentes; función `cancel_order` en `internal/services/order_service.py` para liberar stock al cancelar (RF-14, RF-20, CA-16).
- Registro de las fuentes privadas del issue #2: inventario, catálogo con stock ficticio para pruebas, seguimiento de pedidos y evaluación del agente diferida, sin publicar datos reales.
- Flujo de ramas con releases, hotfixes y backports, documentado en `CONTRIBUTING.md`: cada PR se rechaza si el nombre de la rama no sigue el flujo o si no suma su línea a este changelog.
- CI en cada PR y push a `develop` y `main`: lint con ruff, tests, build de la imagen con arranque de la API y escaneo de secretos en todo el historial.
- Cada tag `vX.Y.Z` en `main` publica la imagen de esa versión en GitHub Container Registry, y producción la corre con `docker-compose.prod.yml`.
- Docker: `Dockerfile` y `docker-compose.yml` para levantar la API y correr los tests sin instalar Python.
- API con FastAPI: `/health`, endpoints del chat web y verificación del webhook de WhatsApp.
- Contrato de datos del pedido: borrador (`OrderDraft`) separado de la orden confirmada (`Order`), con precios en `Decimal` y validación del contenido según el tipo de mensaje.
- Contrato de datos del catálogo (`Product`, `StockCheck`) en `internal/schemas`.
- Agente Norma con interfaz de LLM neutral (proveedor a definir) e instrucciones iniciales.
- Esqueletos de tools (catálogo, stock, borradores), servicios, Google Sheets y base interna.
- Configuración por variables de entorno (`.env.example`) y tests iniciales.
- Dependencias de desarrollo separadas en `requirements-dev.txt`, con cotas de versión mayor.
- Datos de ejemplo inventados en `datos_ejemplo/`: catálogo (formato de la Sheet 1), pedidos (formato de la Sheet 2) y mensajes de clientes con el resultado esperado del agente, validados por `tests/test_datos_ejemplo.py`.
- `Product.min_stock` y `Product.is_low_stock` para marcar productos con stock bajo en el dashboard.
- Bot de Telegram para probar la beta entre el equipo (`python -m internal.telegram_polling` o webhook en `/webhook/telegram`) con lista blanca de usuarios, cliente de Gemini (free tier) para interpretar pedidos de texto y `python -m internal.verificar_claves`. `media_url` admite la referencia `telegram:<file_id>` solo en ese canal. Variables nuevas en `.env`: `TELEGRAM_*`, `LLM_VISION_*`; `httpx` pasa a ser dependencia de producción.
- Función pura `interpretar_palabra_clave` en `internal/services/palabras_clave.py` para reconocer `OK`, `CONFIRMAR` y `CORREGIR` (sin tildes, mayúsculas ni espacios sobrantes), y constante `AYUDA_CONFIRMAR` para instrucciones de confirmación sin botones en WhatsApp (RF-13, CA-17, CA-18).
- Reglas de orden en `internal/services/order_service.py`: `format_order_id` y `parse_order_id` con formato correlativo `ORD-0001`, `can_transition` para validar la máquina de estados según RF-20, y `parse_status` tolerante a mayúsculas y espacios; validación estricta de `order_id` en `Order` (RF-17, RF-20).
- Spec del MVP en `docs/spec/`: visión, arquitectura, flujos, contratos de datos, requisitos, roadmap y criterios de aceptación, más el diseño del agente (loop, estados de la conversación, tools, errores y memoria) y la forma de trabajar spec primero (SDD).
- Spec del agente: skills y reglas de negocio (BR) guardadas en el repositorio, tools `leer_skill` y `leer_br`, permisos por skill y fases L1 y L2 del turno (RF-33, RF-34, CA-37 a CA-40). El tope pasa de 6 a 15 iteraciones por turno (D-07, RNF-12).
- Primer loop del agente con LangGraph: `descubrir_tools`, `llm`, `tools` y `pausa`, con estado guardado en SQLite para retomar una conversación aunque el proceso se reinicie. Variable opcional nueva: `CHECKPOINT_DB`.
- Tools `search_products`, `check_stock`, `leer_skill`, `leer_br`, `respond_to_customer` y `armar_pedido`, y skills `read_catalogo`, `aclarar`, `fuera_de_catalogo`, `corregir_pedido`, `armar_pedido` e `informar`. Por ahora el catálogo sale de `datos_ejemplo/catalogo.csv` y el pedido queda en la base local.
- `python -m internal.evaluar_agente` corre los casos de `datos_ejemplo/mensajes.json` con el LLM real.
- La búsqueda de productos devuelve `score`, no aproxima números y no trae productos sin parecido real (RF-07, RF-08).

### Cambiado

- La carpeta de datos de prueba `datos_ejemplo/` pasa a llamarse `data_example/` y se actualizan todas sus referencias en código, Docker y documentación.

- El agente pasa de 6 skills a 3: `tomar_pedido`, `confirmar_pedido` e `informar`. Las reglas de negocio no cambian y se siguen leyendo solo cuando hacen falta. Sin propuesta vigente, el índice de skills no muestra `confirmar_pedido`.
- `OK` o `CONFIRMAR` exactos registran la propuesta vigente sin llamar al LLM, y una aceptación con condiciones («dale, pero al lunes») ya no cuenta como aceptación (RF-13, CA-18, BR-06).
- **El cliente confirma conversando:** una respuesta como «dale» registra el pedido si acepta la propuesta vigente, y el backend solo registra lo que validó antes (RF-11, RF-13, CA-17, CA-18).
- El agente pasa a usar LangChain: `agent/llm.py` reemplaza a `agent/llm_client.py`.
- La carpeta `backend/` pasa a llamarse `internal/`, y los comandos cambian en consecuencia (`uvicorn internal.main:app`, `python -m internal.telegram_polling`). Las skills del agente viven en `internal/skills/<nombre>/`, con su `skill.md` y sus BR en `BR/`. `instructions.md` pasa a ser `internal/agent/system_prompt.md`, reescrito para leer skills y BR.
- La base SQLite pasa a `data/norma.db` (antes `norma.db` en la raíz y, con Docker, el volumen `norma-data`): para conservar datos locales, moverlos a `data/`.
- Python 3.12 como versión mínima.
- `backend/Schema` pasa a `backend/schemas` y `test` a `tests`, según la arquitectura.
- Roadmap actualizado a un equipo de 3 integrantes.

### Corregido

- Contratos del catálogo: `Product` rechaza stock negativo (`stock ≥ 0`) y `StockCheck` exige cantidad solicitada positiva (`requested > 0`) y disponibilidad no negativa (`available ≥ 0`) (RF-14, RF-10).
- Validación estricta en contratos de pedidos: OrderItem rechaza sku y product_name vacíos, recorta espacios en blanco y limita quantity a MAX_QUANTITY = 999 (RF-08, RNF-02); IncomingMessage, OrderDraft y Order rechazan customer_id vacío y recortan espacios.
- Alineación del contrato de pedidos con la spec (Issue #12, C-13, CA-14): se suma ersion: int (iniciando en 1, ge=1) a OrderDraft y sheets_synced: bool = False a Order; el endpoint POST /chat/drafts/{id}/{action} ahora exige el parámetro de consulta ersion.
- Los PR ya no dan conflicto en `CHANGELOG.md` cuando otro PR sumó su línea en el mismo lugar: `.gitattributes` conserva las líneas de los dos lados (`merge=union`).

### Seguridad

- Validación de `media_url` en `IncomingMessage` contra SSRF: solo permite URLs con esquema `https://` y host público, rechazando `localhost`, direcciones IP privadas o internas (en formato canónico o alternativo decimal, octal, hex) y URLs de más de 2048 caracteres (RNF-02, CA-04).
- Hooks de seguridad locales con `pre-commit` y verificación obligatoria en CI (`secretos.yml`): frenan secretos (`gitleaks` v8.28.0, claves privadas), archivos pesados (>500 KB), archivos prohibidos (`.opus`, `.m4a`, `.vcf`, `_chat.txt`, `.zip`, `.env*`, `credentials/`, `*service-account*.json`) y números telefónicos argentinos en archivos de datos, con mensajes en español y salidas redactadas sin exponer secretos.
- Configuración para asistentes de IA (`.claude/settings.json`, `.geminiignore` y `AGENTS.md`) bloqueando la lectura de `.env`, `.env.*`, `credentials/` y `data/`.
- Documentación en `CONTRIBUTING.md` sobre gestión de secretos en desarrollo y producción, uso de correos `noreply` en commits y procedimiento ante filtraciones.
- El webhook de WhatsApp valida la firma `X-Hub-Signature-256` de Meta (nueva variable `WHATSAPP_APP_SECRET`) y compara el token de verificación en tiempo constante.

[Sin publicar]: https://github.com/FernandoMRestelli/norma-agentic-ai/compare/main...develop

### Eliminado

- Botones Confirmar / Corregir: el cliente confirma escribiendo en todos los canales. Salen del bot de Telegram y de la API, junto con `POST /chat/drafts/{draft_id}/{action}`.
