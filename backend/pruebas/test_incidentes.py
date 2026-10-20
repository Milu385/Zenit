"""H-038: GET /api/incidentes y GET /api/incidentes/{id}."""
import os
from datetime import datetime, timedelta, timezone

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
    """Las rutas de incidentes no tocan el almacen de series."""

    def disponible(self):
        return True


# ------------------------------------------------------------------ sin base de datos
def test_la_ventana_del_detector_se_traduce_a_un_intervalo():
    a = {"inicio": T0, "fin": T0 + timedelta(minutes=12), "version_modelo": "mad-v1 W=60 z=3.5 m=3",
         "evidencia": {"ventana": 60, "valor_observado": 0.93, "valor_esperado": 0.12, "metodo": "mad-v1",
                       "linea_base": {"mediana": 0.12, "mad": 0.02}}}
    ev = incidentes.evidencia_publica(a)
    assert ev["ventana"] == {"desde": (T0 - timedelta(minutes=60)).isoformat(),
                             "hasta": (T0 + timedelta(minutes=12)).isoformat()}
    assert ev["ventana_minutos"] == 60
    assert ev["metricas_relacionadas"] == []
    assert ev["linea_base"] == {"mediana": 0.12, "mad": 0.02}     # lo demas se conserva


def test_una_anomalia_abierta_tiene_ventana_hasta_ahora():
    ahora = T0 + timedelta(minutes=5)
    ev = incidentes.evidencia_publica({"inicio": T0, "fin": None, "version_modelo": "v",
                                       "evidencia": {"ventana": 30}}, ahora)
    assert ev["ventana"]["hasta"] == ahora.isoformat()
    assert ev["metodo"] == "v"          # sin metodo en la evidencia, la version del modelo


def test_una_ventana_que_ya_es_intervalo_no_se_toca():
    ventana = {"desde": "2026-10-20T11:00:00+00:00", "hasta": "2026-10-20T12:05:00+00:00"}
    ev = incidentes.evidencia_publica({"inicio": T0, "fin": None, "version_modelo": "v",
                                       "evidencia": {"ventana": ventana}})
    assert ev["ventana"] == ventana and "ventana_minutos" not in ev


def _app(pg_url=None):
    return crear_app(_SinSeries(), "/no/existe.json", pg_url=pg_url, **opciones())


def test_sin_postgres_la_lista_esta_vacia_y_el_detalle_no_existe():
    api = con_sesion(_app(), "operador")
    assert api.get("/api/incidentes").json() == []
    assert api.get("/api/incidentes/1").status_code == 404


def test_un_estado_desconocido_es_422():
    api = con_sesion(_app(), "operador")
    assert api.get("/api/incidentes", params={"estado": "abierta"}).status_code == 422


def test_finanzas_no_ve_incidentes():
    api = con_sesion(_app(), "finanzas")
    assert api.get("/api/incidentes").status_code == 403
    assert api.get("/api/incidentes/1").status_code == 403


def test_las_rutas_aparecen_en_la_especificacion():
    rutas = con_sesion(_app(), "operador").get("/api/openapi.json").json()["paths"]
    assert "/api/incidentes" in rutas and "/api/incidentes/{id_incidente}" in rutas


# ------------------------------------------------------------------ con PostgreSQL
@pytest.fixture
def pg():
    conexion = preparar_pg(PG)
    yield conexion
    conexion.close()


def _anomalia(c, activo, severidad, inicio, *, fin=None, estado="detectada", metrica="system_cpu_utilization"):
    with c.cursor() as cur:
        cur.execute(
            "INSERT INTO anomalias (activo, metrica, dimensiones, inicio, fin, estado, severidad, puntaje, "
            "evidencia, version_modelo) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (activo, metrica, Jsonb({"state": "user"}), inicio, fin, estado, severidad, 4.2,
             Jsonb({"ventana": 60, "valor_observado": 0.9, "valor_esperado": 0.1, "metodo": "mad-v1"}),
             "mad-v1 W=60 z=3.5 m=3"))
        id_ = cur.fetchone()[0]
    c.commit()
    return id_


def _diagnostico(c, anomalia_id, texto, generado):
    with c.cursor() as cur:
        cur.execute("INSERT INTO diagnosticos (anomalia_id, texto, evidencia_citada, modelo, tokens_entrada, "
                    "tokens_salida, generado) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (anomalia_id, texto, Jsonb([{"metrica": "system_cpu_utilization", "desde": "a", "hasta": "b",
                                                 "observacion": "sube"}]), "claude-x", 1800, 240, generado))
    c.commit()


@con_pg
def test_orden_por_severidad_y_luego_por_inicio(pg):
    baja = _anomalia(pg, "activo-a", "baja", T0 + timedelta(hours=3))
    media = _anomalia(pg, "activo-a", "media", T0)
    alta_vieja = _anomalia(pg, "activo-b", "alta", T0)
    alta_nueva = _anomalia(pg, "activo-a", "alta", T0 + timedelta(hours=1))
    lista = con_sesion(_app(PG), "operador").get("/api/incidentes").json()
    assert [i["id"] for i in lista] == [alta_nueva, alta_vieja, media, baja]
    assert set(lista[0]) == {"id", "activo", "metrica", "dimensiones", "inicio", "fin", "estado",
                             "severidad", "puntaje", "tiene_diagnostico"}


@con_pg
def test_filtra_por_estado_activo_y_rango(pg):
    cerrada = _anomalia(pg, "activo-a", "alta", T0, fin=T0 + timedelta(minutes=20), estado="cerrada")
    abierta = _anomalia(pg, "activo-a", "media", T0 + timedelta(hours=2))
    otra = _anomalia(pg, "activo-b", "baja", T0 + timedelta(hours=2))
    api = con_sesion(_app(PG), "seguridad")

    def ids(**p):
        r = api.get("/api/incidentes", params={k: (v.isoformat() if isinstance(v, datetime) else v)
                                               for k, v in p.items()})
        assert r.status_code == 200, r.text
        return {i["id"] for i in r.json()}

    assert ids(estado="cerrada") == {cerrada}
    assert ids(activo="activo-b") == {otra}
    # la cerrada termino antes del rango; la abierta empezo antes y sigue abierta
    assert ids(desde=T0 + timedelta(hours=1), hasta=T0 + timedelta(hours=3)) == {abierta, otra}
    # un rango que termina antes de que empezaran las abiertas
    assert ids(desde=T0, hasta=T0 + timedelta(minutes=30)) == {cerrada}
    assert api.get("/api/incidentes", params={"desde": (T0 + timedelta(hours=1)).isoformat(),
                                              "hasta": T0.isoformat()}).status_code == 422


@con_pg
def test_detalle_sin_diagnostico_trae_la_evidencia(pg):
    id_ = _anomalia(pg, "activo-a", "alta", T0)
    api = con_sesion(_app(PG), "operador")
    d = api.get(f"/api/incidentes/{id_}").json()
    assert d["diagnostico"] is None and d["tiene_diagnostico"] is False
    assert d["evidencia"]["valor_observado"] == 0.9
    assert d["evidencia"]["ventana"]["desde"] == (T0 - timedelta(minutes=60)).isoformat()
    assert d["evidencia"]["metricas_relacionadas"] == []
    assert d["version_modelo"] == "mad-v1 W=60 z=3.5 m=3"
    assert api.get("/api/incidentes", params={"activo": "activo-a"}).json()[0]["tiene_diagnostico"] is False


@con_pg
def test_detalle_trae_el_diagnostico_mas_reciente(pg):
    id_ = _anomalia(pg, "activo-a", "alta", T0, estado="diagnosticada")
    _diagnostico(pg, id_, "Hipotesis vieja", T0 + timedelta(minutes=1))
    _diagnostico(pg, id_, "Hipotesis nueva", T0 + timedelta(minutes=5))
    api = con_sesion(_app(PG), "administrador")
    d = api.get(f"/api/incidentes/{id_}").json()
    assert d["diagnostico"]["texto"] == "Hipotesis nueva"
    assert d["diagnostico"]["modelo"] == "claude-x"
    assert d["diagnostico"]["tokens_entrada"] == 1800
    assert d["diagnostico"]["evidencia_citada"][0]["metrica"] == "system_cpu_utilization"
    assert api.get("/api/incidentes").json()[0]["tiene_diagnostico"] is True
    assert api.get("/api/incidentes/999999").status_code == 404


def test_postgres_caido_es_503():
    api = con_sesion(_app("postgresql://nadie@127.0.0.1:1/nada"), "operador")
    assert api.get("/api/incidentes").status_code == 503
