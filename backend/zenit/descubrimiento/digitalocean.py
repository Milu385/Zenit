"""Descubrimiento en DigitalOcean (H-009c).

Una sola llamada paginada: GET /v2/droplets?tag_name=zenit-nodo. El modelo de
identidad es plano: un droplet tiene un id numerico global, un nombre y una
region; no hay grupos ni cuentas anidadas.

Credencial: token de API con alcance de solo lectura (en el panel, "Read
Only", o un token personalizado con droplet:read). Con ese token un POST o un
DELETE responde 403, que es como se demuestra (docs/proveedores.md).
"""
from typing import Optional

import httpx

from zenit.catalogo import Recurso


class DigitalOcean:
    nombre = "digitalocean"

    def __init__(self, token: str, etiqueta: str = "zenit-nodo", entorno: Optional[str] = "lab",
                 url: str = "https://api.digitalocean.com", http: Optional[httpx.Client] = None):
        self._etiqueta = etiqueta
        self._entorno = entorno
        self._http = http or httpx.Client(base_url=url, timeout=20.0)
        self._cabeceras = {"Authorization": f"Bearer {token}"}

    def listar(self) -> list[Recurso]:
        salida = []
        ruta: Optional[str] = "/v2/droplets"
        params: Optional[dict] = {"per_page": 200, **({"tag_name": self._etiqueta} if self._etiqueta else {})}
        paginas = 0
        while ruta:
            paginas += 1
            if paginas > 50:
                raise RuntimeError("mas de 50 paginas de droplets: algo anda mal con la paginacion")
            r = self._http.get(ruta, params=params, headers=self._cabeceras)
            r.raise_for_status()
            datos = r.json()
            for d in datos.get("droplets", []):
                salida.append(Recurso(
                    proveedor=self.nombre,
                    id_nativo=f"droplet/{d['id']}",
                    nombre=d["name"],
                    region=(d.get("region") or {}).get("slug"),
                    entorno=self._entorno,
                    atributos={"estado": d.get("status"), "tamano": d.get("size_slug"),
                               "etiquetas": d.get("tags") or []},
                ))
            # la pagina siguiente viene como URL completa, con sus parametros
            ruta, params = (datos.get("links") or {}).get("pages", {}).get("next"), None
        return salida
