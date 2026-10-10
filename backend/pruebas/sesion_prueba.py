"""Ayuda para las pruebas de la API: usuarios en memoria y un cliente con sesion."""
from fastapi.testclient import TestClient

from zenit.gobernanza import claves
from zenit.gobernanza.usuarios import Usuario, UsuariosMemoria

CLAVE = "clave-de-prueba-larga"
SECRETO = b"s" * 32

# derivar con bcrypt es lento a proposito: se hace una vez para todas las pruebas
_HASH = claves.derivar(CLAVE)


def usuarios_de_prueba() -> UsuariosMemoria:
    return UsuariosMemoria([Usuario(rol, rol, _HASH) for rol in ("administrador", "operador", "seguridad", "finanzas")])


def opciones() -> dict:
    return {"usuarios": usuarios_de_prueba(), "secreto_sesion": SECRETO}


def con_sesion(app, usuario: str = "operador") -> TestClient:
    cliente = TestClient(app)
    r = cliente.post("/api/sesion", json={"usuario": usuario, "clave": CLAVE})
    assert r.status_code == 200, r.text
    cliente.headers["Authorization"] = f"Bearer {r.json()['token']}"
    return cliente
