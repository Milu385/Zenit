#!/usr/bin/env bash
# [PLATAFORMA]  --  bash scripts/influx/espacio.sh
#
# Espacio en disco de cada base de InfluxDB, medido sobre los archivos (H-020).
# Respaldo de "zenit.admin espacio" por si system.parquet_files no responde.
# El almacen de objetos de Core guarda <nodo>/dbs/<base>-<id>/ dentro del
# directorio de datos.
set -euo pipefail
cd "$(dirname "$0")/../../deploy"
docker compose exec -T influxdb sh -c \
  'du -sh /var/lib/influxdb3/data/*/dbs/* 2>/dev/null || du -sh /var/lib/influxdb3/data/*'
