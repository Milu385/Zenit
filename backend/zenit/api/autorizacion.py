"""Autenticacion y autorizacion de la API (RNF-SEG-04).

La sesion viaja como `Authorization: Bearer <token>` (contrato, seccion 1).
Cada ruta declara lo que exige con @requiere("permiso"), @autenticada (basta
con tener sesion) o @publica. Una dependencia global revisa todas las
peticiones:
- ruta publica: pasa;
- sin sesion valida: 401;
- ruta que no declaro nada, o rol sin el permiso: 403.
Asi, olvidar declarar el permiso de una ruta nueva la cierra en vez de abrirla.
"""
from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Callable, Optional

from fastapi import HTTPException, Request

from zenit.gobernanza.permisos import permitido
from zenit.gobernanza.sesiones import Sesiones
from zenit.gobernanza.usuarios import RepositorioUsuarios

PUBLICA = object()
AUTENTICADA = object()
_PERMISO = "__zenit_permiso__"
_TOKEN_NODOS_VALE_PARA = "configuraciones:enviar"


def requiere(permiso: str) -> Callable:
    def marcar(f):
        setattr(f, _PERMISO, permiso)
        return f
    return marcar


def publica(f):
    setattr(f, _PERMISO, PUBLICA)
    return f


def autenticada(f):
    setattr(f, _PERMISO, AUTENTICADA)
    return f


@dataclass(frozen=True)
class Identidad:
    usuario: str
    rol: str
    expira: Optional[int]  # None para el token de nodo


def token_de(request: Request) -> Optional[str]:
    cabecera = request.headers.get("authorization", "")
    if cabecera[:7].lower() == "bearer ":
        return cabecera[7:].strip() or None
    return None


def crear_autorizador(sesiones: Sesiones, usuarios: RepositorioUsuarios, token_nodos: str = ""):
    async def autorizar(request: Request) -> None:
        endpoint = request.scope.get("endpoint")
        exige = getattr(endpoint, _PERMISO, None)
        if exige is PUBLICA:
            return
        token = token_de(request)
        # Los nodos envian su instantanea con un token propio, que solo vale
        # para eso. No es una sesion de usuario.
        if token_nodos and token and hmac.compare_digest(token.encode(), token_nodos.encode()):
            if exige != _TOKEN_NODOS_VALE_PARA:
                raise HTTPException(403, detail="el token de nodo solo sirve para enviar instantaneas")
            request.state.identidad = Identidad("nodo", "nodo", None)
            return
        identidad: Optional[Identidad] = None
        sesion = sesiones.leer(token)
        if sesion:
            # se relee el usuario: uno borrado o con otro rol no conserva lo que tenia
            u = usuarios.obtener(sesion.usuario)
            if u:
                identidad = Identidad(u.usuario, u.rol, sesion.expira)
        if identidad is None:
            raise HTTPException(401, detail="inicia sesion para continuar", headers={"WWW-Authenticate": "Bearer"})
        request.state.identidad = identidad
        if exige is AUTENTICADA:
            return
        if not isinstance(exige, str) or not permitido(identidad.rol, exige):
            raise HTTPException(403, detail="tu rol no tiene permiso para esto")

    return autorizar


def identidad(request: Request) -> Identidad:
    return request.state.identidad
