#!/usr/bin/env bash
# [PLATAFORMA]  --  scripts/exportar.sh <desde> <hasta>
#   bash scripts/exportar.sh 2026-10-06T00:00Z 2026-10-07T00:00Z
#   bash scripts/exportar.sh ayer          (el dia UTC anterior completo; para el cron)
#
# Deja en exportaciones/ una carpeta y un .tar.gz con un Parquet por metrica,
# inyecciones.csv, huecos.csv y manifiesto.json (plan de la epica 2, seccion 7).
set -euo pipefail
RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
if [ "${1:-}" = "ayer" ]; then
  DESDE="$(date -u -d 'yesterday' +%Y-%m-%dT00:00Z)"
  HASTA="$(date -u +%Y-%m-%dT00:00Z)"
else
  DESDE="${1:?uso: exportar.sh <desde> <hasta> | ayer}"
  HASTA="${2:?uso: exportar.sh <desde> <hasta> | ayer}"
fi
NOMBRE="zenit-$(echo "$DESDE" | tr -d ':-' | cut -c1-13)-a-$(echo "$HASTA" | tr -d ':-' | cut -c1-13)"
mkdir -p "$RAIZ/exportaciones"
cd "$RAIZ/deploy"
docker compose --env-file .env run --rm --no-deps -T \
  -u "$(id -u):$(id -g)" -v "$RAIZ/exportaciones:/exportaciones" \
  api python -m zenit.admin exportar --desde "$DESDE" --hasta "$HASTA" --salida "/exportaciones/$NOMBRE"
tar -czf "$RAIZ/exportaciones/$NOMBRE.tar.gz" -C "$RAIZ/exportaciones" "$NOMBRE"
echo "listo: exportaciones/$NOMBRE.tar.gz"
