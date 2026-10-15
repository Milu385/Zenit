#!/usr/bin/env bash
# [TU PC]  --  scripts/azure/01-red.sh
#
# Grupo de recursos, dos redes que no se conocen (plataforma y nodo) y sus
# grupos de seguridad. Los dos NSG nacen con el 22 abierto solo a tu IP; el
# 4317 y el 443 se abren despues, una IP a la vez, con admitir-nodo.sh y
# abrir-equipo.sh. Todo lo demas queda cerrado (regla por defecto de Azure).
set -euo pipefail
source "$(dirname "$0")/comun.sh"
MI_IP="$(mi_ip)"

az group create -n "$GRUPO" -l "$REGION" --tags proyecto=zenit -o none
echo "Grupo $GRUPO en $REGION"

for par in "$RED_PLATAFORMA $PREFIJO_PLATAFORMA $SUBRED_PLATAFORMA $NSG_PLATAFORMA" \
           "$RED_NODO $PREFIJO_NODO $SUBRED_NODO $NSG_NODO"; do
  read -r red prefijo subred nsg <<<"$par"
  # si ya existen no se recrean: un create sobre un NSG existente borraria sus reglas
  az network nsg show -g "$GRUPO" -n "$nsg" -o none 2>/dev/null \
    || az network nsg create -g "$GRUPO" -n "$nsg" -l "$REGION" -o none
  az network vnet show -g "$GRUPO" -n "$red" -o none 2>/dev/null \
    || az network vnet create -g "$GRUPO" -n "$red" -l "$REGION" --address-prefixes "$prefijo" \
         --subnet-name principal --subnet-prefixes "$subred" --network-security-group "$nsg" -o none
  agregar_origen "$nsg" equipo-ssh 22 "$PRIORIDAD_SSH" "$MI_IP"
  echo "Red $red ($prefijo) con $nsg: 22 abierto a $MI_IP"
done

echo "Siguiente: bash scripts/azure/02-plataforma.sh"
