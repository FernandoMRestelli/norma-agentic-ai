FROM python:3.12-slim

# PYTEST_ADDOPTS y RUFF_NO_CACHE: /app es de solo lectura para el usuario norma, así que no se guarda caché
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTEST_ADDOPTS="-p no:cacheprovider" \
    RUFF_NO_CACHE=true

WORKDIR /app

# Dependencias primero para aprovechar la caché de capas.
# INSTALL_DEV=true suma pytest y herramientas de desarrollo (docker-compose lo activa).
ARG INSTALL_DEV=false
COPY requirements.txt requirements-dev.txt ./
RUN if [ "$INSTALL_DEV" = "true" ]; then pip install -r requirements-dev.txt; else pip install -r requirements.txt; fi

# Todo el código va dentro de la imagen: la imagen de una release corre exactamente el código de ese tag
COPY backend/ backend/
COPY tests/ tests/
COPY pytest.ini .

# UID fijo para que en el servidor se pueda dar permiso de escritura sobre ./data (montado en /app/data)
RUN useradd --create-home --uid 1000 norma && mkdir -p data && chown norma data
USER norma

# El workflow de release pasa el número de versión; en desarrollo queda "dev"
ARG VERSION=dev
ENV APP_VERSION=$VERSION

EXPOSE 8000

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
