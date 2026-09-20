# [TU PC]  --  scripts/tls/emitir.sh
#!/usr/bin/env bash
set -euo pipefail
IP_PUBLICA="${1:?uso: emitir.sh <ip-elastica-de-la-plataforma>}"
mkdir -p tls && cd tls

# --- autoridad (la clave NUNCA sale de tu equipo; el .crt si se versiona) ---
[ -f ca.key ] || openssl req -x509 -newkey rsa:4096 -nodes -days 3650 \
  -subj "/CN=Zenit CA" -keyout ca.key -out ca.crt

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
