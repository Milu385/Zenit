"""Descubrimiento de activos (H-009).

Un modulo por proveedor, todos con la misma forma: un objeto con `nombre` y
`listar() -> list[Recurso]`. Ninguno escribe en el catalogo; eso lo hace
zenit.catalogo.reconciliar, igual para todos. Es lo que evita que cada
proveedor termine escribiendo el catalogo a su manera (guia de la epica 1,
seccion 10).

Cada proveedor se activa solo si su credencial esta configurada, y la
credencial es siempre de solo lectura (ADR-010). Ver docs/proveedores.md.
"""
import logging
import os
from typing import Protocol

from zenit.catalogo import Recurso

log = logging.getLogger("zenit.descubrimiento")


class Proveedor(Protocol):
    nombre: str

    def listar(self) -> list[Recurso]:
        """Todos los recursos observados del proveedor. Lanza excepcion si
        no pudo listarlos completos: una lista parcial retiraria activos que
        siguen existiendo."""


def proveedores_desde_entorno(entorno=os.environ) -> list:
    """Los proveedores con credencial configurada."""
    activos: list = []
    etiqueta_entorno = entorno.get("DESCUBRIMIENTO_ENTORNO", "lab")
    if entorno.get("DO_TOKEN"):
        from .digitalocean import DigitalOcean

        activos.append(DigitalOcean(entorno["DO_TOKEN"], etiqueta=entorno.get("DO_ETIQUETA", "zenit-nodo"),
                                    entorno=etiqueta_entorno))
    if entorno.get("PROXMOX_URL") and entorno.get("PROXMOX_TOKEN"):
        from .proxmox import Proxmox

        activos.append(Proxmox(entorno["PROXMOX_URL"], entorno["PROXMOX_TOKEN"],
                               ca=entorno.get("PROXMOX_CA") or None,
                               nombre_tls=entorno.get("PROXMOX_NOMBRE_TLS") or None,
                               etiqueta=entorno.get("PROXMOX_ETIQUETA", "zenit-nodo"),
                               region=entorno.get("PROXMOX_REGION", "local"), entorno=etiqueta_entorno))
    if entorno.get("DESCUBRIR_AWS", "").lower() in ("1", "true", "si"):
        from .aws import AWS

        activos.append(AWS(entorno.get("AWS_REGION") or entorno.get("AWS_DEFAULT_REGION") or "us-east-1",
                           entorno=etiqueta_entorno))
    if entorno.get("DESCUBRIR_AZURE", "").lower() in ("1", "true", "si"):
        from .azure import Azure

        if entorno.get("AZURE_SUSCRIPCION") and entorno.get("AZURE_GRUPO"):
            activos.append(Azure(entorno["AZURE_SUSCRIPCION"], entorno["AZURE_GRUPO"],
                                 etiqueta=entorno.get("AZURE_ETIQUETA", "zenit"),
                                 valor=entorno.get("AZURE_VALOR_ETIQUETA", "nodo"), entorno=etiqueta_entorno))
        else:
            # sin esto el descubridor no arrancaria y se llevaria a los otros proveedores
            log.error("DESCUBRIR_AZURE=1 sin AZURE_SUSCRIPCION o AZURE_GRUPO: Azure no se descubre")
    return activos


__all__ = ["Proveedor", "Recurso", "proveedores_desde_entorno"]
