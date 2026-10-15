#!/usr/bin/env bash
# [TU PC]  --  bash scripts/tls/emitir-cliente.sh <nodo> [dias]   (desde la raiz del repositorio)
#
# Certificado de cliente para el agente de un nodo (H-012, RNF-SEG-07). Se
# corre donde esta tls/ca.key, que NUNCA sale de tu equipo.
#
#   <nodo>  el ZENIT_NODO del nodo (zenit-nodo-do). Queda como CN, y el borde
#           lo usa como identidad del emisor para el limite de tasa.
#   [dias]  vigencia; 400 por defecto, que cubre el semestre con holgura.
#
# Deja tls/clientes/<nodo>.crt y <nodo>.key. Al nodo van los dos, como
# laboratorio/agente/cliente/cliente.crt y cliente.key; ninguno se versiona.
set -euo pipefail
NODO="${1:?uso: emitir-cliente.sh <nodo> [dias]}"
DIAS="${2:-400}"
[[ "$NODO" =~ ^[a-z0-9][a-z0-9-]{1,62}$ ]] || { echo "nombre de nodo no valido: $NODO" >&2; exit 1; }
cd tls 2>/dev/null || { echo "no hay carpeta tls/ aqui: corre desde la raiz del repositorio donde esta la CA" >&2; exit 1; }
[ -f ca.key ] && [ -f ca.crt ] || { echo "faltan tls/ca.key o tls/ca.crt: la CA vigente esta en otro equipo" >&2; exit 1; }
mkdir -p clientes
umask 077

openssl req -new -newkey rsa:2048 -nodes -subj "/CN=${NODO}/O=Zenit" \
  -keyout "clientes/${NODO}.key" -out "clientes/${NODO}.csr" 2>/dev/null
cat > "clientes/${NODO}.ext" <<EXT
basicConstraints = CA:FALSE
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = clientAuth
EXT
openssl x509 -req -in "clientes/${NODO}.csr" -CA ca.crt -CAkey ca.key -CAcreateserial \
  -days "$DIAS" -sha256 -extfile "clientes/${NODO}.ext" -out "clientes/${NODO}.crt" 2>/dev/null
rm -f "clientes/${NODO}.csr" "clientes/${NODO}.ext"
chmod 644 "clientes/${NODO}.crt"

SERIE=$(openssl x509 -in "clientes/${NODO}.crt" -noout -serial | cut -d= -f2)
VENCE=$(openssl x509 -in "clientes/${NODO}.crt" -noout -enddate | cut -d= -f2)
openssl verify -CAfile ca.crt "clientes/${NODO}.crt" >/dev/null
echo "Listo: tls/clientes/${NODO}.crt y .key"
echo
echo "Agrega esta fila a docs/certificados.md:"
echo "| ${NODO} | ${SERIE} | $(date -u +%Y-%m-%d) | ${VENCE} | vigente |"
echo
echo "Copia al nodo (la clave por ssh, nunca por el repositorio):"
echo "  ssh <nodo> mkdir -p zenit/laboratorio/agente/cliente"
echo "  scp tls/clientes/${NODO}.crt <nodo>:zenit/laboratorio/agente/cliente/cliente.crt"
echo "  scp tls/clientes/${NODO}.key <nodo>:zenit/laboratorio/agente/cliente/cliente.key"
