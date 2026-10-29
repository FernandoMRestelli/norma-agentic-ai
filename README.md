# Norma Agentic AI

Agente que recibe pedidos de compra de los clientes por **texto, audio o imagen** (chat web o WhatsApp),
los interpreta contra el catálogo, muestra un borrador para **confirmar o corregir** y, solo tras la
confirmación, registra la orden para su seguimiento.

> **El LLM interpreta; el backend valida y registra; el cliente confirma; el equipo controla la operación.**

## Documentación

- [Arquitectura](docs/architecture.md): piezas del sistema y dónde vive cada archivo
- [Roadmap](docs/roadmap.md): etapas del MVP
- [Cómo trabajamos con Git](CONTRIBUTING.md): ramas, Pull Requests y releases

## Puesta en marcha

Requiere Python 3.9 o superior.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # completar los valores
```

Levantar la API (documentación interactiva en http://localhost:8000/docs):

```bash
uvicorn backend.main:app --reload
```

Correr los tests:

```bash
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
├── schemas/           # contrato de datos del pedido (borrador vs. orden)
└── database/          # base interna
frontend/src/          # Chat (tarjeta Confirmar / Corregir) y Dashboard
tests/
docs/
```
