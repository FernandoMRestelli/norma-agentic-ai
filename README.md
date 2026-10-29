# Norma Agentic AI

Agente que recibe pedidos de compra de los clientes por **texto, audio o imagen** (chat web o WhatsApp),
los interpreta contra el catálogo, muestra un borrador para **confirmar o corregir** y, solo tras la
confirmación, registra la orden para su seguimiento.

> **El LLM interpreta; el backend valida y registra; el cliente confirma; el equipo controla la operación.**

## Documentación

- [Arquitectura](docs/architecture.md): piezas del sistema y dónde vive cada archivo
- [Roadmap](docs/roadmap.md): etapas del MVP
- [Cómo trabajamos con Git](CONTRIBUTING.md): ramas, Pull Requests, releases y changelog
- [Changelog](CHANGELOG.md): historial de cambios por versión

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
La base SQLite se guarda en el volumen `norma-data` (se borra con `docker compose down -v`).

## Opción B: con Python local

Requiere **Python 3.12 o superior** (en macOS: `brew install python@3.12`).

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn backend.main:app --reload
pytest
```

## Estructura

```text
backend/
├── main.py            # app FastAPI y rutas
├── config.py          # variables de entorno
├── api/               # entradas: chat web y webhook de WhatsApp
├── agent/             # agente Norma, instrucciones e interfaz neutral del LLM
├── tools/             # acciones que el agente puede usar (catálogo, stock, borradores)
├── services/          # preprocesamiento, interpretación, confirmación, órdenes, Google Sheets
├── schemas/           # contratos de datos: pedido (borrador vs. orden) y catálogo
└── database/          # base interna
frontend/src/          # Chat (tarjeta Confirmar / Corregir) y Dashboard
tests/
docs/
```
