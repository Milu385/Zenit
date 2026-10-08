#!/usr/bin/env bash
# [TU PC]  --  scripts/do/retirar.sh <puerto> <ip>
# Quita una IP de un puerto del cortafuegos de la plataforma, por ejemplo la
# IP vieja de un nodo cuyo proveedor le cambio la IP:  retirar.sh 4317 203.0.113.9
set -euo pipefail
source "$(dirname "$0")/comun.sh"
PUERTO="${1:?uso: retirar.sh <puerto> <ip>}"; IP="${2:?falta la ip}"; es_ip "$IP"
doctl compute firewall remove-rules "$(id_cortafuegos "$FW_PLATAFORMA")" \
  --inbound-rules "protocol:tcp,ports:$PUERTO,address:$IP/32"
echo "Retirado $IP del $PUERTO."
