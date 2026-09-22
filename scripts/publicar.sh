#!/usr/bin/env bash
set -euo pipefail
ORG="${ORG_GITHUB:?exporta ORG_GITHUB}"
VERSION="${1:?uso: publicar.sh <version>   p.ej. 0.1.0}"

echo "$GITHUB_TOKEN" | docker login ghcr.io -u "$ORG" --password-stdin

for s in pedidos catalogo carga; do
  docker build --platform linux/amd64 \
    -t "ghcr.io/$ORG/zenit-$s:$VERSION" \
    "laboratorio/servicios/$s"
  docker push "ghcr.io/$ORG/zenit-$s:$VERSION"
done

echo "Publicado $VERSION. Ponlo en VERSION_APP en los cuatro archivos de entorno."
