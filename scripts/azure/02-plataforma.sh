#!/usr/bin/env bash
# [TU PC]  --  scripts/azure/02-plataforma.sh
#
# La VM de la plataforma con IP publica estatica (la del certificado y de
# DESTINO_OTLP no cambia aunque se apague la VM) y una identidad administrada
# con el rol Reader sobre el grupo: es la credencial de solo lectura del
# descubrimiento en Azure (H-009d), sin secreto que guardar.
set -euo pipefail
source "$(dirname "$0")/comun.sh"
[ -r "$LLAVE_SSH" ] || { echo "no encuentro tu llave publica $LLAVE_SSH (ssh-keygen -t ed25519, o export LLAVE_SSH=...)" >&2; exit 1; }
ALCANCE="$(az group show -n "$GRUPO" --query id -o tsv)"

az network public-ip create -g "$GRUPO" -n zenit-plataforma-ip -l "$REGION" \
  --sku Standard --allocation-method Static --version IPv4 -o none
IP="$(az network public-ip show -g "$GRUPO" -n zenit-plataforma-ip --query ipAddress -o tsv)"

az vm create -g "$GRUPO" -n zenit-plataforma -l "$REGION" \
  --image "$IMAGEN" --size "$TAMANO_PLATAFORMA" \
  --admin-username "$USUARIO" --ssh-key-values "$LLAVE_SSH" \
  --vnet-name "$RED_PLATAFORMA" --subnet principal --nsg "" \
  --public-ip-address zenit-plataforma-ip \
  --os-disk-size-gb "$DISCO_PLATAFORMA_GB" --storage-sku StandardSSD_LRS \
  --custom-data "$(dirname "$0")/../do/userdata-docker.sh" \
  --assign-identity "[system]" --role Reader --scope "$ALCANCE" \
  --tags zenit=plataforma -o none
# --nsg "": la NIC no lleva NSG propio; manda el de la subred (zenit-plataforma-nsg)

SUSCRIPCION="$(az account show --query id -o tsv)"
cat <<FIN

Plataforma lista en $IP (IP estatica).
  1. Certificado del servidor para esta IP, desde la raiz del repositorio donde esta tls/ca.key:
       bash scripts/tls/emitir.sh $IP
  2. Abre la interfaz a tu IP:   bash scripts/azure/abrir-equipo.sh $(mi_ip) <tu-nombre>
  3. Entra:                      ssh $USUARIO@$IP      (espera 3 o 4 minutos a que termine el userdata)
  4. En deploy/.env de la plataforma:
       ZENIT_PROVEEDOR=azure
       ZENIT_REGION=$REGION
       DESCUBRIR_AZURE=1
       AZURE_SUSCRIPCION=$SUSCRIPCION
       AZURE_GRUPO=$GRUPO
FIN
