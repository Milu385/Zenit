#!/usr/bin/env bash
# [PC local]  --  scripts/aws/01-redes.sh
set -euo pipefail

MI_IP="$(curl -s https://checkip.amazonaws.com)/32"

# ---------------------------------------------------------------- PLATAFORMA
# Vive en la VPC por defecto. Es el unico recurso con entrada desde internet.
VPC_PLAT=$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true \
  --query 'Vpcs[0].VpcId' --output text)

SG_PLAT=$(aws ec2 create-security-group \
  --group-name zenit-plataforma --description "Zenit: plataforma" \
  --vpc-id "$VPC_PLAT" --query GroupId --output text)

# Administracion (salud, metricas): solo tu equipo
aws ec2 authorize-security-group-ingress --group-id "$SG_PLAT" \
  --ip-permissions "IpProtocol=tcp,FromPort=8080,ToPort=8080,IpRanges=[{CidrIp=$MI_IP,Description=equipo}]"

# ---------------------------------------------------- RED DEL NODO OBSERVADO
# VPC propia. 10.60.0.0/16 no solapa con la 172.31.0.0/16 por defecto,
# por si algun dia quieres emparejarlas para medir el transporte.
VPC_NODO=$(aws ec2 create-vpc --cidr-block 10.60.0.0/16 \
  --tag-specifications 'ResourceType=vpc,Tags=[{Key=Name,Value=zenit-vpc-nodo-aws}]' \
  --query Vpc.VpcId --output text)
aws ec2 modify-vpc-attribute --vpc-id "$VPC_NODO" --enable-dns-hostnames

SUBRED=$(aws ec2 create-subnet --vpc-id "$VPC_NODO" --cidr-block 10.60.1.0/24 \
  --query Subnet.SubnetId --output text)
aws ec2 modify-subnet-attribute --subnet-id "$SUBRED" --map-public-ip-on-launch

# Salida a internet: la necesita para SSM, para descargar imagenes y para emitir
IGW=$(aws ec2 create-internet-gateway --query InternetGateway.InternetGatewayId --output text)
aws ec2 attach-internet-gateway --vpc-id "$VPC_NODO" --internet-gateway-id "$IGW"
TABLA=$(aws ec2 create-route-table --vpc-id "$VPC_NODO" --query RouteTable.RouteTableId --output text)
aws ec2 create-route --route-table-id "$TABLA" \
  --destination-cidr-block 0.0.0.0/0 --gateway-id "$IGW" >/dev/null
aws ec2 associate-route-table --route-table-id "$TABLA" --subnet-id "$SUBRED" >/dev/null

# Grupo del nodo: SIN reglas de entrada. Solo sale.
SG_NODO=$(aws ec2 create-security-group \
  --group-name zenit-nodo --description "Zenit: nodo observado" \
  --vpc-id "$VPC_NODO" --query GroupId --output text)

cat <<EOF

Guarda esto en scripts/aws/valores.env (no lo subas al repositorio):
  SG_PLATAFORMA=$SG_PLAT
  VPC_NODO=$VPC_NODO
  SUBRED_NODO=$SUBRED
  SG_NODO=$SG_NODO
EOF
