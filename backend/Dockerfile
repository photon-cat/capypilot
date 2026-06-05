# Single image for both the `api` and `athena` services.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CEREAL_PATH=/app/cereal

WORKDIR /app

# System deps for pycapnp (capnp compiler) and building wheels.
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential capnproto libcapnp-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Backend application code.
COPY backend/app /app/app
COPY backend/alembic /app/alembic
COPY backend/alembic.ini /app/alembic.ini
COPY backend/scripts /app/scripts

# Cap'n Proto schemas (the official cereal schema) for the log parser. Vendored
# under backend/schema so the build is self-contained — no openpilot package and
# no opendbc submodule needed.
COPY backend/schema /app/cereal

EXPOSE 8000 8001

HEALTHCHECK --interval=15s --timeout=5s --retries=5 \
  CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
