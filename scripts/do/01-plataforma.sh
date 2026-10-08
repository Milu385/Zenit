#!/usr/bin/env bash
# [TU PC]  --  scripts/do/01-plataforma.sh <id-o-huella-de-tu-llave-ssh>
#
# Crea el droplet de la plataforma con IP reservada y su cortafuegos. El
# cortafuegos nace abierto solo al 22 desde tu IP; el 4317 y el 443 se abren
# despues, una IP a la vez, con admitir-nodo.sh y abrir-equipo.sh.
set -euo pipefail
source "$(dirname "$0")/comun.sh"
LLAVE="${1:?uso: 01-plataforma.sh <llave-ssh>   (doctl compute ssh-key list)}"
MI_IP="$(curl -s https://checkip.amazonaws.com)"; es_ip "$MI_IP"

read -r ID IP_PROPIA < <(doctl compute droplet create zenit-plataforma \
  --region "$REGION" --size "$TAMANO_PLATAFORMA" --image "$IMAGEN" \
  --ssh-keys "$LLAVE" --user-data-file "$(dirname "$0")/userdata-docker.sh" \
  --tag-names zenit,zenit-plataforma --wait --format ID,PublicIPv4 --no-header)
echo "Droplet $ID ($IP_PROPIA)"

# IP reservada: si el droplet se recrea, la IP del certificado y de DESTINO_OTLP no cambia
IP=$(doctl compute reserved-ip create --droplet-id "$ID" --format IP --no-header)

if [ -z "$(id_cortafuegos "$FW_PLATAFORMA")" ]; then
  doctl compute firewall create --name "$FW_PLATAFORMA" --droplet-ids "$ID" \
    --inbound-rules "protocol:tcp,ports:22,address:$MI_IP/32" \
    --outbound-rules "$SALIDA_TODO" >/dev/null
else
  doctl compute firewall add-droplets "$(id_cortafuegos "$FW_PLATAFORMA")" --droplet-ids "$ID"
fi

cat <<FIN

Plataforma lista en $IP (IP reservada).
  1. Certificado nuevo, desde la raiz del repositorio donde esta tls/ca.key:
       bash scripts/tls/emitir.sh $IP
  2. Abre la interfaz a tu IP:  bash scripts/do/abrir-equipo.sh $MI_IP juanjo
  3. Entra:                     ssh root@$IP     (espera 2 o 3 minutos a que termine el userdata)
FIN
