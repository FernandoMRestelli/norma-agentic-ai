# Changelog

Todos los cambios relevantes del proyecto se registran en este archivo.

El formato sigue [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/)
y el proyecto usa [versionado semántico](https://semver.org/lang/es/).
Cómo mantenerlo: ver [CONTRIBUTING.md](CONTRIBUTING.md#changelog).

## [Sin publicar]

### Agregado

- Dashboard operativo con autenticación (RF-22 a RF-25, RF-61, RF-62, CA-24, CA-25, CA-117 a CA-120, Etapa 06): endpoints `/dashboard/summary` (con filtro por período `today`/`yesterday`/`7d`/`30d`/`all`), `/dashboard/orders`, `/dashboard/stock`, `/dashboard/conversations` y `/dashboard/metrics` protegidos con token de operador; SPA interactiva servida en `/dashboard/ui` (modo blanco/negro, filtro de período en resumen operativo, auto-refresh cada 60s), pestaña **Ventas & KPIs** (evolución de monto vendido, pedidos y productos vendidos por día/semana/mes/año con visibilidad seleccionable de series, desglose por categoría/marca y ranking de productos) y gestión de estados de pedidos (`Despachado`, `Retirado`, `Entregado`, `Cancelado`) con descuento físico de stock en el catálogo (Issue #61). La base es la fuente de los pedidos: los que solo están en el CSV de pedidos se ven como solo lectura, y una fila con fecha ilegible se saltea. La pestaña Conversaciones lista solo las derivadas.
- Ciclo de vida de órdenes extendido (RF-20, RF-59, RF-60, CA-113 a CA-116, Issue #61): estados `Despachado` y `Entregado` en `POST /orders/{order_id}/status`, con historial. Al marcar un pedido como `Retirado` o `Despachado` se descuenta su stock del catálogo local una sola vez, junto con el cambio de estado: si falla la base, el catálogo vuelve a como estaba. Con el catálogo en Sheet 1 o en `data_example/` no se descuenta. El estado también se escribe en el CSV de pedidos y en Sheet 2; si la planilla falla, queda para el reintento. Al desplegar: la base pasa sola a la versión 2 del esquema (columna `orders.stock_applied`).
- La búsqueda de productos tolera errores de tipeo y de sonido («pantne», «coleston», «zedal»), abreviaturas y variantes del rubro («sh», «champú», «ACOND.»), medidas escritas de otra forma (`x900` = `900 ml`, `1k` = `1 kg` = `1000 g`, `1 l` = `1000 cc`) y tonos sin punto (`821` = `8.21`), sin aproximar nunca los números. La cantidad escrita al principio («2 mascara argan x 1k») no cuenta como una medida que no coincide, y «baño de crema» encuentra los baños del catálogo. Cuando no hay una coincidencia confiable, `search_products` devuelve `sugerencias` para que el agente pregunte «¿quisiste decir…?», y `corregido` dice qué palabras interpretó como otra. El catálogo normalizado queda en memoria y el stock se sigue leyendo en cada búsqueda.
- Plan de mejora del asistente en `docs/plan-mejora-asistente.md`: diagnóstico de lo que está flojo (búsqueda con errores de tipeo, guardrails, guardado de pedidos, planillas y skills) y los cinco frentes de trabajo, cada uno con su rama, su spec y sus IDs reservados.
- Spec de la toma de pedidos por texto en `docs/spec/toma-de-pedidos-por-texto.md`: qué hace hoy el agente, los cinco puntos donde más conviene reforzarlo (evaluación, iteraciones del grafo, pedidos largos, memoria entre turnos y CSV de pedidos) y una mejora propuesta para cada uno, con sus contratos, requisitos (RF-63 a RF-83) y criterios de aceptación (CA-121 a CA-142). Es solo documentación: no cambia el comportamiento.
- Guardrails en código (`docs/spec/guardrails.md`): antes de llegar al cliente, cada mensaje del asistente, incluido el texto suelto, se revisa para que no revele stock, no nombre SKU ni datos internos, no diga que el pedido está hecho, no invente precios, no prometa descuentos, envíos, plazos ni formas de pago y no use `**`; si falla, el asistente lo reescribe, y al tercer intento sale un mensaje fijo. Además, un mensaje de más de 4000 caracteres o más de 10 mensajes por minuto de un mismo cliente reciben una respuesta fija sin llamar al LLM, el nombre del perfil entra limpio al prompt y los intentos de cambiar las reglas quedan en el log y en la traza, sin el texto. No hay variables nuevas que configurar.
- Google Sheets y Excel (`docs/spec/planillas.md`, RF-50 a RF-54): con `CATALOG_SHEET_ID` el catálogo se lee de Sheet 1 con 5 minutos de caché (hasta 1 hora con la última copia buena si Google no responde) y la confirmación lee fresco; con `ORDERS_SHEET_ID` cada pedido se agrega una sola vez en Sheet 2, además del CSV local, y tras una falla la confirmación no espera a Google durante 1 minuto; la API reintenta cada 3 minutos los pedidos que no llegaron; `GET /orders/export` descarga los pedidos en `.xlsx` (hojas Órdenes y Líneas); la API y el bot ya no escriben el CSV de pedidos a la vez, y `python -m internal.verificar_claves` prueba el acceso a las dos planillas. Sin esas variables todo sigue como antes con los CSV. **Para desplegar con planillas:** guardar el JSON de la cuenta de servicio en `~/norma-prod/credentials/google-service-account.json`, compartir Sheet 1 como Lector y Sheet 2 como Editor con su `client_email`, y completar en `.env` `CATALOG_SHEET_ID` y `ORDERS_SHEET_ID` (opcionales: `CATALOG_WORKSHEET` y `ORDERS_WORKSHEET`). La imagen suma la dependencia `openpyxl`.

### Cambiado

- El asistente habla como alguien del mostrador de la distribuidora: el system prompt y las skills dicen cada regla una sola vez y traen ejemplos de respuestas por situación, con cierres variados. En cada turno lee primero la skill, porque las tools para responderle al cliente aparecen recién al leerla, y no le contesta con texto suelto. Antes de proponer un producto que la búsqueda encontró de forma aproximada pregunta «¿es este?» (BR-09), anota las observaciones del pedido sin aceptar condiciones de envío o pago (BR-10), y `informar` responde horarios, entrega, pagos y mínimo con `internal/skills/informar/datos_negocio.md` (hoy todo pendiente de confirmar, V-06). `evaluar_agente` corre conversaciones de varios turnos, chequea el estilo de cada respuesta y con `--conversaciones` las deja en un `.md` para leerlas. No hay nada que migrar al desplegar.
- Los pedidos guardados dicen quién pidió (el nombre del perfil), qué pidió línea por línea, con las observaciones del cliente (por ejemplo el tono de una coloración), cuánto suma y por qué estados pasó, y el operador puede marcarlos como `Preparado` o `Retirado` con `POST /orders/{order_id}/status`. Las propuestas, los mensajes de Telegram ya procesados y el historial de estados también quedan en la base, y la planilla de pedidos suma las columnas `nombre_cliente` y `total` al final. **Cambia el formato de la base:** al arrancar, la API y el bot la actualizan solos y copian las líneas de las órdenes que ya tenías, sin perder ninguna. **Para desplegar:** antes de levantar la versión nueva, frená la API y el bot y copiá `~/norma-prod/data/norma.db` (por ejemplo a `norma.db.antes-0.2`); es la única forma de volver atrás.
- `.env.example` sigue el orden y los comentarios del `.env` de producción, y trae OpenRouter como LLM de texto por defecto, con Gemini comentado como alternativa. No cambia nada para quien ya tiene su `.env`.
- La spec del MVP, la del canal Telegram, `architecture.md`, `CONTRIBUTING.md` y `README.md` coinciden con el código de la 0.1.1: valores de `kind`, base interna, componentes y endpoints, proveedores de LLM y estado de los requisitos y las etapas.

## [0.1.1] - 2026-11-10

Producción ahora levanta el bot de Telegram. Con la 0.1.0, `docker-compose.prod.yml` arrancaba solo la API y el bot no respondía. Mientras no haya un servidor siempre prendido (D-03), cada integrante corre su producción en el Docker de su máquina.

### Corregido

- `docker-compose.prod.yml` levanta también el bot de Telegram por polling, con la misma imagen y el mismo `data/` que la API; en la 0.1.0 solo arrancaba la API. Cada integrante corre su producción en el Docker de su máquina, con su propio bot (`TELEGRAM_BOT_TOKEN` distinto en cada `.env`). **Para desplegar:** copiá el `docker-compose.prod.yml` de esta versión a `~/norma-prod` y, si levantaste el bot a mano con `docker run`, frenalo antes (`docker rm -f norma-prod-bot`) para que no queden dos procesos con el mismo token.

## [0.1.0] - 2026-11-10

Primera versión publicada. Reúne la base del proyecto y la beta del agente de pedidos para probar entre el equipo: el agente con LangGraph, skills y reglas de negocio guardadas en el repositorio, el bot de Telegram con lista blanca, el catálogo local en CSV, las órdenes en SQLite con volcado a CSV, el detalle de cada propuesta armado por el sistema y la observabilidad con MLflow (apagada por defecto). Todavía no hay un servidor de producción (D-03): esta versión publica la imagen, pero no se despliega.

### Agregado

- Observabilidad del agente con MLflow (RNF-07, CA-56, CA-57, D-10): con `MLFLOW_ENABLED=true`, cada turno queda como traza con un span por llamada al LLM y por tool, tokens, skills, proveedor, modelo y versión del prompt, sin el texto de los mensajes (RNF-03; `MLFLOW_TRACE_TEXT` lo suma solo en desarrollo). Apagado por defecto: no se importa mlflow. Servidor local con `docker compose --profile mlflow up`, y `python -m internal.evaluar_agente --mlflow` guarda cada evaluación como un run para comparar prompts y proveedores. La línea de log de cada turno suma `draft_id`, `order_id`, tools, errores, skills, latencia y tokens.
- Volcado local de órdenes confirmadas a archivo CSV configurable mediante `ORDERS_FILE` (por defecto `data/pedidos.csv`) siguiendo las columnas de Sheet 2 (`id_orden,fecha_hora,cliente,canal,productos,notas,estado`), con zona horaria de Argentina (`America/Argentina/Buenos_Aires`), escritura idempotente y reintento resiliente con `sync_pending_orders` ante fallas temporales (RF-19, CA-19, RNF-04).
- Proveedores de LLM de texto free tier adicionales en `internal/agent/llm.py`, seleccionables con `LLM_PROVIDER`: `groq` (Llama 3.3 70B y otros, límite amplio) y `openrouter` (API compatible con OpenAI). El import de cada paquete es diferido, así solo se carga el del proveedor configurado.
- Exploración y agregación de catálogo por marcas y categorías con la tool base `explorar_catalogo` y categorización de productos en `Product.category` (`categoria` en `catalogo.csv`), permitiendo responder consultas exploratorias abiertas en una sola iteración y sin agotar la tasa de tokens de Gemini (RF-35, CA-55).
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

- El system prompt describe el negocio (distribuidora de peluquería y belleza para peluquerías, farmacias y perfumerías) y cómo escriben los clientes sus pedidos por WhatsApp: listas por marca, abreviaturas y la notación de coloración `tono/cantidad`. Ante un saludo, el agente lo devuelve y pregunta en qué puede ayudar.
- Las propuestas muestran el detalle armado por el sistema: debajo del mensaje del agente, `validation_service.build_proposal_detail` agrega cada línea con su precio y el total desde el catálogo. El agente ya no repite las líneas, así entran pedidos de muchos renglones sin chocar con el tope de 600 caracteres. En un pedido largo, el agente propone lo que está claro y junta todas las dudas en un mismo mensaje (skill `tomar_pedido`, BR-03, CA-58).
- Cada skill declara en su encabezado un campo obligatorio `funcion` (qué logra y cómo termina el turno), y el índice del system prompt lo muestra junto al `cuando`, así el agente elige la skill sin tener que leerla. `confirmar_pedido` e `informar` aclaran que las preguntas por productos o precios van a `tomar_pedido`.
- Cada error de tool trae en `error.message` qué tiene que hacer el agente (buscar de nuevo, seguir BR-01, avisar al cliente sin proponer nada). El system prompt ya no lista los errores uno por uno: solo pide seguir ese mensaje, y así cubre también los que no estaban en la lista.
- Nuevo `docs/validaciones_pendientes.md` con los supuestos que el agente usa sin confirmar con el negocio, empezando por la notación `tono/cantidad`.
- La carpeta `data/` pasa a ser espacio de archivos de trabajo locales del equipo (CSV) que los asistentes de IA pueden leer; siguen bloqueadas las bases SQLite (`*.db`) y los `.vcf` con datos de clientes. Política actualizada en `AGENTS.md` y `.claude/settings.json`.
- `.env.example` vuelve a ser legible por los asistentes de IA (el `deny` de `.env.*` lo tapaba); los `.env` con secretos siguen bloqueados mediante patrones específicos en `.claude/settings.json`.
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

### Eliminado

- Botones Confirmar / Corregir: el cliente confirma escribiendo en todos los canales. Salen del bot de Telegram y de la API, junto con `POST /chat/drafts/{draft_id}/{action}`.

### Corregido

- Ocultamiento del número exacto de stock en `search_products` y `get_product` exponiendo únicamente `hay_stock: bool` para blindar la privacidad de stock frente al LLM; inmutabilidad de órdenes confirmadas evitando duplicaciones o mezclas en pedidos posteriores; corrección de voseo y proactividad comercial (RF-10, RF-15, RNF-11).
- Renderizado de negritas en Telegram convirtiendo `**` a formato Markdown con fallback a texto plano, eliminación de Spanglish y tecnicismos de catálogo («sizes», «presentación») por lenguaje natural rioplatense («tamaño», «medida») y preguntas de cierre comerciales amables (RNF-11, RF-10).
- Aislamiento estricto de la base SQLite en la suite de tests unitarios evitando contaminación en entornos de desarrollo local.
- Contratos del catálogo: `Product` rechaza stock negativo (`stock ≥ 0`) y `StockCheck` exige cantidad solicitada positiva (`requested > 0`) y disponibilidad no negativa (`available ≥ 0`) (RF-14, RF-10).
- Validación estricta en contratos de pedidos: OrderItem rechaza sku y product_name vacíos, recorta espacios en blanco y limita quantity a MAX_QUANTITY = 999 (RF-08, RNF-02); IncomingMessage, OrderDraft y Order rechazan customer_id vacío y recortan espacios.
- Alineación del contrato de pedidos con la spec (Issue #12, C-13, CA-14): se suma `version`: int (iniciando en 1, ge=1) a OrderDraft y sheets_synced: bool = False a Order; el endpoint POST /chat/drafts/{id}/{action} ahora exige el parámetro de consulta `version`.
- Los PR ya no dan conflicto en `CHANGELOG.md` cuando otro PR sumó su línea en el mismo lugar: `.gitattributes` conserva las líneas de los dos lados (`merge=union`).

### Seguridad

- Validación de `media_url` en `IncomingMessage` contra SSRF: solo permite URLs con esquema `https://` y host público, rechazando `localhost`, direcciones IP privadas o internas (en formato canónico o alternativo decimal, octal, hex) y URLs de más de 2048 caracteres (RNF-02, CA-04).
- Hooks de seguridad locales con `pre-commit` y verificación obligatoria en CI (`secretos.yml`): frenan secretos (`gitleaks` v8.28.0, claves privadas), archivos pesados (>500 KB), archivos prohibidos (`.opus`, `.m4a`, `.vcf`, `_chat.txt`, `.zip`, `.env*`, `credentials/`, `*service-account*.json`) y números telefónicos argentinos en archivos de datos, con mensajes en español y salidas redactadas sin exponer secretos.
- Configuración para asistentes de IA (`.claude/settings.json`, `.geminiignore` y `AGENTS.md`) bloqueando la lectura de `.env`, `.env.*`, `credentials/` y `data/`.
- Documentación en `CONTRIBUTING.md` sobre gestión de secretos en desarrollo y producción, uso de correos `noreply` en commits y procedimiento ante filtraciones.
- El webhook de WhatsApp valida la firma `X-Hub-Signature-256` de Meta (nueva variable `WHATSAPP_APP_SECRET`) y compara el token de verificación en tiempo constante.

[Sin publicar]: https://github.com/FernandoMRestelli/norma-agentic-ai/compare/v0.1.1...develop
[0.1.1]: https://github.com/FernandoMRestelli/norma-agentic-ai/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/FernandoMRestelli/norma-agentic-ai/releases/tag/v0.1.0
