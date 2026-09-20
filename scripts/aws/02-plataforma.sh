#!/usr/bin/env bash
# [PC local]  --  scripts/aws/02-plataforma.sh
set -euo pipefail
SG_PLAT="${1:?uso: 02-plataforma.sh <sg-id>}"
ROL_INSTANCIA="${ROL_INSTANCIA:-LabInstanceProfile}"

AMI=$(aws ssm get-parameters \
  --names /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id \
  --query 'Parameters[0].Value' --output text)

ID=$(aws ec2 run-instances \
  --image-id "$AMI" \
  --instance-type t3.small \
  --security-group-ids "$SG_PLAT" \
  --iam-instance-profile "Name=$ROL_INSTANCIA" \
  --metadata-options "HttpTokens=required,HttpEndpoint=enabled" \
  --block-device-mappings '[{"DeviceName":"/dev/xvda","Ebs":{"VolumeSize":30,"VolumeType":"gp3","DeleteOnTermination":true}}]' \
  --user-data file://scripts/aws/userdata-docker.sh \
  --tag-specifications 'ResourceType=instance,Tags=[
      {Key=Name,Value=zenit-plataforma},
      {Key=zenit:rol,Value=plataforma},
      {Key=zenit:entorno,Value=aws}]' \
  --query 'Instances[0].InstanceId' --output text)

echo "Instancia: $ID"

# IP elastica: para que DESTINO_OTLP no cambie al apagar y encender
ALLOC=$(aws ec2 allocate-address --domain vpc --query AllocationId --output text)
aws ec2 wait instance-running --instance-ids "$ID"
aws ec2 associate-address --instance-id "$ID" --allocation-id "$ALLOC"
aws ec2 describe-addresses --allocation-ids "$ALLOC" \
  --query 'Addresses[0].PublicIp' --output text
