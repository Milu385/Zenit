#!/usr/bin/env bash
# [TU PC]  --  scripts/do/02-nodo.sh <llave-ssh> [nombre]
#
# Crea un nodo observado en DigitalOcean y lo admite en la plataforma.
# El cortafuegos del nodo no tiene entradas salvo el 22 desde tu IP.
set -euo pipefail
source "$(dirname "$0")/comun.sh"
LLAVE="${1:?uso: 02-nodo.sh <llave-ssh> [nombre]}"
NOMBRE="${2:-zenit-nodo-do}"
MI_IP="$(curl -s https://checkip.amazonaws.com)"; es_ip "$MI_IP"

read -r ID IP < <(doctl compute droplet create "$NOMBRE" \
  --region "$REGION" --size "$TAMANO_NODO" --image "$IMAGEN" \
  --ssh-keys "$LLAVE" --user-data-file "$(dirname "$0")/userdata-docker.sh" \
  --tag-names zenit,zenit-nodo --wait --format ID,PublicIPv4 --no-header)
echo "Nodo $ID ($IP)"

if [ -z "$(id_cortafuegos "$FW_NODO")" ]; then
  doctl compute firewall create --name "$FW_NODO" --droplet-ids "$ID" \
    --inbound-rules "protocol:tcp,ports:22,address:$MI_IP/32" \
    --outbound-rules "$SALIDA_TODO" >/dev/null
else
  doctl compute firewall add-droplets "$(id_cortafuegos "$FW_NODO")" --droplet-ids "$ID"
fi

# Un droplet conserva su IP publica mientras exista, y es la IP con la que
# sale hacia la plataforma: esa es la que se admite en el 4317.
bash "$(dirname "$0")/admitir-nodo.sh" "$IP" "$NOMBRE"
echo "Entra con: ssh root@$IP   (espera 2 o 3 minutos a que termine el userdata)"
