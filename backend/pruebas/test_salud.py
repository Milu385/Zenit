"""H-021: salud de las fuentes, inventario con estado y aviso en la serie."""
import os
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import falso_influx
import otlp
from conftest import preparar_pg
from test_ingesta import armar, enviar, esperar_reposo
from zenit import catalogo, salud
from zenit.api.main import crear_app
from zenit.influx import ClienteInflux
from zenit.protocolo_linea import construir_linea
from zenit.repositorio.influx import RepositorioInflux

PG = os.environ.get("ZENIT_PG_PRUEBAS")
con_pg = pytest.mark.skipif(not PG, reason="sin ZENIT_PG_PRUEBAS")


@pytest.fixture
def pg():
    conexion = preparar_pg(PG)
    yield conexion
    conexion.close()


@pytest.fixture
def influx():
    almacen, url, srv = falso_influx.levantar()
    for bd in ("zenit_raw", "zenit_interno"):
        almacen.bases[bd] = {"retencion": "7d", "tablas": {}}
    yield almacen, url
    srv.shutdown()


def test_estado_de_una_fuente():
    ahora = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)
    assert salud.estado_de(None, ahora) == "sin_datos"
    assert salud.estado_de(ahora - timedelta(minutes=4, seconds=59), ahora) == "ok"
    assert salud.estado_de(ahora - timedelta(minutes=5, seconds=1), ahora) == "degradado"
    assert salud.estado_activo({"estado": "retirado"}, ahora, 0, ahora) == "retirado"
    assert salud.estado_activo({"estado": "activo"}, ahora, 2, ahora) == "atencion"
    assert salud.estado_activo({"estado": "activo"}, ahora - timedelta(minutes=6), 2, ahora) == "degradado"


@con_pg
def test_la_ingesta_registra_cada_fuente_en_postgres_y_en_zenit_interno(pg, influx):
    almacen, url = influx
    catalogo.agregar_manual(pg, "zenit-nodo-aws", "activo-aws-nodo-01", "aws")
    cat = catalogo.CatalogoPG(PG, arrancar=False)
    nucleo, srv, stub = armar(cat, url)
    nucleo.interno = ClienteInflux(url, "apiv3_prueba")
    try:
        t = time.time_ns()
        enviar(stub, otlp.peticion(otlp.gauge("system.cpu.utilization",
                                              [otlp.punto(0.2, t, cpu="cpu0"), otlp.punto(0.3, t, cpu="cpu1")])))
        enviar(stub, otlp.peticion(otlp.gauge("system.cpu.utilization", [otlp.punto(0.2, t, cpu="cpu0")]),
                                   res=otlp.recurso("nodo-raro")))
        esperar_reposo(nucleo)
        nucleo.volcar_salud(PG)
    finally:
        srv.stop(0)
    filas = {(f["activo"], f["host_name"], f["senal"]): f["recibidas"] for f in salud.fuentes_pg(pg)}
    assert filas == {("activo-aws-nodo-01", "zenit-nodo-aws", "metricas"): 2,
                     ("sin_resolver", "nodo-raro", "metricas"): 1}
    internas = almacen.bases["zenit_interno"]["tablas"]
    assert sorted(f["value"] for f in internas["zenit_fuente_recibidas"]) == [1.0, 2.0]
    assert "zenit_ingesta_lineas_pendientes" in internas and "zenit_ingesta_memoria_bytes" in internas


@con_pg
def test_si_postgres_falla_la_cuenta_no_se_pierde(pg, influx):
    _, url = influx
    cat = catalogo.CatalogoPG(PG, arrancar=False)
    nucleo, srv, stub = armar(cat, url)
    try:
        enviar(stub, otlp.peticion(otlp.gauge("system.cpu.utilization", [otlp.punto(0.2, time.time_ns())])))
        esperar_reposo(nucleo)
        nucleo.volcar_salud("postgresql://nadie@127.0.0.1:1/nada")
        nucleo.volcar_salud(PG)
    finally:
        srv.stop(0)
    (f,) = salud.fuentes_pg(pg)
    assert f["recibidas"] == 1


def _api(pg, influx, ahora):
    almacen, url = influx
    cliente = ClienteInflux(url, "apiv3_prueba")
    canon = {"host_name": "zenit-nodo-do", "service_name": "host", "cloud_provider": "digitalocean",
             "cloud_region": "nyc3", "deployment_environment": "lab"}
    t = int((ahora - timedelta(minutes=1)).timestamp()) * 10**9
    lineas = [construir_linea("system_cpu_utilization", {**canon, "zenit_asset_id": "activo-do-nodo-01",
                                                         "cpu": "cpu0", "state": s}, v, t)
              for s, v in (("idle", 0.75), ("user", 0.25))]
    cliente.escribir("zenit_raw", "\n".join(lineas))
    cliente.escribir("zenit_interno", construir_linea(
        "zenit_fuente_recibidas", {"zenit_asset_id": "activo-do-nodo-01", "host_name": "zenit-nodo-do",
                                   "senal": "metricas", "cloud_provider": "digitalocean"}, 180.0, t))

    catalogo.agregar_manual(pg, "zenit-nodo-do", "activo-do-nodo-01", "digitalocean")
    catalogo.agregar_manual(pg, "zenit-nodo-onprem", "activo-onprem-nodo-01", "proxmox")
    catalogo.agregar_manual(pg, "zenit-nodo-nuevo", "activo-do-nuevo", "digitalocean")
    catalogo.agregar_manual(pg, "zenit-nodo-viejo", "activo-viejo", "aws")
    with pg.cursor() as cur:
        cur.execute("UPDATE activos SET estado = 'retirado' WHERE id = 'activo-viejo'")
        cur.execute("INSERT INTO anomalias (activo, metrica, inicio, severidad, puntaje, evidencia, version_modelo) "
                    "VALUES ('activo-do-nodo-01', 'system_cpu_utilization', now(), 'alta', 6, '{}', 'v0')")
    pg.commit()
    salud.guardar_pg(pg, {
        ("activo-do-nodo-01", "zenit-nodo-do", "metricas"): {"proveedor": "digitalocean", "recibidas": 500,
                                                             "errores": 0, "primera": ahora - timedelta(days=1),
                                                             "ultima": ahora - timedelta(seconds=20)},
        ("activo-onprem-nodo-01", "zenit-nodo-onprem", "metricas"): {
            "proveedor": "proxmox", "recibidas": 90, "errores": 1, "primera": ahora - timedelta(days=1),
            "ultima": ahora - timedelta(minutes=7)},
        ("sin_resolver", "zenit-nodo-x", "metricas"): {"proveedor": "aws", "recibidas": 3, "errores": 0,
                                                       "primera": ahora, "ultima": ahora},
    })
    repo = RepositorioInflux(cliente, "zenit_raw", cache_s=0)
    return TestClient(crear_app(repo, "/no/existe.json", pg_url=PG))


@con_pg
def test_api_de_fuentes_marca_degradada_la_que_lleva_mas_de_cinco_minutos(pg, influx):
    api = _api(pg, influx, datetime.now(timezone.utc))
    por_activo = {(f["activo"], f["host_name"]): f for f in api.get("/api/fuentes").json()}
    assert por_activo[("activo-onprem-nodo-01", "zenit-nodo-onprem")]["estado"] == "degradado"
    ok = por_activo[("activo-do-nodo-01", "zenit-nodo-do")]
    assert ok["estado"] == "ok" and ok["senales"]["metricas"]["recibidas_5min"] == 180
    assert por_activo[("activo-do-nuevo", "zenit-nodo-nuevo")]["estado"] == "sin_datos"
    assert por_activo[("sin_resolver", "zenit-nodo-x")]["huerfana"] is True
    assert ("activo-viejo", "zenit-nodo-viejo") not in por_activo      # los retirados no son fuentes
    assert list(por_activo)[0] == ("activo-onprem-nodo-01", "zenit-nodo-onprem")   # lo degradado primero


@con_pg
def test_inventario_con_estado_incidentes_y_cpu(pg, influx):
    api = _api(pg, influx, datetime.now(timezone.utc))
    activos = {a["id"]: a for a in api.get("/api/activos").json()}
    assert {k: a["estado"] for k, a in activos.items()} == {
        "activo-do-nodo-01": "atencion", "activo-onprem-nodo-01": "degradado",
        "activo-do-nuevo": "sin_datos", "activo-viejo": "retirado"}
    assert activos["activo-do-nodo-01"]["incidentes_abiertos"] == 1
    assert activos["activo-do-nodo-01"]["cpu_reciente"] == pytest.approx(0.25)
    assert activos["activo-onprem-nodo-01"]["ultima_recepcion"] is not None


@con_pg
def test_la_serie_avisa_si_la_fuente_esta_degradada(pg, influx):
    api = _api(pg, influx, datetime.now(timezone.utc))
    r = api.get("/api/activos/activo-do-nodo-01/series/system_cpu_utilization").json()
    assert r["fuente_degradada"] is False and r["ultima_recepcion"]
    r = api.get("/api/activos/activo-onprem-nodo-01/series/system_cpu_utilization").json()
    assert r["fuente_degradada"] is True


def test_crear_bases_incluye_la_interna(influx):
    from zenit import admin

    almacen, url = influx
    del almacen.bases["zenit_interno"]
    admin.crear_bases(ClienteInflux(url, "apiv3_prueba"))
    assert almacen.bases["zenit_interno"]["retencion"] == "7d"


def test_sin_postgres_el_inventario_responde_503_y_la_serie_se_sirve_sin_aviso(influx):
    _, url = influx
    repo = RepositorioInflux(ClienteInflux(url, "apiv3_prueba"), "zenit_raw", cache_s=0)
    api = TestClient(crear_app(repo, "/no/existe.json", pg_url="postgresql://nadie@127.0.0.1:1/nada"))
    assert api.get("/api/fuentes").status_code == 503
    assert api.get("/api/activos").status_code == 503
