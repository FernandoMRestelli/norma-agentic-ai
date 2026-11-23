# Norma Agentic AI

Agente que recibe pedidos de compra de los clientes por **texto, audio o imagen** (chat web o WhatsApp),
los interpreta contra el catálogo, le propone el pedido por el mismo chat y, solo cuando el cliente
lo acepta, registra la orden para su seguimiento.

> **El LLM interpreta; el backend valida y registra; el cliente confirma; el equipo controla la operación.**

## Documentación

- [Spec del MVP](docs/spec/README.md): qué tiene que hacer el sistema (fuente de verdad), con la [spec del agente](docs/spec/agente.md)
- [Arquitectura](docs/architecture.md): piezas del sistema y dónde vive cada archivo
- [Roadmap](docs/roadmap.md): etapas del MVP
- [Cómo trabajamos](CONTRIBUTING.md): ramas, PR, reglas del CI, changelog, versiones, releases y producción
- [Changelog](CHANGELOG.md): historial de cambios por versión
- [Mejoras](MEJORAS.md): estado actual, limitaciones y próximos pasos
- [Fuentes de datos preparadas](docs/data/fuentes.md): catálogo, pedidos privados, accesos y alcance del issue #2

## Configuración

```bash
cp .env.example .env   # completar los valores
```

Las credenciales de Google (cuenta de servicio) van en `credentials/`, que no se sube a GitHub.

## Opción A: con Docker (recomendada)

Solo requiere [Docker Desktop](https://www.docker.com/products/docker-desktop/), sin instalar Python.

```bash
docker compose up --build
```

La API queda en http://localhost:8000 (documentación interactiva en http://localhost:8000/docs).
El código de `internal/` está montado en el contenedor, así que los cambios se recargan solos.

Correr los tests:

```bash
docker compose run --rm api pytest
```

Reconstruir la imagen solo hace falta si cambian los `requirements*.txt` o el `Dockerfile`.
La imagen de producción se construye sin dependencias de desarrollo: `docker build -t norma-agentic-ai .`
La base SQLite se guarda en `data/norma.db`, montada desde la carpeta del repo (git la ignora).

## Opción B: con Python local

Requiere **Python 3.12 o superior** (en macOS: `brew install python@3.12`).

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn internal.main:app --reload
ruff check .
python -m pytest -q
```

En producción no se usa ninguna de las dos opciones: corre la imagen publicada de una release
(ver [Producción](CONTRIBUTING.md#producción)).

## Bot de Telegram (beta)

Para probar entre el equipo, cada integrante le escribe pedidos simulados a un bot de Telegram
(detalle en la [spec del canal](docs/spec/canal-telegram.md)). Hoy el bot recibe, filtra y responde; interpretar
el pedido llega con el agente (etapa 03).

Hay dos tipos de bot:

| Bot | Quién lo crea | Para qué | Lista blanca |
|---|---|---|---|
| **De desarrollo** | Cada integrante, el suyo | Programar y probar en tu máquina sin pisar a los demás | Solo tu id |
| **Beta compartido** | Uno para el equipo, cuando el agente tome pedidos (etapa 03) | Que los tres pidan al mismo negocio y los pedidos caigan en la misma base y planilla | Los tres ids |

El bot beta corre en **un solo lugar** (idealmente un servidor, D-03): Telegram admite un solo proceso de polling
por token y responde `409 Conflict` al segundo.

1. **Token de tu bot de desarrollo**: en Telegram, `/newbot` en [@BotFather](https://t.me/BotFather).
   Va en `TELEGRAM_BOT_TOKEN`.
2. **API key del LLM**: gratis en [OpenRouter](https://openrouter.ai/keys). Va en `LLM_API_KEY`
   (`LLM_PROVIDER=openrouter` y `LLM_MODEL` ya vienen en `.env.example`). Si el modelo no usa las tools, la
   alternativa es Gemini con una clave de [Google AI Studio](https://aistudio.google.com/apikey) (está comentada
   en `.env.example`). Los free tier pueden usar lo que se manda para entrenar: **solo datos simulados**.
3. Verificar las claves (no las muestra):

   ```bash
   python -m internal.verificar_claves     # o: docker compose run --rm api python -m internal.verificar_claves
   ```

4. Levantar el bot:

   ```bash
   python -m internal.telegram_polling     # o: docker compose --profile bot up bot
   ```

5. **Lista blanca**: escribile a tu bot, que te responde con tu id. Ponelo en `TELEGRAM_ALLOWED_USER_IDS` y reiniciá
   el bot. En el bot beta van los ids de los tres, separados por coma.

Cada uno ya tiene su propio chat aunque el bot sea compartido: Telegram abre un chat privado por persona, y el
sistema trata a cada integrante como un cliente distinto (`telegram:<id>`).

## Monitoreo del agente con MLflow (opcional)

[MLflow](https://mlflow.org/) guarda cada turno del agente como una traza: cada llamada al LLM y cada tool, con
iteraciones, tokens, latencia, skills leídas, proveedor, modelo y versión del prompt. Está apagado por defecto y es
solo para desarrollo (detalle en la [spec del agente §10.1](docs/spec/agente.md#101-observabilidad-con-mlflow)).

1. Levantar el servidor. Queda en http://localhost:5001 (en macOS el 5000 lo ocupa AirPlay) y guarda los datos en
   `data/mlflow/`:

   ```bash
   docker compose --profile mlflow up -d mlflow
   ```

   Con Python local:

   ```bash
   mkdir -p data/mlflow
   mlflow server --port 5001 --backend-store-uri sqlite:///data/mlflow/mlflow.db --artifacts-destination data/mlflow/artifacts
   ```

2. En `.env`, poner `MLFLOW_ENABLED=true` y reiniciar la API o el bot. Con docker compose, la URL del servidor ya
   está fijada; con Python local se usa `MLFLOW_TRACKING_URI` (por defecto `http://localhost:5001`).
3. Escribirle al bot y abrir http://localhost:5001 → experimento `norma-agente` → **Traces**. Cada traza es un
   turno; los de una misma conversación comparten la sesión (`conversation_id`).

Las trazas **no guardan el texto** de los mensajes (RNF-03). Para depurar un caso con datos simulados,
`MLFLOW_TRACE_TEXT=true` suma el mensaje, la respuesta y el prompt completo; solo funciona con `APP_ENV=development`.

Para comparar prompts o proveedores, la evaluación con los casos de `data_example/mensajes.json` guarda cada corrida
como un run con el porcentaje de aciertos, los tokens y la latencia:

```bash
python -m internal.evaluar_agente --mlflow --pausa 30
```

En MLflow, el experimento → **Runs** → seleccionar dos corridas → **Compare**.

## Lectura de imágenes a JSON (experimental)

`internal/services/vision_service.py` lee una foto o captura de un pedido y devuelve un JSON (`ImagenLeida` en
`internal/schemas/vision_schema.py`): el texto completo, un renglón por producto con su cantidad, marca y
presentación, y las partes dudosas. Con `LLM_VISION_PROVIDER` configurado, el bot de Telegram baja cada foto, la lee
y le pasa el texto al agente; sin configurar, le pide al cliente que lo escriba (RF-32).

Hay dos caminos, con la misma salida:

| | Proveedor | Necesita |
|---|---|---|
| **API de frontera** | `openai` (ej. `gpt-6-luna`), `openrouter` o `gemini` | `LLM_VISION_API_KEY`. Salida estructurada con JSON Schema |
| **Modelo local de Hugging Face** | `huggingface` (por defecto `Qwen/Qwen3-VL-2B-Instruct`) | `pip install -r requirements-vision-local.txt` y ~5 GB de disco; sin key ni costo por imagen. Corre con Python local (en Docker para Mac no hay GPU y va lento) |

```bash
# Una imagen (usa LLM_VISION_* de .env, o los flags)
python -m internal.leer_imagen tests/fixtures/imagenes_pedidos/captura-chat.png --proveedor openai --modelo gpt-6-luna

# Comparar modelos con las imágenes ficticias de tests/fixtures/imagenes_pedidos/
python -m internal.comparar_vision --modelo openai:gpt-6-luna --modelo huggingface:Qwen/Qwen3-VL-2B-Instruct \
  --precio 0.10,0.50 --salida data/comparacion-vision.json
```

Primera prueba de Qwen3-VL 2B en una Mac M5 (16 GB): leyó los 17 productos de las 6 imágenes, con JSON válido
en todas, en 16 s por imagen en promedio (3 a 28 s) y 9 s de carga al arrancar. Suma saludos y encabezados como
renglones sin cantidad; al agente no le afecta porque recibe el texto completo.

`--precio` es USD por millón de tokens de entrada y de salida, para estimar el costo por imagen de los modelos por
API. Lo que se manda a una API puede quedar guardado del lado del proveedor: **solo imágenes de prueba** (RNF-03).

## Cómo funciona

1. El mensaje del cliente entra por `api/` (chat web, webhook de WhatsApp con la firma de Meta validada, o el bot de Telegram de la beta).
2. `services/message_service.py` lo convierte en texto (transcribe audio, lee imágenes).
3. El agente (`agent/`) interpreta el pedido y usa las `tools/` para buscar productos y stock en el catálogo.
4. Con eso arma un **borrador** (`OrderDraft`) y `validation_service.py` le muestra el resumen al cliente.
5. Solo cuando el cliente confirma, `order_service.py` revalida el stock y registra **una** orden en el Google Sheet de seguimiento.

El detalle de cada pieza está en [docs/architecture.md](docs/architecture.md).

## Estructura

| Archivo o carpeta | Rol |
|---|---|
| `internal/main.py` | App FastAPI y rutas; la versión sale de `APP_VERSION`, que fija la imagen de cada release |
| `internal/config.py` | Configuración y secretos por variables de entorno (`.env`) |
| `internal/api/` | Entradas: chat web, webhook de WhatsApp y webhook de Telegram |
| `internal/agent/` | Agente Norma, sus instrucciones e interfaz neutral del LLM (Gemini, Groq u OpenRouter) |
| `internal/skills/` | Skills del agente: cada una con su `skill.md` y sus BR |
| `internal/tools/` | Acciones que el agente puede usar (catálogo, stock, borradores) |
| `internal/services/` | Orquestador, preprocesamiento, interpretación, confirmación, órdenes, Google Sheets y Telegram |
| `internal/telegram_polling.py` | Bot de Telegram por polling, para desarrollo y la beta |
| `internal/verificar_claves.py` | Prueba las claves de Telegram y del LLM sin mostrarlas |
| `internal/schemas/` | Contratos de datos: pedido (borrador vs. orden) y catálogo |
| `internal/database/` | Base interna (SQLite en `data/`) |
| `frontend/src/` | Chat y Dashboard |
| `tests/conftest.py` | Aísla los tests: sin `.env`, base temporal y sin red |
| `tests/datos_sinteticos.py` | Catálogos, mensajes y borradores deterministas para los tests |
| `tests/test_*.py` | Un archivo por módulo, más `test_invariantes.py` para todos los canales y tipos de mensaje |
| `data_example/` | Catálogo, pedidos y mensajes inventados con el formato de las planillas (ver su README) |
| `data/` | Bases y datos locales; git la ignora salvo el `.gitkeep` |
| `docs/spec/` | Spec del MVP, spec del agente, spec del canal Telegram y plantilla para las specs de cada etapa |
| `docs/` | Arquitectura y roadmap |
| `.github/workflows/` | CI: tests y lint, secretos, reglas de PR e imagen de release |
| `.github/scripts/verificar_reglas_pr.sh` | Nombre de rama y entrada de changelog de cada PR |
| `Dockerfile` | Imagen de la app; lleva todo el código, así una release corre exactamente su tag |
| `docker-compose.yml` | Desarrollo: monta el código y `data/` encima de la imagen |
| `docker-compose.prod.yml` | Producción: imagen publicada de una versión, solo monta `data/` y credenciales |
| `pyproject.toml` | Configuración de ruff |
| `CONTRIBUTING.md` / `CHANGELOG.md` / `MEJORAS.md` | Flujo de trabajo, historial de cambios y estado del proyecto |
