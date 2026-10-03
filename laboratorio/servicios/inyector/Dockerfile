FROM python:3.12-slim

# stress-ng provoca los fallos de CPU y memoria. Lo demas es Python puro.
RUN apt-get update \
 && apt-get install -y --no-install-recommends stress-ng \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY inyector/ ./inyector/

# Corre como root: necesita el socket de Docker del nodo (caida de servicio) y
# escribir en el volumen de relleno. No tiene red (network_mode: none en el
# compose), asi que no puede alcanzar la plataforma ni ningun otro equipo.
CMD ["python", "-u", "-m", "inyector", "campana"]
