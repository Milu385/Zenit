#!/usr/bin/env bash
# [TU PC]  --  scripts/azure/retirar.sh <puerto> <ip>
# Quita una IP de un puerto de la plataforma, por ejemplo la IP vieja de un
# nodo cuyo proveedor le cambio la IP:  retirar.sh 4317 203.0.113.9
# Con el 22 la quita tambien de los nodos (lo que abrio abrir-equipo.sh).
set -euo pipefail
source "$(dirname "$0")/comun.sh"
PUERTO="${1:?uso: retirar.sh <puerto> <ip>}"; IP="${2:?falta la ip}"; es_ip "$IP"
case "$PUERTO" in
  4317) REGLA=nodos-otlp ;;
  443)  REGLA=equipo-interfaz ;;
  22)   REGLA=equipo-ssh ;;
  *) echo "puerto no administrado: $PUERTO (4317, 443 o 22)" >&2; exit 1 ;;
esac
quitar_origen "$NSG_PLATAFORMA" "$REGLA" "$IP"
[ "$PUERTO" = 22 ] && quitar_origen "$NSG_NODO" equipo-ssh "$IP"
echo "Listo: $IP fuera del $PUERTO."
