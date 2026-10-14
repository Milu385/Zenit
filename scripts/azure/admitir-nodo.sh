#!/usr/bin/env bash
# [TU PC]  --  scripts/azure/admitir-nodo.sh <ip-publica-del-nodo> <etiqueta>
# Abre el 4317 de la plataforma a un nodo de cualquier proveedor (el Proxmox incluido).
set -euo pipefail
source "$(dirname "$0")/comun.sh"
IP="${1:?uso: admitir-nodo.sh <ip> <etiqueta>}"; es_ip "$IP"
ETIQUETA="${2:?falta una etiqueta}"
agregar_origen "$NSG_PLATAFORMA" nodos-otlp 4317 "$PRIORIDAD_OTLP" "$IP"
echo "$(date -u +%F) admitido $ETIQUETA ($IP) en el 4317 de Azure" >> "$(dirname "$0")/../../docs/nodos.md"
echo "Admitido $ETIQUETA ($IP). Quedo anotado en docs/nodos.md."
