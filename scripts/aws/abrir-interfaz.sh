#!/usr/bin/env bash
# [PC local]  --  abrir-interfaz.sh <sg-plataforma> <ip-publica> <etiqueta>
# Abre el 443 (interfaz y API) a una IP del equipo. Solo a IP concretas:
# RNF-SEG-07 deja el punto de entrada como unico componente abierto a internet.
# Para saber tu IP publica: curl -s https://checkip.amazonaws.com
set -euo pipefail
SG="${1:?falta el grupo de la plataforma}"
IP="${2:?falta la IP publica}"
ETIQUETA="${3:?falta una etiqueta, p. ej. juanjo-casa}"

aws ec2 authorize-security-group-ingress --group-id "$SG" \
  --ip-permissions "IpProtocol=tcp,FromPort=443,ToPort=443,\
IpRanges=[{CidrIp=${IP}/32,Description=${ETIQUETA}}]"

echo "Interfaz abierta para $ETIQUETA ($IP): https://<ip-elastica>/"
