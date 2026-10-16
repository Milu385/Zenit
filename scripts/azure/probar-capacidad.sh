#!/usr/bin/env bash
# [TU PC]  --  scripts/azure/probar-capacidad.sh
#
# Busca una region con capacidad para las dos VMs sin crear nada. Las
# suscripciones de prueba gratuita reciben "SkuNotAvailable ... Capacity
# Restrictions" aunque el tamano exista y haya cuota, y eso solo se ve al
# desplegar. Aqui se valida un despliegue de prueba (red, NIC y VM) con
# "az deployment group validate": Azure hace la misma revision previa que en
# un despliegue real, pero no crea nada ni cobra.
#
# Requiere que exista el grupo (01-red.sh). Las VMs de prueba pueden estar en
# otra region distinta a la del grupo; solo se validan.
#
# Uso:  bash scripts/azure/probar-capacidad.sh [region ...]
set -euo pipefail
source "$(dirname "$0")/comun.sh"
[ -r "$LLAVE_SSH" ] || { echo "no encuentro tu llave publica $LLAVE_SSH" >&2; exit 1; }
az group show -n "$GRUPO" -o none 2>/dev/null || { echo "no existe el grupo $GRUPO: corre 01-red.sh primero" >&2; exit 1; }

REGIONES=("$@")
[ ${#REGIONES[@]} -gt 0 ] || REGIONES=(eastus2 centralus southcentralus westus2 westus3 eastus canadacentral brazilsouth)
# 2 vCPU y 8 GiB para la plataforma; 2 vCPU y 4 GiB para el nodo. En orden de precio.
PLATAFORMA=(Standard_B2as_v2 Standard_B2s_v2 Standard_B2ms Standard_D2as_v5 Standard_D2s_v5)
NODO=(Standard_B2als_v2 Standard_B2ls_v2 Standard_B2s)

PLANTILLA="$(mktemp)"; trap 'rm -f "$PLANTILLA"' EXIT
cat >"$PLANTILLA" <<'JSON'
{
  "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#",
  "contentVersion": "1.0.0.0",
  "parameters": {
    "region": { "type": "string" },
    "tamano": { "type": "string" },
    "llave": { "type": "string" }
  },
  "resources": [
    {
      "type": "Microsoft.Network/virtualNetworks", "apiVersion": "2023-09-01",
      "name": "zenit-prueba-red", "location": "[parameters('region')]",
      "properties": {
        "addressSpace": { "addressPrefixes": [ "10.99.0.0/16" ] },
        "subnets": [ { "name": "principal", "properties": { "addressPrefix": "10.99.1.0/24" } } ]
      }
    },
    {
      "type": "Microsoft.Network/networkInterfaces", "apiVersion": "2023-09-01",
      "name": "zenit-prueba-nic", "location": "[parameters('region')]",
      "dependsOn": [ "[resourceId('Microsoft.Network/virtualNetworks', 'zenit-prueba-red')]" ],
      "properties": {
        "ipConfigurations": [ { "name": "ip", "properties": {
          "privateIPAllocationMethod": "Dynamic",
          "subnet": { "id": "[resourceId('Microsoft.Network/virtualNetworks/subnets', 'zenit-prueba-red', 'principal')]" }
        } } ]
      }
    },
    {
      "type": "Microsoft.Compute/virtualMachines", "apiVersion": "2024-07-01",
      "name": "zenit-prueba", "location": "[parameters('region')]",
      "dependsOn": [ "[resourceId('Microsoft.Network/networkInterfaces', 'zenit-prueba-nic')]" ],
      "properties": {
        "hardwareProfile": { "vmSize": "[parameters('tamano')]" },
        "storageProfile": {
          "imageReference": { "publisher": "Canonical", "offer": "ubuntu-24_04-lts", "sku": "server", "version": "latest" },
          "osDisk": { "createOption": "FromImage", "managedDisk": { "storageAccountType": "StandardSSD_LRS" } }
        },
        "osProfile": {
          "computerName": "zenitprueba", "adminUsername": "zenit",
          "linuxConfiguration": { "disablePasswordAuthentication": true, "ssh": { "publicKeys": [
            { "path": "/home/zenit/.ssh/authorized_keys", "keyData": "[parameters('llave')]" } ] } }
        },
        "networkProfile": { "networkInterfaces": [
          { "id": "[resourceId('Microsoft.Network/networkInterfaces', 'zenit-prueba-nic')]" } ] }
      }
    }
  ]
}
JSON
LLAVE="$(cat "$LLAVE_SSH")"

# imprime "ok" o el codigo de error de Azure; nunca crea recursos
probar() {   # <region> <tamano>
  local salida
  if salida="$(az deployment group validate -g "$GRUPO" -n "zenit-prueba-$RANDOM" --template-file "$PLANTILLA" \
       --parameters region="$1" tamano="$2" llave="$LLAVE" -o none 2>&1)"; then
    echo ok
  else
    grep -oE 'SkuNotAvailable|Capacity Restrictions|QuotaExceeded|OperationNotAllowed|NotAvailableForSubscription|LocationNotAvailableForResourceType|RequestDisallowedByPolicy|InvalidTemplate[A-Za-z]*' \
      <<<"$salida" | sort -u | tr '\n' ' ' || true
    grep -qE 'SkuNotAvailable|Capacity|Quota|NotAvailable|NotAllowed|Disallowed|InvalidTemplate' <<<"$salida" || echo "otro error"
  fi
}

primero() {   # <region> <tamanos...>: el primer tamano con capacidad, o nada
  local region="$1" t r; shift
  for t in "$@"; do
    r="$(probar "$region" "$t")"
    printf '    %-18s %s\n' "$t" "$r" >&2
    [ "$r" = ok ] && { echo "$t"; return 0; }
  done
  return 0
}

for region in "${REGIONES[@]}"; do
  echo "== $region"
  echo "  plataforma:"
  tp="$(primero "$region" "${PLATAFORMA[@]}")"
  [ -n "$tp" ] || continue
  echo "  nodo:"
  tn="$(primero "$region" "${NODO[@]}")"
  [ -n "$tn" ] || continue
  echo; echo "Hay capacidad en $region: plataforma $tp, nodo $tn."
  if [ "$(az group show -n "$GRUPO" --query location -o tsv)" != "$region" ]; then
    echo "El grupo esta en otra region; se borra y se crea de nuevo (no hay VMs todavia):"
    echo; echo "  az group delete -n $GRUPO --yes"
  fi
  cat <<FIN
  export REGION=$region TAMANO_PLATAFORMA=$tp TAMANO_NODO=$tn
  bash scripts/azure/01-red.sh && bash scripts/azure/02-plataforma.sh

Usa la misma terminal (o repite el export) para 03-nodo.sh.
FIN
  exit 0
done
echo; echo "Ninguna region de la lista tuvo capacidad para los dos tamanos. Prueba otras:"
echo "  bash scripts/azure/probar-capacidad.sh northeurope westeurope uksouth"
exit 1
