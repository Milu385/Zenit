#!/usr/bin/env bash
# Instantanea de configuracion de un nodo (reporte de configuraciones, RF-GOB-03).
#
# Toma, en el nodo donde corre: puertos que escuchan, contenedores con su
# imagen, version y usuario, version del agente e intervalo de muestreo. Imprime
# un JSON con la forma que recibe POST /api/gobernanza/configuraciones/instantaneas.
#
# Se corre en el nodo con sudo (sin root, ss no dice que proceso tiene cada
# puerto). Como el 443 de la plataforma solo admite las IP del equipo, lo
# normal es tomarla por SSH y enviarla desde el equipo:
#
#   ssh nodo 'sudo bash -s -- ~/zenit/laboratorio/entornos/digitalocean.env' \
#     < scripts/instantanea-nodo.sh \
#     | ZENIT_API=https://<ip-plataforma> TOKEN_INSTANTANEAS=... scripts/instantanea-nodo.sh --enviar
#
# Uso:
#   instantanea-nodo.sh [archivo.env]       imprime la instantanea
#   instantanea-nodo.sh --enviar            lee una instantanea de stdin y la envia
#
# El archivo .env es el del entorno del nodo (laboratorio/entornos/*.env o
# deploy/.env en la plataforma): de ahi salen ZENIT_NODO, ZENIT_PROVEEDOR,
# ZENIT_ENTORNO e INTERVALO_METRICAS.
set -euo pipefail

if [[ "${1:-}" == "--enviar" ]]; then
  : "${ZENIT_API:?falta ZENIT_API, p. ej. https://<ip-plataforma>}"
  : "${TOKEN_INSTANTANEAS:?falta TOKEN_INSTANTANEAS}"
  CA="${RUTA_CA:-$(dirname "$0")/../tls/ca.crt}"
  # el token va por un archivo de cabeceras y no en la linea de comandos,
  # que cualquiera en el equipo puede ver con ps
  cabeceras=$(mktemp)
  trap 'rm -f "$cabeceras"' EXIT
  printf 'Authorization: Bearer %s\nContent-Type: application/json\n' "$TOKEN_INSTANTANEAS" > "$cabeceras"
  curl -fsS --cacert "$CA" -H @"$cabeceras" --data-binary @- "$ZENIT_API/api/gobernanza/configuraciones/instantaneas"
  echo
  exit 0
fi

if [[ -n "${1:-}" ]]; then
  [[ -r "$1" ]] || { echo "no puedo leer $1" >&2; exit 1; }
  set -a
  # shellcheck disable=SC1090
  source "$1"
  set +a
fi
: "${ZENIT_NODO:?falta ZENIT_NODO (pasa el .env del entorno)}"

# La comparacion es entre entornos: los cuatro nodos del laboratorio comparten
# ZENIT_ENTORNO=lab y se distinguen por proveedor. La plataforma es su propio entorno.
if [[ "${ZENIT_ENTORNO:-}" == "plataforma" ]]; then
  entorno=plataforma
else
  entorno="${ZENIT_PROVEEDOR:-${ZENIT_ENTORNO:-desconocido}}"
fi

command -v ss >/dev/null || { echo "falta ss (iproute2)" >&2; exit 1; }
puertos=$(ss -H -lntu 2>/dev/null; echo "---procesos---"; ss -H -lntup 2>/dev/null || true)

contenedores=""
if command -v docker >/dev/null && docker info >/dev/null 2>&1; then
  ids=$(docker ps -q)
  if [[ -n "$ids" ]]; then
    # nombre del servicio de compose (o del contenedor) | imagen | usuario | puertos publicados
    contenedores=$(docker inspect --format \
      '{{with index .Config.Labels "com.docker.compose.service"}}{{.}}{{else}}{{.Name}}{{end}}|{{.Config.Image}}|{{.Config.User}}|{{range $p, $b := .NetworkSettings.Ports}}{{range $b}}{{.HostIp}}:{{.HostPort}}/{{$p}} {{end}}{{end}}' \
      $ids)
  fi
fi

ZENIT_NODO="$ZENIT_NODO" ENTORNO="$entorno" INTERVALO="${INTERVALO_METRICAS:-10s}" \
PUERTOS="$puertos" CONTENEDORES="$contenedores" python3 - <<'PY'
import json, os, re
from datetime import datetime, timezone

def segundos(texto):
    m = re.fullmatch(r"(\d+)\s*(ms|s|m|h)?", texto.strip())
    if not m:
        return 10
    n, u = int(m.group(1)), m.group(2) or "s"
    return max(1, {"ms": n // 1000, "s": n, "m": n * 60, "h": n * 3600}[u])

def separar(direccion):
    # 0.0.0.0:22, [::]:22, *:22, 127.0.0.53%lo:53
    d, _, p = direccion.rpartition(":")
    d = d.strip("[]").split("%")[0]
    return ("0.0.0.0" if d in ("*", "") else d), int(p)

crudo, _, con_procesos = os.environ["PUERTOS"].partition("---procesos---")
procesos = {}
for linea in con_procesos.splitlines():
    c = linea.split()
    if len(c) >= 6:
        m = re.search(r'users:\(\("([^"]+)"', linea)
        if m:
            procesos[(c[0], c[4])] = m.group(1)
puertos = {}
for linea in crudo.splitlines():
    c = linea.split()
    if len(c) < 5:
        continue
    try:
        direccion, puerto = separar(c[4])
    except ValueError:
        continue
    puertos[(puerto, c[0], direccion)] = {"puerto": puerto, "protocolo": c[0], "direccion": direccion,
                                          "proceso": procesos.get((c[0], c[4]), "")}

contenedores, version_agente = [], ""
for linea in os.environ["CONTENEDORES"].splitlines():
    if not linea.strip():
        continue
    nombre, imagen, usuario, publicados = (linea.split("|") + ["", "", ""])[:4]
    nombre = nombre.lstrip("/")
    base, version = imagen, "latest"
    if ":" in imagen.rsplit("/", 1)[-1]:
        base, version = imagen.rsplit(":", 1)
    contenedores.append({"nombre": nombre, "imagen": base, "version": version, "usuario": usuario})
    if nombre == "agente" or "opentelemetry-collector" in base:
        version_agente = version_agente or version
    # puertos publicados por Docker: no siempre aparecen en ss
    for p in publicados.split():
        m = re.fullmatch(r"(.*):(\d+)/\d+/(tcp|udp)", p)
        if m:
            d = m.group(1) or "0.0.0.0"
            clave = (int(m.group(2)), m.group(3), d)
            puertos.setdefault(clave, {"puerto": clave[0], "protocolo": clave[1], "direccion": d,
                                       "proceso": f"docker:{nombre}"})

print(json.dumps({
    "nodo": os.environ["ZENIT_NODO"],
    "entorno": os.environ["ENTORNO"],
    "tomada_en": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "version_agente": version_agente,
    "intervalo_muestreo_s": segundos(os.environ["INTERVALO"]),
    "puertos": sorted(puertos.values(), key=lambda p: (p["puerto"], p["protocolo"], p["direccion"])),
    "contenedores": sorted(contenedores, key=lambda c: c["nombre"]),
}, indent=2))
PY
