#!/usr/bin/env bash
# [TU PC]  --  scripts/tls/emitir.sh <ip-elastica>   (desde la raiz del repositorio)
set -euo pipefail
IP_PUBLICA="${1:?uso: emitir.sh <ip-elastica-de-la-plataforma>}"
mkdir -p tls && cd tls

# --- autoridad (la clave NUNCA sale de tu equipo; el .crt si se versiona) ---
# Sin ca.key no se crea una CA nueva en silencio: los nodos confian en la que
# esta versionada y dejarian de conectar con "unknown authority". Una CA nueva
# solo se crea a proposito, con NUEVA_CA=1, y obliga a redistribuir ca.crt.
if [ ! -f ca.key ]; then
  if [ "${NUEVA_CA:-0}" != "1" ]; then
    echo "No hay tls/ca.key en $(pwd)." >&2
    echo "Corre el script en el equipo y la carpeta donde esta la llave de la CA vigente." >&2
    echo "Para crear una CA NUEVA a proposito: NUEVA_CA=1 bash scripts/tls/emitir.sh <ip>" >&2
    exit 1
  fi
  openssl req -x509 -newkey rsa:4096 -nodes -days 3650 \
    -subj "/CN=Zenit CA" -keyout ca.key -out ca.crt
  echo "CA NUEVA creada. Copia ca.crt a laboratorio/agente/ca.crt y a todos los nodos." >&2
fi

# --- certificado del punto de entrada ---
openssl req -new -newkey rsa:4096 -nodes \
  -subj "/CN=zenit-ingesta" -keyout servidor.key -out servidor.csr

cat >san.cnf <<EOF
subjectAltName = IP:${IP_PUBLICA}, DNS:zenit-ingesta
extendedKeyUsage = serverAuth
EOF

openssl x509 -req -in servidor.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -days 825 -extfile san.cnf -out servidor.crt

rm -f servidor.csr san.cnf
echo "Listo. Copia ca.crt a laboratorio/agente/ca.crt y versionalo."
echo "servidor.key y ca.key NO van al repositorio."
