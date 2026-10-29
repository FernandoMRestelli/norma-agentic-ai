# Norma Agentic AI

Agente que recibe pedidos de compra de los clientes por **texto, audio o imagen** (chat web o WhatsApp),
los interpreta contra el catálogo, muestra un borrador para **confirmar o corregir** y, solo tras la
confirmación, registra la orden para su seguimiento.

> **El LLM interpreta; el backend valida y registra; el cliente confirma; el equipo controla la operación.**

## Documentación

- [Spec del MVP](docs/spec/README.md): qué tiene que hacer el sistema (fuente de verdad), con la [spec del agente](docs/spec/agente.md)
- [Arquitectura](docs/architecture.md): piezas del sistema y dónde vive cada archivo
- [Roadmap](docs/roadmap.md): etapas del MVP
- [Cómo trabajamos](CONTRIBUTING.md): ramas, PR, reglas del CI, changelog, versiones, releases y producción
- [Changelog](CHANGELOG.md): historial de cambios por versión
- [Mejoras](MEJORAS.md): estado actual, limitaciones y próximos pasos

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
El código de `backend/` está montado en el contenedor, así que los cambios se recargan solos.

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
uvicorn backend.main:app --reload
ruff check .
python -m pytest -q
```

En producción no se usa ninguna de las dos opciones: corre la imagen publicada de una release
(ver [Producción](CONTRIBUTING.md#producción)).

## Cómo funciona

1. El mensaje del cliente entra por `api/` (chat web o webhook de WhatsApp, con la firma de Meta validada).
2. `services/message_service.py` lo convierte en texto (transcribe audio, lee imágenes).
3. El agente (`agent/`) interpreta el pedido y usa las `tools/` para buscar productos y stock en el catálogo.
4. Con eso arma un **borrador** (`OrderDraft`) y `validation_service.py` le muestra el resumen al cliente.
5. Solo cuando el cliente confirma, `order_service.py` revalida el stock y registra **una** orden en el Google Sheet de seguimiento.

El detalle de cada pieza está en [docs/architecture.md](docs/architecture.md).

## Estructura

| Archivo o carpeta | Rol |
|---|---|
| `backend/main.py` | App FastAPI y rutas; la versión sale de `APP_VERSION`, que fija la imagen de cada release |
| `backend/config.py` | Configuración y secretos por variables de entorno (`.env`) |
| `backend/api/` | Entradas: chat web y webhook de WhatsApp |
| `backend/agent/` | Agente Norma, sus instrucciones e interfaz neutral del LLM |
| `backend/tools/` | Acciones que el agente puede usar (catálogo, stock, borradores) |
| `backend/services/` | Preprocesamiento, interpretación, confirmación, órdenes y Google Sheets |
| `backend/schemas/` | Contratos de datos: pedido (borrador vs. orden) y catálogo |
| `backend/database/` | Base interna (SQLite en `data/`) |
| `frontend/src/` | Chat (tarjeta Confirmar / Corregir) y Dashboard |
| `tests/conftest.py` | Aísla los tests: sin `.env`, base temporal y sin red |
| `tests/datos_sinteticos.py` | Catálogos, mensajes y borradores deterministas para los tests |
| `tests/test_*.py` | Un archivo por módulo, más `test_invariantes.py` para todos los canales y tipos de mensaje |
| `datos_ejemplo/` | Catálogo, pedidos y mensajes inventados con el formato de las planillas (ver su README) |
| `data/` | Bases y datos locales; git la ignora salvo el `.gitkeep` |
| `docs/spec/` | Spec del MVP, spec del agente y plantilla para las specs de cada etapa |
| `docs/` | Arquitectura y roadmap |
| `.github/workflows/` | CI: tests y lint, secretos, reglas de PR e imagen de release |
| `.github/scripts/verificar_reglas_pr.sh` | Nombre de rama y entrada de changelog de cada PR |
| `Dockerfile` | Imagen de la app; lleva todo el código, así una release corre exactamente su tag |
| `docker-compose.yml` | Desarrollo: monta el código y `data/` encima de la imagen |
| `docker-compose.prod.yml` | Producción: imagen publicada de una versión, solo monta `data/` y credenciales |
| `pyproject.toml` | Configuración de ruff |
| `CONTRIBUTING.md` / `CHANGELOG.md` / `MEJORAS.md` | Flujo de trabajo, historial de cambios y estado del proyecto |
