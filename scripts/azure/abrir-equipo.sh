#!/usr/bin/env bash
# [TU PC]  --  scripts/azure/abrir-equipo.sh <ip> <nombre>
# Da a un integrante acceso al 443 (interfaz y API) y al 22 (SSH) de la
# plataforma, y al 22 de los nodos.
set -euo pipefail
source "$(dirname "$0")/comun.sh"
IP="${1:?uso: abrir-equipo.sh <ip> <nombre>}"; es_ip "$IP"
NOMBRE="${2:?falta el nombre}"
agregar_origen "$NSG_PLATAFORMA" equipo-interfaz 443 "$PRIORIDAD_INTERFAZ" "$IP"
agregar_origen "$NSG_PLATAFORMA" equipo-ssh 22 "$PRIORIDAD_SSH" "$IP"
agregar_origen "$NSG_NODO" equipo-ssh 22 "$PRIORIDAD_SSH" "$IP"
echo "Abierto a $NOMBRE ($IP): 443 y 22 de la plataforma, 22 de los nodos."
