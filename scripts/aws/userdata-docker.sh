#!/bin/bash
# [TU PC]  --  scripts/aws/userdata-docker.sh  (lo ejecuta la instancia al nacer)
set -euxo pipefail

apt-get update -y
apt-get install -y ca-certificates curl gnupg git

# repositorio oficial de Docker (el docker.io de Ubuntu suele ir atrasado)
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  >/etc/apt/sources.list.d/docker.list

apt-get update -y
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

systemctl enable --now docker
usermod -aG docker ubuntu

# marcador para saber que el userdata termino
touch /var/lib/zenit-listo
