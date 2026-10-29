# Changelog

Todos los cambios relevantes del proyecto se registran en este archivo.

El formato sigue [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/)
y el proyecto usa [versionado semántico](https://semver.org/lang/es/).
Cómo mantenerlo: ver [CONTRIBUTING.md](CONTRIBUTING.md#changelog).

## [Sin publicar]

### Agregado

- Flujo de ramas con releases, hotfixes y backports, documentado en `CONTRIBUTING.md`: cada PR se rechaza si el nombre de la rama no sigue el flujo o si no suma su línea a este changelog.
- CI en cada PR y push a `develop` y `main`: lint con ruff, tests, build de la imagen con arranque de la API y escaneo de secretos en todo el historial.
- Cada tag `vX.Y.Z` en `main` publica la imagen de esa versión en GitHub Container Registry, y producción la corre con `docker-compose.prod.yml`.
- Docker: `Dockerfile` y `docker-compose.yml` para levantar la API y correr los tests sin instalar Python.
- API con FastAPI: `/health`, endpoints del chat web y verificación del webhook de WhatsApp.
- Contrato de datos del pedido: borrador (`OrderDraft`) separado de la orden confirmada (`Order`), con precios en `Decimal` y validación del contenido según el tipo de mensaje.
- Contrato de datos del catálogo (`Product`, `StockCheck`) en `backend/schemas`.
- Agente Norma con interfaz de LLM neutral (proveedor a definir) e instrucciones iniciales.
- Esqueletos de tools (catálogo, stock, borradores), servicios, Google Sheets y base interna.
- Configuración por variables de entorno (`.env.example`) y tests iniciales.
- Dependencias de desarrollo separadas en `requirements-dev.txt`, con cotas de versión mayor.
- Datos de ejemplo inventados en `datos_ejemplo/`: catálogo (formato de la Sheet 1), pedidos (formato de la Sheet 2) y mensajes de clientes con el resultado esperado del agente, validados por `tests/test_datos_ejemplo.py`.
- `Product.min_stock` y `Product.is_low_stock` para marcar productos con stock bajo en el dashboard.

### Cambiado

- La base SQLite pasa a `data/norma.db` (antes `norma.db` en la raíz y, con Docker, el volumen `norma-data`): para conservar datos locales, moverlos a `data/`.
- Python 3.12 como versión mínima.
- `backend/Schema` pasa a `backend/schemas` y `test` a `tests`, según la arquitectura.
- Roadmap actualizado a un equipo de 3 integrantes.

### Seguridad

- El webhook de WhatsApp valida la firma `X-Hub-Signature-256` de Meta (nueva variable `WHATSAPP_APP_SECRET`) y compara el token de verificación en tiempo constante.

[Sin publicar]: https://github.com/FernandoMRestelli/norma-agentic-ai/compare/main...develop
