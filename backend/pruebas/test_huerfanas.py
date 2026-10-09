"""H-010 y H-011: la ingesta resuelve contra el catalogo de PostgreSQL, lleva
la cuenta de los origenes huerfanos y su telemetria previa se reasigna cuando
el activo entra al catalogo."""
import os
import time
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import otlp
from conftest import preparar_pg
from test_ingesta import armar, enviar, esperar_reposo, filas, influx  # noqa: F401 (fixture)
from zenit import catalogo, huerfanas
from zenit.api.main import crear_app
from zenit.catalogo import AcumuladorHuerfanas, CatalogoPG
from zenit.influx import ClienteInflux

PG = os.environ.get("ZENIT_PG_PRUEBAS")
con_pg = pytest.mark.skipif(not PG, reason="sin ZENIT_PG_PRUEBAS")
AHORA_NS = int(time.time()) * 10**9


@pytest.fixture
def pg():
    conexion = preparar_pg(PG)
    yield conexion
    conexion.close()


def test_acumulador_suma_por_origen_y_devuelve_si_falla_el_volcado():
    a = AcumuladorHuerfanas()
    a.sumar("zenit-nodo-x", "host.name", "proxmox", "metricas", 10)
    a.sumar("zenit-nodo-x", "host.name", "proxmox", "registros", 2)
    with pytest.raises(Exception):
        a.volcar("postgresql://nadie@127.0.0.1:1/nada")
    d = a.tomar()
    assert d["zenit-nodo-x"]["metricas"] == 10 and d["zenit-nodo-x"]["registros"] == 2


@con_pg
def test_de_huerfana_a_asociada_de_punta_a_punta(pg, influx):
    almacen, url = influx
    ingesta_catalogo = CatalogoPG(PG, arrancar=False)
    nucleo, srv, stub = armar(ingesta_catalogo, url)
    try:
        # 1. un nodo que nadie admitio emite: se guarda como sin_resolver
        enviar(stub, otlp.peticion(otlp.gauge("system.cpu.utilization",
                                              [otlp.punto(0.4, AHORA_NS - 60 * 10**9, cpu="cpu0")]),
                                   res=otlp.recurso("zenit-nodo-onprem")))
        esperar_reposo(nucleo)
        nucleo.volcar_huerfanas(PG)
        (h,) = catalogo.listar_huerfanas(pg)
        assert (h["clave"], h["tipo_clave"], h["metricas"], h["resuelve_a"], h["asociada_a"]) == (
            "zenit-nodo-onprem", "host.name", 1, None, None)
        with pg.cursor() as cur:   # su verdad de referencia tambien llego huerfana
            cur.execute("INSERT INTO inyecciones (uid, tipo, nodo, inicio, intensidad, estado) "
                        "VALUES ('u1', 'cpu', 'zenit-nodo-onprem', now(), '70', 'en_curso')")
        pg.commit()

        # 2. se admite; la ingesta lo toma en su siguiente recarga
        catalogo.agregar_manual(pg, "zenit-nodo-onprem", "activo-onprem-nodo-01", "proxmox")
        ingesta_catalogo.recargar()
        enviar(stub, otlp.peticion(otlp.gauge("system.cpu.utilization",
                                              [otlp.punto(0.5, AHORA_NS - 50 * 10**9, cpu="cpu0")]),
                                   res=otlp.recurso("zenit-nodo-onprem")))
        esperar_reposo(nucleo)
    finally:
        srv.stop(0)
    ids = sorted(f["zenit_asset_id"] for f in filas(almacen, "system_cpu_utilization"))
    assert ids == ["activo-onprem-nodo-01", "sin_resolver"]

    # 3. la asociacion reasigna lo previo, una sola vez
    cliente = ClienteInflux(url, "apiv3_prueba")
    hechas = huerfanas.asociar(pg, cliente)
    assert [(c, a, n) for c, a, n, _ in hechas] == [("zenit-nodo-onprem", "activo-onprem-nodo-01", 1)]
    assert huerfanas.asociar(pg, cliente) == []
    del_activo = [f for f in filas(almacen, "system_cpu_utilization") if f["zenit_asset_id"] == "activo-onprem-nodo-01"]
    assert sorted(f["value"] for f in del_activo) == [0.4, 0.5]
    assert all(f["host_name"] == "zenit-nodo-onprem" and f["cpu"] == "cpu0" for f in del_activo)
    (h,) = catalogo.listar_huerfanas(pg)
    assert h["asociada_a"] == "activo-onprem-nodo-01"
    with pg.cursor() as cur:
        cur.execute("SELECT activo FROM inyecciones WHERE uid = 'u1'")
        assert cur.fetchone() == ("activo-onprem-nodo-01",)


@con_pg
def test_retroceder_agregacion_al_asociar(pg, influx):
    _, url = influx
    with pg.cursor() as cur:
        cur.execute("INSERT INTO agregacion_progreso (nivel, hasta) VALUES ('1m', now()), ('1h', now())")
    pg.commit()
    catalogo.sumar_huerfanas(pg, {"n1": {"tipo_clave": "host.name", "proveedor": "aws", "metricas": 3,
                                         "primera": datetime(2026, 10, 8, tzinfo=timezone.utc),
                                         "ultima": datetime(2026, 10, 8, 1, tzinfo=timezone.utc)}})
    catalogo.agregar_manual(pg, "n1", "activo-aws-nodo-01", "aws")
    huerfanas.asociar(pg, ClienteInflux(url, "apiv3_prueba"))
    with pg.cursor() as cur:
        cur.execute("SELECT min(hasta) FROM agregacion_progreso")
        (hasta,) = cur.fetchone()
    assert hasta < datetime(2026, 10, 8, tzinfo=timezone.utc)


@con_pg
def test_api_lista_huerfanas(pg, tmp_path):
    catalogo.sumar_huerfanas(pg, {"zenit-nodo-do": {"tipo_clave": "host.name", "proveedor": "digitalocean",
                                                    "metricas": 120, "primera": datetime.now(timezone.utc),
                                                    "ultima": datetime.now(timezone.utc)}})

    class RepoQuieto:
        def disponible(self):
            return True

        def cpu_reciente(self, minutos=5):
            return {}

        def volumen_fuentes(self):
            return {}

    cliente = TestClient(crear_app(RepoQuieto(), tmp_path / "x.json", pg_url=PG))
    (h,) = cliente.get("/api/fuentes/huerfanas").json()
    assert h["clave"] == "zenit-nodo-do" and h["metricas"] == 120 and h["resuelve_a"] is None
