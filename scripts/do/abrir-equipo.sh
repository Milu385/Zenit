#!/usr/bin/env bash
# [TU PC]  --  scripts/do/abrir-equipo.sh <ip> <nombre>
# Da a un integrante acceso al 443 (interfaz y API) y al 22 (SSH) de la
# plataforma, y al 22 de los nodos.
set -euo pipefail
source "$(dirname "$0")/comun.sh"
IP="${1:?uso: abrir-equipo.sh <ip> <nombre>}"; es_ip "$IP"
NOMBRE="${2:?falta el nombre}"
FW="$(id_cortafuegos "$FW_PLATAFORMA")"; [ -n "$FW" ] || { echo "no existe el cortafuegos $FW_PLATAFORMA" >&2; exit 1; }
doctl compute firewall add-rules "$FW" \
  --inbound-rules "protocol:tcp,ports:443,address:$IP/32 protocol:tcp,ports:22,address:$IP/32"
FWN="$(id_cortafuegos "$FW_NODO")"
[ -z "$FWN" ] || doctl compute firewall add-rules "$FWN" --inbound-rules "protocol:tcp,ports:22,address:$IP/32"
echo "Abierto a $NOMBRE ($IP): 443 y 22 de la plataforma${FWN:+, 22 de los nodos}."
