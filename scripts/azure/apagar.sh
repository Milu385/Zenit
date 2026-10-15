#!/usr/bin/env bash
# [TU PC]  --  scripts/azure/apagar.sh [encender]
#
# Desasigna las VM (dejan de cobrar computo; el disco y las IP estaticas se
# siguen cobrando, poco). Con "encender" las vuelve a levantar. Las IP no
# cambian porque son estaticas. Sirve para estirar el credito de estudiante
# despues de la sustentacion; durante la campana NO se apaga nada.
set -euo pipefail
source "$(dirname "$0")/comun.sh"
VMS="$(az vm list -g "$GRUPO" --query "[].id" -o tsv)"
[ -n "$VMS" ] || { echo "no hay VM en $GRUPO"; exit 0; }
# shellcheck disable=SC2086
if [ "${1:-}" = "encender" ]; then az vm start --ids $VMS -o none; echo "Encendidas."
else az vm deallocate --ids $VMS -o none; echo "Desasignadas: no cobran computo."; fi
