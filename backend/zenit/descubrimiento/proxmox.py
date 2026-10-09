"""Descubrimiento en Proxmox VE (H-009b).

GET /api2/json/cluster/resources?type=vm devuelve las maquinas virtuales y
contenedores de todos los nodos del cluster en una llamada. El identificador
nativo es "qemu/105" o "lxc/110": el vmid es unico en el cluster pero se
puede reutilizar si se borra la maquina, por eso el catalogo guarda tambien
el nombre.

Credencial: un token de API de un usuario con el rol PVEAuditor sobre "/".
PVEAuditor solo tiene privilegios .Audit: puede leer, no puede encender,
apagar ni modificar nada (docs/proveedores.md tiene la prueba).

TLS: el certificado de Proxmox lo firma la CA propia del cluster
(/etc/pve/pve-root-ca.pem). Se verifica contra esa CA (PROXMOX_CA); nunca se
desactiva la verificacion. Si la API se alcanza por un tunel (localhost), el
nombre del certificado no coincide con la direccion: PROXMOX_NOMBRE_TLS dice
contra que nombre verificar.
"""
import ssl
from typing import Optional

import httpx

from zenit.catalogo import Recurso


class Proxmox:
    nombre = "proxmox"

    def __init__(self, url: str, token: str, ca: Optional[str] = None, nombre_tls: Optional[str] = None,
                 etiqueta: str = "zenit-nodo", region: str = "local", entorno: Optional[str] = "lab",
                 http: Optional[httpx.Client] = None):
        if "!" not in token or "=" not in token:
            raise ValueError("PROXMOX_TOKEN debe tener la forma usuario@realm!idtoken=secreto")
        self._etiqueta = etiqueta
        self._region = region
        self._entorno = entorno
        self._nombre_tls = nombre_tls
        verificar = ssl.create_default_context(cafile=ca) if ca else True
        self._http = http or httpx.Client(base_url=url.rstrip("/"), timeout=20.0, verify=verificar)
        self._cabeceras = {"Authorization": f"PVEAPIToken={token}"}

    def listar(self) -> list[Recurso]:
        extensiones = {"sni_hostname": self._nombre_tls} if self._nombre_tls else None
        r = self._http.get("/api2/json/cluster/resources", params={"type": "vm"},
                           headers=self._cabeceras, extensions=extensiones)
        r.raise_for_status()
        salida = []
        for vm in r.json().get("data", []):
            if vm.get("template"):
                continue
            etiquetas = [t for t in str(vm.get("tags") or "").replace(",", ";").split(";") if t]
            if self._etiqueta and self._etiqueta not in etiquetas:
                continue
            salida.append(Recurso(
                proveedor=self.nombre,
                id_nativo=str(vm["id"]),                       # qemu/105
                nombre=vm.get("name") or str(vm["id"]),
                region=self._region,
                entorno=self._entorno,
                atributos={"estado": vm.get("status"), "nodo": vm.get("node"), "vmid": vm.get("vmid"),
                           "etiquetas": etiquetas},
            ))
        return salida
