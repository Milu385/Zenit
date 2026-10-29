#!/usr/bin/env bash
# Cifras de H-029, H-034 y de la medicion del piso con una exportacion de Azure.
#
#   bash scripts/deteccion/evaluar_azure.sh <carpeta de la exportacion> <despliegue de mad-v2> <corte> [salida]
#   bash scripts/deteccion/evaluar_azure.sh exportaciones/zenit-export-2026-10-30 2026-10-26T14:01Z 2026-10-30T12:00Z
#
# La carpeta es la exportacion descomprimida: tiene que contener (en
# cualquier subcarpeta) inyecciones.csv, anomalias.csv,
# abiertas-mad-v1-2026-10-26.csv, manifiesto.json y los Parquet de metricas.
# Todo corre fuera de la plataforma y solo escribe en la carpeta de salida
# (por defecto exportaciones/informes-<corte>/, que git ignora).
#
# Las fechas del informe (docs/evaluacion/azure.md, "Lo que se declara"):
#   inicio de la campana   2026-10-16
#   mad-v1                 2026-10-21T13:13Z
#   reposicion catalogo    2026-10-21T15:17Z
#   mad-v2                 la hora que anoto Juan Jose con `date -u` el 26
#
# Necesita las dependencias de scripts/deteccion/requirements.txt.
set -euo pipefail
RAIZ="$(cd "$(dirname "$0")/../.." && pwd)"
EXPORT="$(cd "${1:?uso: evaluar_azure.sh <exportacion> <despliegue mad-v2> <corte> [salida]}" && pwd)"
DESPLIEGUE="${2:?falta la hora del despliegue de mad-v2, UTC (2026-10-26T14:01Z)}"
CORTE="${3:?falta la fecha de corte, UTC}"
SALIDA="${4:-$RAIZ/exportaciones/informes-$(echo "$CORTE" | tr -d ':-' | cut -c1-13)}"
INICIO_MAD_V1="2026-10-21T13:13Z"
PY="${PYTHON:-python3}"

buscar() { find "$EXPORT" -name "$1" -print | awk '{ print length, $0 }' | sort -n | head -1 | cut -d' ' -f2-; }
INY="$(buscar inyecciones.csv)"
ANO="$(buscar anomalias.csv)"
MANIFIESTO="$(buscar manifiesto.json)"
METRICAS="$(dirname "$MANIFIESTO")"         # la carpeta que tiene metricas/<tabla>.parquet
for f in "$INY" "$ANO" "$MANIFIESTO"; do [ -n "$f" ] || { echo "falta un archivo en $EXPORT" >&2; exit 1; }; done
mkdir -p "$SALIDA"
echo "exportacion: $EXPORT"; echo "salida:      $SALIDA"; echo

cd "$RAIZ/backend"
echo "== H-029, mad-v1 ($INICIO_MAD_V1 a $DESPLIEGUE)"
"$PY" -m zenit.deteccion.evaluacion --inyecciones "$INY" --anomalias "$ANO" \
  --desde "$INICIO_MAD_V1" --hasta "$DESPLIEGUE" --version mad-v1 --nombre "mad-v1" \
  --corte "$CORTE" --salida "$SALIDA/h029-mad-v1/" > /dev/null
echo "== H-029, mad-v2 ($DESPLIEGUE a $CORTE)"
"$PY" -m zenit.deteccion.evaluacion --inyecciones "$INY" --anomalias "$ANO" \
  --desde "$DESPLIEGUE" --hasta "$CORTE" --version mad-v2 --nombre "mad-v2" \
  --corte "$CORTE" --salida "$SALIDA/h029-mad-v2/" > /dev/null

cd "$RAIZ"
echo "== H-029, series bloqueadas por mad-v1"
"$PY" scripts/deteccion/series_bloqueadas.py "$EXPORT" --desde "$INICIO_MAD_V1" --hasta "$DESPLIEGUE" \
  --salida "$SALIDA/h029-series-bloqueadas/" > /dev/null

echo "== H-034, KMeans entrenado antes de mad-v2 y detectando en su ventana"
"$PY" scripts/deteccion/no_supervisada.py --exportacion "$METRICAS" \
  --entrenar 2026-10-16T00:00Z "$DESPLIEGUE" --detectar "$DESPLIEGUE" "$CORTE" \
  --salida "$SALIDA/h034-anomalias-kmeans.csv" > "$SALIDA/h034-entrenamiento.txt"
(cd backend && "$PY" -m zenit.deteccion.evaluacion --inyecciones "$INY" \
  --anomalias "$SALIDA/h034-anomalias-kmeans.csv" --desde "$DESPLIEGUE" --hasta "$CORTE" \
  --version kmeans --nombre "KMeans (H-034)" --corte "$CORTE" --salida "$SALIDA/h034/" > /dev/null)

echo "== Piso del swap y del disco (mad-v3 propuesto)"
"$PY" scripts/deteccion/medir_piso.py "$EXPORT" --desde "$DESPLIEGUE" --hasta "$CORTE" \
  --anomalias "$ANO" --salida "$SALIDA/piso-mad-v2/" > /dev/null
"$PY" scripts/deteccion/medir_piso.py "$EXPORT" --sin-bloqueo --salida "$SALIDA/piso-campana/" > /dev/null

echo; echo "listo. Informes:"; find "$SALIDA" -name informe.md | sort
echo "H-035: abrir Modelos/Modelos SARIMA/modelos_sarima.ipynb con ZENIT_EXPORTACION=$EXPORT"
