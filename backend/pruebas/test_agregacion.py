"""H-018 (resumenes), H-019 (seleccion de nivel) y H-020 (retencion).

El SQL de la agregacion y del repositorio corre de verdad en DataFusion, el
motor de consulta de InfluxDB 3, sobre el doble de prueba. El progreso vive en
PostgreSQL, asi que estas pruebas necesitan ZENIT_PG_PRUEBAS.
"""
import os
from datetime import datetime, timedelta, timezone

import pytest

from sesion_prueba import con_sesion, opciones
import falso_influx
from conftest import preparar_pg
from zenit.agregacion import Agregador
from zenit.influx import ClienteInflux
from zenit.protocolo_linea import construir_linea
from zenit.repositorio import Resolucion
from zenit.repositorio.influx import RepositorioInflux

PG = os.environ.get("ZENIT_PG_PRUEBAS")
con_pg = pytest.mark.skipif(not PG, reason="sin ZENIT_PG_PRUEBAS")

ACTIVO = "activo-do-nodo-01"
CANON = {"zenit_asset_id": ACTIVO, "host_name": "zenit-nodo-do", "service_name": "host",
         "cloud_provider": "digitalocean", "cloud_region": "nyc3", "deployment_environment": "lab"}
# Tres horas de datos que terminan en una hora en punto; el agregador corre
# con el reloj fijo cinco minutos despues.
FIN = (datetime.now(timezone.utc) - timedelta(minutes=10)).replace(minute=0, second=0, microsecond=0)
AHORA = FIN + timedelta(minutes=5)
INICIO = FIN - timedelta(hours=3)
FALTAN = {INICIO + timedelta(minutes=7, seconds=20), INICIO + timedelta(minutes=7, seconds=30)}


def ns(m: datetime) -> int:
    return int(m.timestamp()) * 10**9


@pytest.fixture
def entorno():
    pg = preparar_pg(PG)
    almacen, url, srv = falso_influx.levantar()
    for bd in ("zenit_raw", "zenit_1m", "zenit_1h", "zenit_interno"):
        almacen.bases[bd] = {"retencion": "30d", "tablas": {}}
    cliente = ClienteInflux(url, "apiv3_prueba")
    lineas = []
    t = INICIO
    while t < FIN:
        if t not in FALTAN:
            for cpu in ("cpu0", "cpu1"):
                lineas.append(construir_linea("system_cpu_utilization", {**CANON, "cpu": cpu, "state": "user"},
                                              0.2 + (0.4 if t.minute % 2 else 0.0), ns(t)))
        t += timedelta(seconds=10)
    cliente.escribir("zenit_raw", "\n".join(lineas))
    yield cliente, almacen, pg
    pg.close()
    srv.shutdown()


def _agregar(cliente, ahora=AHORA, presupuesto=60.0):
    return Agregador(cliente, PG, relleno=timedelta(hours=4), presupuesto_s=presupuesto).ciclo(ahora)


@con_pg
def test_resumen_por_minuto_con_conteo_y_minuto_incompleto(entorno):
    cliente, almacen, _ = entorno
    r = _agregar(cliente)
    filas = almacen.bases["zenit_1m"]["tablas"]["system_cpu_utilization"]
    del_minuto = {(f["cpu"], f["time"]): f for f in filas}
    f = del_minuto[("cpu0", ns(INICIO + timedelta(minutes=1)))]
    assert (f["value_avg"], f["value_min"], f["value_max"], f["value_count"]) == pytest.approx((0.6, 0.6, 0.6, 6))
    incompleto = del_minuto[("cpu0", ns(INICIO + timedelta(minutes=7)))]
    assert incompleto["value_count"] == 4                       # se marca, no se rellena
    assert all(f["zenit_asset_id"] == ACTIVO and f["host_name"] == "zenit-nodo-do" for f in filas)
    assert len(filas) == 2 * 180
    assert r["retraso_1m_s"] <= 120                              # RNF-REN-05: dos ciclos de 60 s


@con_pg
def test_resumen_por_hora_es_el_promedio_exacto_de_lo_crudo(entorno):
    cliente, almacen, _ = entorno
    _agregar(cliente)
    horas = [f for f in almacen.bases["zenit_1h"]["tablas"]["system_cpu_utilization"] if f["cpu"] == "cpu0"]
    assert len(horas) == 3
    primera = min(horas, key=lambda f: f["time"])
    # 30 minutos a 0.2 y 30 a 0.6, menos dos puntos de un minuto impar (0.6)
    esperado = (180 * 0.2 + 178 * 0.6) / 358
    assert primera["value_count"] == 358
    assert primera["value_avg"] == pytest.approx(esperado)
    assert (primera["value_min"], primera["value_max"]) == pytest.approx((0.2, 0.6))


@con_pg
def test_reejecutar_no_duplica_ni_cambia_nada(entorno):
    from zenit import catalogo

    cliente, almacen, pg = entorno
    _agregar(cliente)
    antes = {bd: [dict(f) for f in almacen.bases[bd]["tablas"]["system_cpu_utilization"]]
             for bd in ("zenit_1m", "zenit_1h")}
    catalogo.retroceder_agregacion(pg, INICIO - timedelta(hours=1))   # se pide rehacer todo
    _agregar(cliente)
    _agregar(cliente)
    for bd, filas in antes.items():
        despues = almacen.bases[bd]["tablas"]["system_cpu_utilization"]
        assert sorted(map(lambda f: sorted(f.items()), despues)) == sorted(map(lambda f: sorted(f.items()), filas))


@con_pg
def test_el_relleno_avanza_por_tramos_y_sigue_en_el_ciclo_siguiente(entorno):
    cliente, almacen, _ = entorno
    r1 = _agregar(cliente, presupuesto=0)        # un tramo de una hora y corta
    r2 = _agregar(cliente, presupuesto=0)
    assert r1["hasta_1m"] < r2["hasta_1m"]
    for _ in range(4):
        r = _agregar(cliente, presupuesto=0)
    assert r["retraso_1m_s"] <= 120


@con_pg
def test_el_repositorio_elige_el_nivel_por_el_rango_y_lo_declara(entorno):
    cliente, _, _ = entorno
    _agregar(cliente)
    repo = RepositorioInflux(cliente, "zenit_raw", cache_s=0)

    corta = repo.serie(ACTIVO, "system_cpu_utilization", INICIO, INICIO + timedelta(hours=1))
    assert corta.resolucion == Resolucion.CRUDO and corta.intervalo_s == 10

    tres_dias = repo.serie(ACTIVO, "system_cpu_utilization", FIN - timedelta(days=3), FIN, agrupar="cpu")
    assert tres_dias.resolucion == Resolucion.MINUTO and tres_dias.intervalo_s == 300
    (cpu0, _cpu1) = tres_dias.series
    assert len(cpu0.puntos) == 36                              # 3 h en pasos de 5 min
    # cada paso de 5 min tiene 3 minutos a 0.6 o 2, segun donde empiece; el
    # total coincide con el promedio crudo de las tres horas
    crudo = (540 * 0.2 + 538 * 0.6) / 1078
    ponderado = sum(v for _, v in cpu0.puntos) / len(cpu0.puntos)
    assert ponderado == pytest.approx(crudo, abs=0.01)
    assert tres_dias.intervalos_incompletos == 1              # el paso que contiene el minuto 7

    cuarenta_dias = repo.serie(ACTIVO, "system_cpu_utilization", FIN - timedelta(days=40), FIN)
    assert cuarenta_dias.resolucion == Resolucion.HORA and cuarenta_dias.intervalo_s == 3600
    assert len(cuarenta_dias.series[0].puntos) == 3


@con_pg
def test_rango_largo_se_parte_en_tramos_que_no_tocan_demasiados_archivos(entorno, monkeypatch):
    from zenit.repositorio import influx as modulo

    cliente, _, _ = entorno
    _agregar(cliente)
    consultas = []
    original = cliente.consultar

    def contar(bd, sql, params=None):
        if bd == "zenit_1m" and "date_bin" in sql:
            consultas.append(sql)
        return original(bd, sql, params)

    monkeypatch.setattr(cliente, "consultar", contar)
    repo = RepositorioInflux(cliente, "zenit_raw", cache_s=0)
    r = repo.serie(ACTIVO, "system_cpu_utilization", FIN - timedelta(days=30), FIN)
    assert len(consultas) == 30                                 # 30 dias en tramos de 1
    assert r.resolucion == Resolucion.MINUTO and r.intervalo_s == 1800
    assert modulo.TRAMO["1m"] == timedelta(days=1)


@con_pg
def test_api_de_series_declara_la_resolucion(entorno):
    
    from zenit.api.main import crear_app

    cliente, _, _ = entorno
    _agregar(cliente)
    api = con_sesion(crear_app(RepositorioInflux(cliente, "zenit_raw", cache_s=0), "/no/existe.json", **opciones()), "administrador")
    q = {"desde": (FIN - timedelta(days=7)).isoformat(), "hasta": FIN.isoformat()}
    r = api.get(f"/api/activos/{ACTIVO}/series/system_cpu_utilization", params=q).json()
    assert (r["resolucion"], r["intervalo_s"]) == ("1m", 600)
    d = api.get(f"/api/activos/{ACTIVO}/metricas/system_cpu_utilization/dimensiones", params=q).json()
    assert d["dimensiones"]["cpu"] == ["cpu0", "cpu1"]


def test_niveles_y_pasos():
    t = datetime(2026, 10, 9, tzinfo=timezone.utc)
    nivel, paso = RepositorioInflux.nivel_para, RepositorioInflux.paso_para
    assert nivel(t - timedelta(hours=24), t) == Resolucion.CRUDO
    assert nivel(t - timedelta(hours=24, seconds=1), t) == Resolucion.MINUTO
    assert nivel(t - timedelta(days=30), t) == Resolucion.MINUTO
    assert nivel(t - timedelta(days=31), t) == Resolucion.HORA
    assert paso(Resolucion.MINUTO, t - timedelta(days=2), t) == 120
    assert paso(Resolucion.HORA, t - timedelta(days=180), t) == 10800


def test_retencion_verificable_detecta_filas_fuera_de_la_ventana(capsys):
    from zenit import admin

    almacen, url, srv = falso_influx.levantar()
    try:
        for bd in ("zenit_raw", "zenit_1m", "zenit_1h", "zenit_interno"):
            almacen.bases[bd] = {"retencion": "x", "tablas": {}}
        cliente = ClienteInflux(url, "apiv3_prueba")
        ahora = datetime.now(timezone.utc)
        dentro = construir_linea("system_cpu_utilization", {**CANON, "cpu": "cpu0"}, 0.1,
                                 ns(ahora - timedelta(days=30) + timedelta(minutes=90)))
        cliente.escribir("zenit_raw", dentro)
        assert admin.retencion(cliente) == 0
        assert "ninguna fila (OK)" in capsys.readouterr().out
        # una fila que el motor ya deberia estar ocultando
        cliente.escribir("zenit_raw", construir_linea("system_cpu_utilization", {**CANON, "cpu": "cpu0"}, 0.1,
                                                      ns(ahora - timedelta(days=30, minutes=90))))
        assert admin.retencion(cliente) == 1
        assert "FALLA" in capsys.readouterr().out
        # el doble no tiene system.parquet_files: el comando lo dice y sugiere el respaldo
        assert admin.espacio(cliente) == 2
        assert "espacio.sh" in capsys.readouterr().err
    finally:
        srv.shutdown()


def _copia_con_otro_activo(cliente, activo="nuevo"):
    lineas, t = [], INICIO
    while t < FIN:
        lineas.append(construir_linea("system_cpu_utilization",
                                      {**CANON, "zenit_asset_id": activo, "cpu": "cpu0", "state": "user"}, 0.5, ns(t)))
        t += timedelta(seconds=10)
    cliente.escribir("zenit_raw", "\n".join(lineas))


def _de(almacen, bd, activo="nuevo"):
    return [f for f in almacen.bases[bd]["tablas"]["system_cpu_utilization"] if f["zenit_asset_id"] == activo]


@con_pg
def test_un_retroceso_largo_cortado_por_el_presupuesto_se_completa(entorno):
    """Hallazgo de la revision: el pedido de rehacer se perdia al cortar."""
    from zenit import catalogo

    cliente, almacen, pg = entorno
    _agregar(cliente)
    _copia_con_otro_activo(cliente)                 # lo que hace la asociacion de una huerfana
    catalogo.retroceder_agregacion(pg, INICIO)
    for _ in range(6):
        _agregar(cliente, presupuesto=0)            # un tramo por ciclo
    assert len(_de(almacen, "zenit_1m")) == 180
    assert len(_de(almacen, "zenit_1h")) == 3


@con_pg
def test_un_retroceso_a_mitad_de_ciclo_no_deja_la_hora_sin_rehacer(entorno, monkeypatch):
    """Hallazgo de la revision: la hora se rehacia antes que el minuto."""
    import zenit.agregacion as ag
    from zenit import catalogo

    cliente, almacen, pg = entorno
    _agregar(cliente)
    original, hecho = ag.resumir, []

    def resumir(cli, origen, destino, paso, a, b):
        n = original(cli, origen, destino, paso, a, b)
        if destino == "zenit_1m" and not hecho:
            hecho.append(1)
            _copia_con_otro_activo(cli)
            catalogo.retroceder_agregacion(pg, INICIO)   # la asociacion termina en medio del ciclo
        return n

    monkeypatch.setattr(ag, "resumir", resumir)
    for _ in range(3):
        _agregar(cliente)
    assert len(_de(almacen, "zenit_1m")) == 180
    assert len(_de(almacen, "zenit_1h")) == 3


@con_pg
def test_la_hora_se_escribe_una_sola_vez(entorno, monkeypatch):
    """Core no compacta: cada reescritura de una hora es un archivo mas."""
    import zenit.agregacion as ag

    cliente, _, _ = entorno
    escrituras = []
    original = ag.resumir

    def resumir(cli, origen, destino, paso, a, b):
        if destino == "zenit_1h":
            escrituras.append((a, b))
        return original(cli, origen, destino, paso, a, b)

    monkeypatch.setattr(ag, "resumir", resumir)
    for k in range(4):
        _agregar(cliente, ahora=AHORA + timedelta(minutes=k))
    assert sum(1 for a, b in escrituras if a <= INICIO < b) == 1     # cuatro ciclos, una escritura
