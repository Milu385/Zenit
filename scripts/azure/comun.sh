# [TU PC]  --  scripts/azure/comun.sh   (lo cargan los demas scripts; no se ejecuta solo)
# shellcheck shell=bash
# Valores por defecto de Azure. Se pueden cambiar con variables de entorno.
#
# Azure for Students restringe las regiones (politica "Allowed resource
# deployment regions") y los tamanos. 00-verificar.sh dice cuales sirven en
# tu suscripcion; si eastus no esta entre ellas: export REGION=<otra>.
GRUPO="${GRUPO:-zenit}"
REGION="${REGION:-eastus}"
IMAGEN="${IMAGEN:-Canonical:ubuntu-24_04-lts:server:latest}"
USUARIO="${USUARIO:-zenit}"
# 2 vCPU y 8 GiB: InfluxDB, ClickHouse, PostgreSQL, ingesta, API y trabajadores
TAMANO_PLATAFORMA="${TAMANO_PLATAFORMA:-Standard_B2as_v2}"
# 2 vCPU y 4 GiB: aplicacion de referencia, agente y laboratorio
TAMANO_NODO="${TAMANO_NODO:-Standard_B2als_v2}"
DISCO_PLATAFORMA_GB="${DISCO_PLATAFORMA_GB:-64}"
DISCO_NODO_GB="${DISCO_NODO_GB:-30}"
LLAVE_SSH="${LLAVE_SSH:-$HOME/.ssh/id_ed25519.pub}"
NSG_PLATAFORMA="zenit-plataforma-nsg"
NSG_NODO="zenit-nodo-nsg"
# Plataforma y nodo en redes distintas: la telemetria del nodo viaja por
# internet, como la de cualquier otro entorno (ADR-017).
RED_PLATAFORMA="zenit-plataforma-red"; PREFIJO_PLATAFORMA="10.70.0.0/16"; SUBRED_PLATAFORMA="10.70.1.0/24"
RED_NODO="zenit-nodo-red";             PREFIJO_NODO="10.71.0.0/16";       SUBRED_NODO="10.71.1.0/24"
# prioridades de las reglas de entrada (en Azure no se repiten dentro de un NSG)
PRIORIDAD_SSH=100
PRIORIDAD_OTLP=200
PRIORIDAD_INTERFAZ=210

command -v az >/dev/null || { echo "falta la CLI de Azure: https://learn.microsoft.com/cli/azure/install-azure-cli" >&2; exit 1; }
az account show >/dev/null 2>&1 || { echo "inicia sesion primero: az login" >&2; exit 1; }

es_ip() {
  [[ "$1" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || { echo "no es una IPv4: $1" >&2; exit 1; }
}

mi_ip() {
  local ip; ip="$(curl -s https://checkip.amazonaws.com)"; es_ip "$ip"; echo "$ip"
}

# Las IP admitidas en un puerto viven en UNA regla por puerto, con la lista de
# origenes. Agregar o quitar una IP reescribe la lista.
origenes() {   # <nsg> <regla>
  {
    az network nsg rule show -g "$GRUPO" --nsg-name "$1" -n "$2" --query "sourceAddressPrefix" -o tsv 2>/dev/null
    az network nsg rule show -g "$GRUPO" --nsg-name "$1" -n "$2" --query "sourceAddressPrefixes[]" -o tsv 2>/dev/null
  } | tr '\t' '\n' | { grep -E '^[0-9]' || true; } | sort -u
}

agregar_origen() {   # <nsg> <regla> <puerto> <prioridad> <ip>
  local nsg="$1" regla="$2" puerto="$3" prioridad="$4" ip="$5/32" lista
  lista="$( (origenes "$nsg" "$regla"; echo "$ip") | sort -u | tr '\n' ' ')"
  if az network nsg rule show -g "$GRUPO" --nsg-name "$nsg" -n "$regla" >/dev/null 2>&1; then
    # shellcheck disable=SC2086
    az network nsg rule update -g "$GRUPO" --nsg-name "$nsg" -n "$regla" --source-address-prefixes $lista -o none
  else
    # shellcheck disable=SC2086
    az network nsg rule create -g "$GRUPO" --nsg-name "$nsg" -n "$regla" --priority "$prioridad" \
      --direction Inbound --access Allow --protocol Tcp --destination-port-ranges "$puerto" \
      --source-address-prefixes $lista -o none
  fi
}

quitar_origen() {   # <nsg> <regla> <ip>
  local nsg="$1" regla="$2" ip="$3/32" lista
  if ! origenes "$nsg" "$regla" | grep -qxF "$ip"; then
    echo "  $3 no estaba en $regla de $nsg; nada que quitar"
    return 0
  fi
  lista="$(origenes "$nsg" "$regla" | { grep -vxF "$ip" || true; } | tr '\n' ' ')"
  if [ -z "${lista// }" ]; then
    az network nsg rule delete -g "$GRUPO" --nsg-name "$nsg" -n "$regla" -o none
  else
    # shellcheck disable=SC2086
    az network nsg rule update -g "$GRUPO" --nsg-name "$nsg" -n "$regla" --source-address-prefixes $lista -o none
  fi
}
