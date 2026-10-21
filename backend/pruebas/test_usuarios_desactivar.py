"""H-044: desactivar un usuario sin borrarlo."""
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import ESQUEMAS_PG, preparar_pg
from sesion_prueba import CLAVE, SECRETO, con_sesion, usuarios_de_prueba
from zenit.api.main import crear_app
from zenit.gobernanza.usuarios import UsuariosPg, main as cli_usuarios, nuevo

PG = os.environ.get("ZENIT_PG_PRUEBAS")
con_pg = pytest.mark.skipif(not PG, reason="sin ZENIT_PG_PRUEBAS")
MIGRACION = Path(__file__).resolve().parents[2] / "deploy" / "postgres" / "06-usuarios-activo.sql"


class _RepoNulo:
    def disponible(self):
        return True


def _app(usuarios):
    return crear_app(_RepoNulo(), "/no/existe.json", usuarios=usuarios, secreto_sesion=SECRETO)


def test_desactivado_no_entra_y_recibe_el_mismo_mensaje_que_una_clave_mala():
    usuarios = usuarios_de_prueba()
    assert cli_usuarios(["desactivar", "operador"], usuarios) == 0
    c = TestClient(_app(usuarios))
    desactivado = c.post("/api/sesion", json={"usuario": "operador", "clave": CLAVE})
    clave_mala = c.post("/api/sesion", json={"usuario": "seguridad", "clave": "otra-clave-cualquiera"})
    assert desactivado.status_code == clave_mala.status_code == 401
    assert desactivado.json() == clave_mala.json()


def test_desactivar_corta_la_sesion_vigente():
    usuarios = usuarios_de_prueba()
    c = con_sesion(_app(usuarios), "operador")
    assert c.get("/api/yo").status_code == 200
    assert cli_usuarios(["desactivar", "operador"], usuarios) == 0
    assert c.get("/api/yo").status_code == 401


def test_desactivar_no_borra_y_activar_lo_devuelve(capsys):
    usuarios = usuarios_de_prueba()
    assert cli_usuarios(["desactivar", "finanzas"], usuarios) == 0
    u = usuarios.obtener("finanzas")
    assert u is not None and u.rol == "finanzas" and not u.activo
    cli_usuarios(["listar"], usuarios)
    assert "desactivado" in [l for l in capsys.readouterr().out.splitlines() if l.startswith("finanzas")][0]
    # cambiar el rol o la clave no lo reactiva
    assert cli_usuarios(["rol", "finanzas", "seguridad"], usuarios) == 0
    assert not usuarios.obtener("finanzas").activo
    assert cli_usuarios(["activar", "finanzas"], usuarios) == 0
    c = TestClient(_app(usuarios))
    assert c.post("/api/sesion", json={"usuario": "finanzas", "clave": CLAVE}).status_code == 200


def test_desactivar_dos_veces_o_a_alguien_que_no_existe():
    usuarios = usuarios_de_prueba()
    assert cli_usuarios(["desactivar", "operador"], usuarios) == 0
    assert cli_usuarios(["desactivar", "operador"], usuarios) == 0
    assert cli_usuarios(["desactivar", "nadie"], usuarios) == 1


# ------------------------------------------------------------------ con PostgreSQL
@con_pg
def test_la_migracion_se_puede_correr_dos_veces_y_conserva_a_los_usuarios():
    assert MIGRACION in ESQUEMAS_PG
    c = preparar_pg(PG)           # ya aplico todo deploy/postgres, 06 incluido
    try:
        c.execute("INSERT INTO usuarios (usuario, rol, clave_hash) VALUES ('viejo', 'operador', 'x')")
        c.commit()
        c.execute(MIGRACION.read_text())
        c.execute(MIGRACION.read_text())
        c.commit()
        assert c.execute("SELECT activo FROM usuarios WHERE usuario = 'viejo'").fetchone() == (True,)
    finally:
        c.close()


@con_pg
def test_desactivar_en_postgres():
    preparar_pg(PG).close()
    repo = UsuariosPg(PG)
    repo.guardar(nuevo("maria", "operador", CLAVE))
    assert repo.obtener("maria").activo
    assert cli_usuarios(["desactivar", "maria"], repo) == 0
    u = repo.obtener("maria")
    assert (u.rol, u.activo) == ("operador", False)
    assert [x.usuario for x in repo.listar()] == ["maria"]
    c = TestClient(_app(repo))
    assert c.post("/api/sesion", json={"usuario": "maria", "clave": CLAVE}).status_code == 401
