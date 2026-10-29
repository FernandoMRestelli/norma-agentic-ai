FROM python:3.12-slim

# PYTEST_ADDOPTS: /app es de solo lectura para el usuario norma, así que pytest no guarda caché
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTEST_ADDOPTS="-p no:cacheprovider"

WORKDIR /app

# Dependencias primero para aprovechar la caché de capas.
# INSTALL_DEV=true suma pytest y herramientas de desarrollo (docker-compose y CI lo activan).
ARG INSTALL_DEV=false
COPY requirements.txt requirements-dev.txt ./
RUN if [ "$INSTALL_DEV" = "true" ]; then pip install -r requirements-dev.txt; else pip install -r requirements.txt; fi

COPY backend/ backend/
COPY tests/ tests/
COPY datos_ejemplo/ datos_ejemplo/
COPY pytest.ini .

# Usuario sin privilegios; /app/data guarda la base SQLite
RUN useradd --create-home norma && mkdir -p data && chown norma data
USER norma

EXPOSE 8000

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
