FROM python:3.12-slim

# ip y tc (iproute2): el inyector degrada el enlace hacia catalogo con tc
# netem dentro de este contenedor (H-027). Hace falta cap_add NET_ADMIN.
RUN apt-get update \
 && apt-get install -y --no-install-recommends iproute2 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && opentelemetry-bootstrap -a install

COPY app.py .

# sin destino, el SDK no exporta nada y no llena el log de errores
ENV OTEL_TRACES_EXPORTER=none \
    OTEL_METRICS_EXPORTER=none \
    OTEL_LOGS_EXPORTER=none

EXPOSE 8000
CMD ["opentelemetry-instrument", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
