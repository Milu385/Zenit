#!/bin/bash
# [NO SE EJECUTA A MANO]  --  scripts/do/userdata-docker.sh
# Lo corre el droplet al nacer. Tambien sirve, con sudo, para preparar la
# maquina virtual del Proxmox.
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive

apt-get update -y
apt-get install -y ca-certificates curl gnupg git

install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  >/etc/apt/sources.list.d/docker.list
apt-get update -y
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
# Registros de los contenedores con tope: la campana corre semanas y, sin
# rotacion, los registros crecen hasta llenar el disco.
# Se agrega a la configuracion existente, sin borrar lo que ya tenga.
mkdir -p /etc/docker
python3 - <<'PY'
import json, pathlib
p = pathlib.Path("/etc/docker/daemon.json")
d = json.loads(p.read_text()) if p.exists() and p.read_text().strip() else {}
d.setdefault("log-driver", "json-file")
d.setdefault("log-opts", {}).update({"max-size": "10m", "max-file": "3"})
p.write_text(json.dumps(d, indent=2))
PY
systemctl enable --now docker
systemctl restart docker

# Swap de 2 GB: el primer build compila varias imagenes y con 2 o 4 GB de RAM
# el OOM puede matar el build o a InfluxDB sin mensaje.
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# Reloj sincronizado: el tiempo hasta la deteccion compara relojes de maquinas distintas
timedatectl set-ntp true || true

touch /var/lib/zenit-listo
