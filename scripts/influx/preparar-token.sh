#!/usr/bin/env bash
# [nodo de plataforma]  --  scripts/influx/preparar-token.sh
#
# Genera el token de administracion de InfluxDB una sola vez y lo deja en dos
# sitios:
#   - deploy/.env (INFLUX_TOKEN), que leen la ingesta y la API;
#   - deploy/secretos/influx-admin.json, que InfluxDB lee al arrancar
#     (--admin-token-file) para registrar ese mismo token.
#
# Si ya existe, no lo cambia: InfluxDB registra el token la primera vez que
# arranca con un volumen vacio, y uno nuevo despues no seria valido.
set -euo pipefail
cd "$(dirname "$0")/../../deploy"

[ -f .env ] || { echo "falta deploy/.env: cp .env.example .env y completa ORG_GITHUB y TOKEN_INGESTA"; exit 1; }

ACTUAL=$(grep -E '^INFLUX_TOKEN=' .env | head -1 | cut -d= -f2- || true)
if [ -n "$ACTUAL" ]; then
  TOKEN="$ACTUAL"
  echo "INFLUX_TOKEN ya existe en deploy/.env: se conserva"
else
  TOKEN="apiv3_$(openssl rand -hex 32)"
  if grep -qE '^INFLUX_TOKEN=' .env; then
    sed -i "s|^INFLUX_TOKEN=.*|INFLUX_TOKEN=${TOKEN}|" .env
  else
    printf '\nINFLUX_TOKEN=%s\n' "$TOKEN" >> .env
  fi
  echo "INFLUX_TOKEN generado y escrito en deploy/.env"
fi

mkdir -p secretos
chmod 700 secretos                       # nadie mas en el anfitrion entra
printf '{"token": "%s", "name": "zenit-admin"}\n' "$TOKEN" > secretos/influx-admin.json
chmod 644 secretos/influx-admin.json     # el usuario influxdb3 del contenedor debe leerlo
echo "listo: deploy/secretos/influx-admin.json"
