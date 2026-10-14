"""Descubrimiento en Azure (H-009d, H-014).

Una llamada paginada a Azure Resource Manager por grupo de recursos:
GET /subscriptions/{s}/resourceGroups/{g}/providers/Microsoft.Compute/virtualMachines
y se quedan las maquinas con la etiqueta zenit=nodo. El modelo de identidad
es jerarquico, a diferencia de DigitalOcean: suscripcion, grupo de recursos y
recurso. El id de recurso completo cambia si la VM se mueve de grupo, asi que
la identidad nativa que se guarda es `vmId`, el GUID que Azure asigna a la
maquina y no cambia en su vida.

Credencial: la identidad administrada de la VM de la plataforma, con el rol
Reader sobre el grupo de recursos. No hay secreto que guardar: el token lo da
el servicio de metadatos de la instancia (IMDS) y dura unas horas. Con ese
rol un PUT o un DELETE responde 403 AuthorizationFailed, que es como se
demuestra que es de solo lectura (docs/proveedores.md).
"""
import time
from typing import Optional

import httpx

from zenit.catalogo import Recurso

API_VERSION = "2024-07-01"
IMDS = "http://169.254.169.254/metadata/identity/oauth2/token"
RECURSO_ARM = "https://management.azure.com/"


class Azure:
    nombre = "azure"

    def __init__(self, suscripcion: str, grupo: str, etiqueta: str = "zenit", valor: str = "nodo",
                 entorno: Optional[str] = "lab", url: str = "https://management.azure.com",
                 http: Optional[httpx.Client] = None, imds: Optional[httpx.Client] = None,
                 token: Optional[str] = None):
        if not suscripcion or not grupo:
            raise ValueError("Azure necesita AZURE_SUSCRIPCION y AZURE_GRUPO")
        self._suscripcion = suscripcion
        self._grupo = grupo
        self._etiqueta = etiqueta
        self._valor = valor
        self._entorno = entorno
        self._http = http or httpx.Client(base_url=url, timeout=20.0)
        # IMDS no pasa por proxies y responde en milisegundos
        self._imds = imds or httpx.Client(timeout=5.0, trust_env=False)
        self._token_fijo = token
        self._token: Optional[str] = None
        self._vence = 0.0

    def _token_arm(self) -> str:
        if self._token_fijo:
            return self._token_fijo
        if self._token and time.time() < self._vence - 300:
            return self._token
        r = self._imds.get(IMDS, params={"api-version": "2018-02-01", "resource": RECURSO_ARM},
                           headers={"Metadata": "true"})
        if r.status_code == 400:
            raise RuntimeError("la VM no tiene identidad administrada; ver docs/proveedores.md, seccion Azure")
        r.raise_for_status()
        datos = r.json()
        self._token = datos["access_token"]
        self._vence = float(datos.get("expires_on") or time.time() + 3600)
        return self._token

    def listar(self) -> list[Recurso]:
        cabeceras = {"Authorization": f"Bearer {self._token_arm()}"}
        ruta: Optional[str] = (f"/subscriptions/{self._suscripcion}/resourceGroups/{self._grupo}"
                               "/providers/Microsoft.Compute/virtualMachines")
        params: Optional[dict] = {"api-version": API_VERSION}
        salida = []
        paginas = 0
        while ruta:
            paginas += 1
            if paginas > 50:
                raise RuntimeError("mas de 50 paginas de maquinas: algo anda mal con la paginacion")
            r = self._http.get(ruta, params=params, headers=cabeceras)
            r.raise_for_status()
            datos = r.json()
            for vm in datos.get("value", []):
                etiquetas = vm.get("tags") or {}
                if self._etiqueta and etiquetas.get(self._etiqueta) != self._valor:
                    continue
                props = vm.get("properties") or {}
                salida.append(Recurso(
                    proveedor=self.nombre,
                    id_nativo=f"vm/{props.get('vmId') or vm['id']}",
                    # el nombre de la VM es el ZENIT_NODO del nodo, igual que en DigitalOcean
                    nombre=vm["name"],
                    region=vm.get("location"),
                    entorno=self._entorno,
                    atributos={"tamano": (props.get("hardwareProfile") or {}).get("vmSize"),
                               "grupo": self._grupo, "etiquetas": etiquetas,
                               "recurso": vm.get("id")},
                ))
            # la pagina siguiente viene como URL completa, con sus parametros
            ruta, params = datos.get("nextLink"), None
        return salida
