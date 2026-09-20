#!/usr/bin/env bash
# [PC local]  --  admitir-nodo.sh <sg-plataforma> <ip-publica-del-nodo> <etiqueta>
# Ejemplo: admitir-nodo.sh sg-0abc 203.0.113.44 nodo-digitalocean
set -euo pipefail
SG="${1:?falta el grupo de la plataforma}"
IP="${2:?falta la IP publica del nodo}"
ETIQUETA="${3:?falta una etiqueta}"

aws ec2 authorize-security-group-ingress --group-id "$SG" \
  --ip-permissions "IpProtocol=tcp,FromPort=4317,ToPort=4317,\
IpRanges=[{CidrIp=${IP}/32,Description=${ETIQUETA}}]"

echo "Admitido $ETIQUETA ($IP). Anotalo en docs/nodos.md."
