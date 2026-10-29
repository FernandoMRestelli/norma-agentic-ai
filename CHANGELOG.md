# Changelog

Todos los cambios relevantes del proyecto se registran en este archivo.

El formato sigue [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/)
y el proyecto usa [versionado semántico](https://semver.org/lang/es/).
Cómo mantenerlo: ver [CONTRIBUTING.md](CONTRIBUTING.md#changelog).

## [Sin publicar]

### Agregado

- Flujo de trabajo con ramas `main`, `develop` y ramas por tarea, documentado en `CONTRIBUTING.md`, con plantilla de Pull Request.
- CI con GitHub Actions que corre los tests en cada PR y push a `develop` y `main`.
- Docker: `Dockerfile` y `docker-compose.yml` para levantar la API y correr los tests sin instalar Python.
- API con FastAPI: `/health`, endpoints del chat web y verificación del webhook de WhatsApp.
- Contrato de datos del pedido: borrador (`OrderDraft`) separado de la orden confirmada (`Order`), con precios en `Decimal` y validación del contenido según el tipo de mensaje.
- Contrato de datos del catálogo (`Product`, `StockCheck`) en `backend/schemas`.
- Agente Norma con interfaz de LLM neutral (proveedor a definir) e instrucciones iniciales.
- Esqueletos de tools (catálogo, stock, borradores), servicios, Google Sheets y base interna.
- Configuración por variables de entorno (`.env.example`) y tests iniciales.
- Dependencias de desarrollo separadas en `requirements-dev.txt`, con cotas de versión mayor.

### Cambiado

- Python 3.12 como versión mínima.
- `backend/Schema` pasa a `backend/schemas` y `test` a `tests`, según la arquitectura.
- Roadmap actualizado a un equipo de 3 integrantes.

### Seguridad

- El webhook de WhatsApp valida la firma `X-Hub-Signature-256` de Meta (nueva variable `WHATSAPP_APP_SECRET`) y compara el token de verificación en tiempo constante.

[Sin publicar]: https://github.com/FernandoMRestelli/norma-agentic-ai/compare/main...develop
