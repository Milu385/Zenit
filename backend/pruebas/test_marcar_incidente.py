"""H-037: POST /api/incidentes/{id}/estado (confirmar o marcar falso positivo)."""
import os
from datetime import datetime, timezone

import pytest
from psycopg.types.json import Jsonb

from conftest import preparar_pg
from sesion_prueba import con_sesion, opciones
from zenit import incidentes
from zenit.api.main import crear_app

PG = os.environ.get("ZENIT_PG_PRUEBAS")
con_pg = pytest.mark.skipif(not PG, reason="sin ZENIT_PG_PRUEBAS")

T0 = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)


class _SinSeries:
    def disponible(self):
        return True


def _app(pg_url=None):
    return crear_app(_SinSeries(), "/no/existe.json", pg_url=pg_url, **opciones())


# ------------------------------------------------------------------ sin base de datos
@pytest.mark.parametrize("rol", ["seguridad", "finanzas"])
def test_rol_sin_incidentes_gestionar_recibe_403(rol):
    r = con_sesion(_app(), rol).post("/api/incidentes/1/estado", json={"estado": "falso_positivo"})
    assert r.status_code == 403


@pytest.mark.parametrize("estado", ["cerrada", "detectada", "diagnosticada", "FALSO_POSITIVO", ""])
def test_solo_acepta_confirmada_y_falso_positivo(estado):
    r = con_sesion(_app(), "operador").post("/api/incidentes/1/estado", json={"estado": estado})
    assert r.status_code == 422


def test_sin_sesion_es_401():
    from fastapi.testclient import TestClient

    r = TestClient(_app()).post("/api/incidentes/1/estado", json={"estado": "confirmada"})
    assert r.status_code == 401


def test_la_ruta_aparece_en_la_especificacion():
    rutas = con_sesion(_app(), "operador").get("/api/openapi.json").json()["paths"]
    assert "post" in rutas["/api/incidentes/{id_incidente}/estado"]


def test_marcar_rechaza_un_estado_no_manual():
    with pytest.raises(ValueError):
        incidentes.marcar(None, 1, "cerrada", "daniel")


def test_postgres_caido_es_503():
    api = con_sesion(_app("postgresql://nadie@127.0.0.1:1/nada"), "operador")
    assert api.post("/api/incidentes/1/estado", json={"estado": "confirmada"}).status_code == 503


# ------------------------------------------------------------------ con PostgreSQL
@pytest.fixture
def pg():
    conexion = preparar_pg(PG)
    yield conexion
    conexion.close()


def _anomalia(c, estado="detectada", fin=None):
    with c.cursor() as cur:
        cur.execute(
            "INSERT INTO anomalias (activo, metrica, inicio, fin, estado, severidad, puntaje, evidencia, "
            "version_modelo) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            ("activo-a", "system_cpu_utilization", T0, fin, estado, "alta", 4.2, Jsonb({"ventana": 60}), "mad-v1"))
        id_ = cur.fetchone()[0]
    c.commit()
    return id_


def _fila(c, id_):
    with c.cursor() as cur:
        cur.execute("SELECT estado, marcada_por, marcada_en FROM anomalias WHERE id = %s", (id_,))
        return cur.fetchone()


@con_pg
@pytest.mark.parametrize("rol", ["operador", "administrador"])
def test_falso_positivo_guarda_quien_y_cuando(pg, rol):
    id_ = _anomalia(pg)
    antes = datetime.now(timezone.utc)
    r = con_sesion(_app(PG), rol).post(f"/api/incidentes/{id_}/estado", json={"estado": "falso_positivo"})
    assert r.status_code == 200, r.text
    assert r.json()["estado"] == "falso_positivo" and r.json()["marcada_por"] == rol
    estado, por, en = _fila(pg, id_)
    assert (estado, por) == ("falso_positivo", rol)
    assert en >= antes


@con_pg
def test_confirmada_y_despues_falso_positivo(pg):
    id_ = _anomalia(pg, estado="diagnosticada")
    api = con_sesion(_app(PG), "operador")
    assert api.post(f"/api/incidentes/{id_}/estado", json={"estado": "confirmada"}).status_code == 200
    assert _fila(pg, id_)[0] == "confirmada"
    assert api.get(f"/api/incidentes/{id_}").json()["marcada_por"] == "operador"
    # confirmada no es terminal: el operador puede cambiar de opinion
    assert api.post(f"/api/incidentes/{id_}/estado", json={"estado": "falso_positivo"}).status_code == 200
    assert _fila(pg, id_)[0] == "falso_positivo"


@con_pg
@pytest.mark.parametrize("estado", ["cerrada", "falso_positivo"])
def test_cerrado_o_descartado_no_cambia(pg, estado):
    id_ = _anomalia(pg, estado=estado, fin=T0 if estado == "cerrada" else None)
    r = con_sesion(_app(PG), "operador").post(f"/api/incidentes/{id_}/estado", json={"estado": "confirmada"})
    assert r.status_code == 409
    assert _fila(pg, id_) == (estado, None, None)


@con_pg
def test_incidente_inexistente_es_404(pg):
    r = con_sesion(_app(PG), "operador").post("/api/incidentes/999999/estado", json={"estado": "confirmada"})
    assert r.status_code == 404


@con_pg
def test_sin_permiso_no_toca_la_fila(pg):
    id_ = _anomalia(pg)
    r = con_sesion(_app(PG), "seguridad").post(f"/api/incidentes/{id_}/estado", json={"estado": "falso_positivo"})
    assert r.status_code == 403
    assert _fila(pg, id_) == ("detectada", None, None)
