#!/usr/bin/env bash
# [TU PC]  --  scripts/azure/03-nodo.sh [nombre]
#
# Un nodo observado en Azure (H-014), en su propia red, con IP publica
# estatica y la etiqueta zenit=nodo con la que lo encuentra el descubridor.
# El NSG del nodo no tiene entradas salvo el 22 desde tu IP: el nodo solo
# abre conexiones hacia la plataforma. Al final lo admite en el 4317.
set -euo pipefail
source "$(dirname "$0")/comun.sh"
NOMBRE="${1:-zenit-nodo-azure}"
[ -r "$LLAVE_SSH" ] || { echo "no encuentro tu llave publica $LLAVE_SSH" >&2; exit 1; }

az network public-ip create -g "$GRUPO" -n "$NOMBRE-ip" -l "$REGION" \
  --sku Standard --allocation-method Static --version IPv4 -o none
IP="$(az network public-ip show -g "$GRUPO" -n "$NOMBRE-ip" --query ipAddress -o tsv)"

az vm create -g "$GRUPO" -n "$NOMBRE" -l "$REGION" \
  --image "$IMAGEN" --size "$TAMANO_NODO" \
  --admin-username "$USUARIO" --ssh-key-values "$LLAVE_SSH" \
  --vnet-name "$RED_NODO" --subnet principal --nsg "" \
  --public-ip-address "$NOMBRE-ip" \
  --os-disk-size-gb "$DISCO_NODO_GB" --storage-sku StandardSSD_LRS \
  --custom-data "$(dirname "$0")/../do/userdata-docker.sh" \
  --tags zenit=nodo -o none
echo "Nodo $NOMBRE ($IP)"

# La IP publica estatica es la IP con la que el nodo sale hacia la plataforma
bash "$(dirname "$0")/admitir-nodo.sh" "$IP" "$NOMBRE"
echo "Entra con: ssh $USUARIO@$IP   (espera 3 o 4 minutos a que termine el userdata)"
