#!/usr/bin/env bash
# [TU PC]  --  scripts/do/admitir-nodo.sh <ip-publica-del-nodo> <etiqueta>
# Abre el 4317 de la plataforma a un nodo de cualquier proveedor (el Proxmox incluido).
set -euo pipefail
source "$(dirname "$0")/comun.sh"
IP="${1:?uso: admitir-nodo.sh <ip> <etiqueta>}"; es_ip "$IP"
ETIQUETA="${2:?falta una etiqueta}"
FW="$(id_cortafuegos "$FW_PLATAFORMA")"; [ -n "$FW" ] || { echo "no existe el cortafuegos $FW_PLATAFORMA" >&2; exit 1; }
doctl compute firewall add-rules "$FW" --inbound-rules "protocol:tcp,ports:4317,address:$IP/32"
echo "$(date -u +%F) admitido $ETIQUETA ($IP) en el 4317 de DigitalOcean" >> "$(dirname "$0")/../../docs/nodos.md"
echo "Admitido $ETIQUETA ($IP). Quedo anotado en docs/nodos.md."
