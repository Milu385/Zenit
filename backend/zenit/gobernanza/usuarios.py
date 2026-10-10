"""Usuarios y su rol (tabla `usuarios`), mas la herramienta para gestionarlos.

Mientras no haya pantalla de gestion de usuarios, se hace desde el contenedor
de la API (el encargo lo permite si falta tiempo):

    docker compose exec api python -m zenit.gobernanza.usuarios crear daniel administrador
    docker compose exec api python -m zenit.gobernanza.usuarios rol maria operador
    docker compose exec api python -m zenit.gobernanza.usuarios clave maria
    docker compose exec api python -m zenit.gobernanza.usuarios listar
    docker compose exec api python -m zenit.gobernanza.usuarios borrar maria

La clave se pide por la terminal (getpass) y nunca pasa por argumentos, que
quedan en el historial del shell y en la lista de procesos.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from dataclasses import dataclass
from typing import Optional, Protocol

from zenit.gobernanza import claves
from zenit.gobernanza.permisos import ROLES


@dataclass(frozen=True)
class Usuario:
    usuario: str
    rol: str
    clave_hash: str


class RepositorioUsuarios(Protocol):
    def obtener(self, usuario: str) -> Optional[Usuario]: ...
    def guardar(self, u: Usuario) -> None: ...
    def borrar(self, usuario: str) -> bool: ...
    def listar(self) -> list[Usuario]: ...


def _validar(usuario: str, rol: str) -> None:
    if not usuario or len(usuario) > 120 or any(c.isspace() for c in usuario):
        raise ValueError("usuario invalido: sin espacios y de hasta 120 caracteres")
    if rol not in ROLES:
        raise ValueError(f"rol desconocido {rol!r}; validos: {', '.join(ROLES)}")


def nuevo(usuario: str, rol: str, clave: str) -> Usuario:
    _validar(usuario, rol)
    return Usuario(usuario, rol, claves.derivar(clave))


class UsuariosMemoria:
    """Para pruebas y para correr la API sin PostgreSQL."""

    def __init__(self, iniciales: list[Usuario] | None = None):
        self._datos = {u.usuario: u for u in iniciales or []}

    def obtener(self, usuario):
        return self._datos.get(usuario)

    def guardar(self, u):
        _validar(u.usuario, u.rol)
        self._datos[u.usuario] = u

    def borrar(self, usuario):
        return self._datos.pop(usuario, None) is not None

    def listar(self):
        return sorted(self._datos.values(), key=lambda u: u.usuario)


class UsuariosPg:
    def __init__(self, url: str):
        self._url = url

    def _conexion(self):
        import psycopg

        return psycopg.connect(self._url, autocommit=True)

    def obtener(self, usuario):
        with self._conexion() as c:
            fila = c.execute("SELECT usuario, rol, clave_hash FROM usuarios WHERE usuario = %s", (usuario,)).fetchone()
        return Usuario(*fila) if fila else None

    def guardar(self, u):
        _validar(u.usuario, u.rol)
        with self._conexion() as c:
            c.execute(
                "INSERT INTO usuarios (usuario, rol, clave_hash) VALUES (%s, %s, %s) "
                "ON CONFLICT (usuario) DO UPDATE SET rol = EXCLUDED.rol, clave_hash = EXCLUDED.clave_hash",
                (u.usuario, u.rol, u.clave_hash),
            )

    def borrar(self, usuario):
        with self._conexion() as c:
            return c.execute("DELETE FROM usuarios WHERE usuario = %s", (usuario,)).rowcount > 0

    def listar(self):
        with self._conexion() as c:
            filas = c.execute("SELECT usuario, rol, clave_hash FROM usuarios ORDER BY usuario").fetchall()
        return [Usuario(*f) for f in filas]


def _pedir_clave() -> str:
    while True:
        clave = getpass.getpass("Clave: ")
        try:
            claves.validar(clave)
        except claves.ClaveInvalida as e:
            print(e, file=sys.stderr)
            continue
        if getpass.getpass("Repitela: ") == clave:
            return clave
        print("no coinciden", file=sys.stderr)


def main(argv: list[str] | None = None, repo: RepositorioUsuarios | None = None, pedir_clave=_pedir_clave) -> int:
    p = argparse.ArgumentParser(prog="python -m zenit.gobernanza.usuarios")
    sub = p.add_subparsers(dest="orden", required=True)
    c = sub.add_parser("crear", help="crea un usuario (pide la clave)")
    c.add_argument("usuario")
    c.add_argument("rol", choices=ROLES)
    r = sub.add_parser("rol", help="cambia el rol")
    r.add_argument("usuario")
    r.add_argument("rol", choices=ROLES)
    k = sub.add_parser("clave", help="cambia la clave (la pide)")
    k.add_argument("usuario")
    b = sub.add_parser("borrar")
    b.add_argument("usuario")
    sub.add_parser("listar")
    a = p.parse_args(argv)

    if repo is None:
        url = os.environ.get("PG_URL")
        if not url:
            raise SystemExit("falta PG_URL (postgresql://usuario:clave@postgres:5432/zenit)")
        repo = UsuariosPg(url)

    if a.orden == "listar":
        for u in repo.listar():
            print(f"{u.usuario:30s} {u.rol}")
        return 0
    if a.orden == "borrar":
        if not repo.borrar(a.usuario):
            print(f"no existe {a.usuario}", file=sys.stderr)
            return 1
        print(f"borrado {a.usuario}")
        return 0

    actual = repo.obtener(a.usuario)
    if a.orden == "crear":
        if actual:
            print(f"ya existe {a.usuario}; usa 'rol' o 'clave'", file=sys.stderr)
            return 1
        repo.guardar(nuevo(a.usuario, a.rol, pedir_clave()))
        print(f"creado {a.usuario} ({a.rol})")
        return 0
    if not actual:
        print(f"no existe {a.usuario}", file=sys.stderr)
        return 1
    if a.orden == "rol":
        repo.guardar(Usuario(actual.usuario, a.rol, actual.clave_hash))
        print(f"{a.usuario} ahora es {a.rol}")
    else:
        repo.guardar(Usuario(actual.usuario, actual.rol, claves.derivar(pedir_clave())))
        print(f"clave de {a.usuario} cambiada")
    return 0


if __name__ == "__main__":
    sys.exit(main())
