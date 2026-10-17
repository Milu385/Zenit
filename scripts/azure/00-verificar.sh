#!/usr/bin/env bash
# [TU PC]  --  scripts/azure/00-verificar.sh
#
# Antes de crear nada: tipo de suscripcion, regiones permitidas, si los dos
# tamanos existen en la region elegida y cuanta cuota de vCPU queda. No crea
# ni cambia nada. La consulta de tamanos tarda uno o dos minutos: Azure
# devuelve el catalogo completo de la region y se filtra aqui.
set -euo pipefail
source "$(dirname "$0")/comun.sh"
SUSCRIPCION="$(az account show --query id -o tsv)"

echo "== Suscripcion"
az account show --query "{nombre:name, id:id, estado:state}" -o table
echo -n "Oferta: "
az rest --method get --url "https://management.azure.com/subscriptions/$SUSCRIPCION?api-version=2022-12-01" \
  --query "subscriptionPolicies.quotaId" -o tsv 2>/dev/null || echo "(no se pudo leer)"
echo "  AzureForStudents_* = Azure for Students · FreeTrial_* = prueba gratuita · PayAsYouGo_* = pago por uso"

echo; echo "== Regiones permitidas por politica (vacio = sin restriccion de region)"
az policy assignment list --disable-scope-strict-match \
  --query "[?parameters.listOfAllowedLocations].parameters.listOfAllowedLocations.value[]" -o tsv 2>/dev/null \
  | sort -u | tr '\n' ' '; echo

echo; echo "== Tamanos en $REGION (uno o dos minutos)"
az vm list-skus -l "$REGION" --resource-type virtualMachines -o tsv \
  --query "[?name=='$TAMANO_PLATAFORMA' || name=='$TAMANO_NODO'].[name, join(',', restrictions[].reasonCode)]" \
  > /tmp/zenit-skus.tsv
for t in "$TAMANO_PLATAFORMA" "$TAMANO_NODO"; do
  fila="$(awk -F'\t' -v t="$t" '$1 == t' /tmp/zenit-skus.tsv)"
  if [ -z "$fila" ]; then echo "  $t: no se pudo comprobar (el catalogo vino vacio; ver probar-capacidad.sh)"
  elif [ -n "$(cut -f2 <<<"$fila")" ]; then echo "  $t: restringido en $REGION ($(cut -f2 <<<"$fila"))"
  else echo "  $t: disponible"; fi
done
rm -f /tmp/zenit-skus.tsv

echo; echo "== Cuota de vCPU en $REGION (hacen falta 4: 2 de la plataforma y 2 del nodo)"
az vm list-usage -l "$REGION" -o table \
  --query "[?contains(name.localizedValue, 'Total Regional') || contains(name.localizedValue, 'Basv2') || contains(name.localizedValue, 'BS Family')].{cuota:name.localizedValue, usado:currentValue, limite:limit}"

cat <<FIN

La capacidad real solo se ve al desplegar: en pruebas gratuitas Azure responde
"SkuNotAvailable ... Capacity Restrictions" aunque haya cuota. Para buscar una
region con capacidad sin crear nada:  bash scripts/azure/probar-capacidad.sh

Si algo sale "restringido" o la cuota no alcanza:
  export REGION=<otra de las permitidas>          y vuelve a correr este script, o
  export TAMANO_PLATAFORMA=Standard_B2ms TAMANO_NODO=Standard_B2s   (serie B clasica)
FIN
