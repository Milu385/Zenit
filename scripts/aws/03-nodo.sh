#!/usr/bin/env bash
# [TU PC]  --  scripts/aws/03-nodo.sh <subred> <sg-nodo> <sg-plataforma>
set -euo pipefail
SUBRED="${1:?falta la subred del nodo}"
SG_NODO="${2:?falta el grupo del nodo}"
SG_PLAT="${3:?falta el grupo de la plataforma}"
ROL_INSTANCIA="${ROL_INSTANCIA:-LabInstanceProfile}"

AMI=$(aws ssm get-parameters \
  --names /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id \
  --query 'Parameters[0].Value' --output text)

ID=$(aws ec2 run-instances \
  --image-id "$AMI" --instance-type t3.small \
  --subnet-id "$SUBRED" \
  --security-group-ids "$SG_NODO" \
  --iam-instance-profile "Name=$ROL_INSTANCIA" \
  --metadata-options "HttpTokens=required,HttpEndpoint=enabled" \
  --block-device-mappings '[{"DeviceName":"/dev/xvda","Ebs":{"VolumeSize":20,"VolumeType":"gp3","DeleteOnTermination":true}}]' \
  --user-data file://scripts/aws/userdata-docker.sh \
  --tag-specifications 'ResourceType=instance,Tags=[
      {Key=Name,Value=zenit-nodo-aws},
      {Key=zenit:rol,Value=nodo},
      {Key=zenit:entorno,Value=aws}]' \
  --query 'Instances[0].InstanceId' --output text)

aws ec2 wait instance-running --instance-ids "$ID"
IP=$(aws ec2 describe-instances --instance-ids "$ID" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)

echo "Nodo $ID con IP $IP"
bash "$(dirname "$0")/admitir-nodo.sh" "$SG_PLAT" "$IP" "nodo-aws"
