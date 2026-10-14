#!/usr/bin/env bash
# [TU PC]  --  scripts/azure/00-verificar.sh
#
# Antes de crear nada: que regiones permite la suscripcion, si los dos
# tamanos existen en la region elegida y cuanta cuota de vCPU queda. No crea
# ni cambia nada.
set -euo pipefail
source "$(dirname "$0")/comun.sh"

echo "== Suscripcion"
az account show --query "{nombre:name, id:id, estado:state}" -o table

echo; echo "== Regiones permitidas por politica (Azure for Students suele traer esta restriccion)"
az policy assignment list --disable-scope-strict-match --query "[?contains(displayName, 'region') || contains(displayName, 'location')].{politica:displayName, regiones:parameters.listOfAllowedLocations.value}" -o json \
  || echo "(no se pudo leer la politica; revisala en el portal: Policy > Assignments)"

echo; echo "== Tamanos en $REGION"
for t in "$TAMANO_PLATAFORMA" "$TAMANO_NODO"; do
  restr=$(az vm list-skus -l "$REGION" --size "$t" --resource-type virtualMachines \
          --query "[?name=='$t'].restrictions[].reasonCode" -o tsv 2>/dev/null || true)
  existe=$(az vm list-skus -l "$REGION" --size "$t" --resource-type virtualMachines --query "[?name=='$t'] | length(@)" -o tsv)
  if [ "$existe" = "0" ]; then echo "  $t: NO existe en $REGION"
  elif [ -n "$restr" ]; then echo "  $t: restringido en $REGION ($restr)"
  else echo "  $t: disponible"; fi
done

echo; echo "== Cuota de vCPU en $REGION (hacen falta 4: 2 de la plataforma y 2 del nodo)"
az vm list-usage -l "$REGION" --query "[?contains(name.localizedValue, 'Total Regional') || contains(name.localizedValue, 'BS') || contains(name.localizedValue, 'Bsv2') || contains(name.localizedValue, 'Basv2')].{cuota:name.localizedValue, usado:currentValue, limite:limit}" -o table

cat <<FIN

Si algo sale "restringido", "NO existe" o la cuota no alcanza:
  export REGION=<otra de las permitidas>          y vuelve a correr este script, o
  export TAMANO_PLATAFORMA=Standard_B2ms TAMANO_NODO=Standard_B2s   (serie B clasica)
FIN
