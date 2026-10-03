# [TU PC]  --  scripts/do/comun.sh   (lo cargan los demas scripts; no se ejecuta solo)
# Valores por defecto de DigitalOcean. Se pueden cambiar con variables de entorno.
REGION="${REGION:-nyc3}"
IMAGEN="${IMAGEN:-ubuntu-24-04-x64}"
TAMANO_PLATAFORMA="${TAMANO_PLATAFORMA:-s-2vcpu-4gb}"
TAMANO_NODO="${TAMANO_NODO:-s-1vcpu-2gb}"
FW_PLATAFORMA="zenit-plataforma"
FW_NODO="zenit-nodo"
SALIDA_TODO="protocol:tcp,ports:all,address:0.0.0.0/0,address:::/0 protocol:udp,ports:all,address:0.0.0.0/0,address:::/0 protocol:icmp,address:0.0.0.0/0,address:::/0"

command -v doctl >/dev/null || { echo "falta doctl: https://docs.digitalocean.com/reference/doctl/how-to/install/" >&2; exit 1; }

id_cortafuegos() {
  doctl compute firewall list --format ID,Name --no-header | awk -v n="$1" '$2 == n {print $1}'
}

es_ip() {
  [[ "$1" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || { echo "no es una IPv4: $1" >&2; exit 1; }
}
