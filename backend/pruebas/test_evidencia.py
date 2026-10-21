"""H-039: evidencia de las tres senales de un incidente."""
import os
from datetime import datetime, timedelta, timezone

import pytest
from psycopg.types.json import Jsonb

from conftest import preparar_pg
from sesion_prueba import con_sesion, opciones
from zenit.api.main import crear_app
from zenit.clickhouse import ErrorClickHouse
from zenit.diagnostico import evidencia, persistencia
from zenit.influx import ErrorInflux
from zenit.repositorio.contrato import Resolucion, Respuesta, Serie

PG = os.environ.get("ZENIT_PG_PRUEBAS")
con_pg = pytest.mark.skipif(not PG, reason="sin ZENIT_PG_PRUEBAS")

INICIO = datetime(2026, 10, 21, 13, 20, tzinfo=timezone.utc)
FIN = datetime(2026, 10, 21, 13, 46, tzinfo=timezone.utc)
AHORA = datetime(2026, 10, 21, 15, 0, tzinfo=timezone.utc)
ACTIVO = "activo-azure-zenit-nodo-azure"


def _ms(t):
    return int(t.timestamp() * 1000)


def _escalon(desde, hasta, normal, alto, inicio=INICIO, fin=FIN, paso=60):
    puntos, t = [], desde
    while t < hasta:
        puntos.append([_ms(t), alto if inicio <= t <= fin else normal])
        t += timedelta(seconds=paso)
    return puntos


def _contador(desde, hasta, ritmo_normal, ritmo_alto, paso=60):
    """Un contador acumulado que crece mas rapido durante la anomalia."""
    puntos, t, v = [], desde, 1000.0
    while t < hasta:
        puntos.append([_ms(t), v])
        v += (ritmo_alto if INICIO <= t <= FIN else ritmo_normal) * paso
        t += timedelta(seconds=paso)
    return puntos


class FalsoRepositorio:
    def __init__(self, falla=False):
        self.consultas = []
        self.falla = falla

    def serie(self, activo, metrica, desde, hasta, *, agrupar=None, filtros=None):
        self.consultas.append({"metrica": metrica, "desde": desde, "hasta": hasta,
                               "agrupar": agrupar, "filtros": dict(filtros or {})})
        if self.falla:
            raise ErrorInflux("influx no responde")
        if metrica == "system_filesystem_usage":
            series = [Serie({}, _escalon(desde, hasta, 7.6e9, 10.8e9))]
        elif metrica == "system_disk_io":
            series = [Serie({"device": "sda"}, _contador(desde, hasta, 1000, 900000)),
                      Serie({"device": "sda1"}, _contador(desde, hasta, 1000, 900000))]
        else:
            series = [Serie({}, _escalon(desde, hasta, 0.01, 0.02))]
        return Respuesta(activo=activo, metrica=metrica, resolucion=Resolucion.MINUTO, desde=desde,
                         hasta=hasta, agrupar=agrupar, series=series, intervalo_s=60)

    def disponible(self):
        return True


class FalsoAnalitico:
    def __init__(self, falla=False):
        self.llamadas = {}
        self.falla = falla

    def _anotar(self, nombre, **kw):
        if self.falla:
            raise ErrorClickHouse("clickhouse no responde")
        self.llamadas[nombre] = kw

    def registros(self, activo, desde, hasta, **kw):
        self._anotar("registros", desde=desde, hasta=hasta, **kw)
        return [{"momento": "2026-10-21T13:21:00+00:00", "severidad": "ERROR", "cuerpo": "disco lleno"}]

    def trazas(self, activo, desde, hasta, **kw):
        self._anotar("trazas", **kw)
        return [{"traza_id": "a" * 32, "errores": 1, "raiz": "POST /pedidos"}]

    def correlacion(self, activo, desde, hasta, limite=200):
        self._anotar("correlacion", limite=limite)
        return [{"momento": "x", "severidad": "INFO", "estado_span": "ok", "cuerpo": "bien"},
                {"momento": "y", "severidad": "INFO", "estado_span": "error", "cuerpo": "fallo el span"},
                {"momento": "z", "severidad": "ERROR", "estado_span": None, "cuerpo": "registro de error"}]

    def resumen(self, activo, desde, hasta):
        self._anotar("resumen")
        return {"registros": 10, "registros_error": 1, "spans": 100, "spans_error": 1}

    def disponible(self):
        return True


ANOMALIA = {"id": 29, "activo": ACTIVO, "metrica": "system_filesystem_usage",
            "dimensiones": {"state": "used", "mountpoint": "/"}, "inicio": INICIO, "fin": FIN}


# ------------------------------------------------------------------ relacionadas
def test_relacionadas_de_disco_traen_escritura_y_espera_de_cpu():
    nombres = evidencia.metricas_relacionadas("system_filesystem_usage", {"state": "used", "mountpoint": "/"})
    assert "system_disk_io" in nombres and "system_cpu_utilization" in nombres
    assert "system_filesystem_usage" not in nombres
    assert "system_memory_utilization" not in nombres


def test_relacionadas_de_cpu_no_incluyen_la_misma_serie():
    rel = evidencia.senales_relacionadas("system_cpu_utilization", {"state": "user"})
    claves = {s.clave for s in rel}
    assert "system_cpu_utilization[state=user]" not in claves
    assert "system_cpu_utilization[state=system]" in claves
    assert "system_cpu_load_average_1m" in claves


def test_una_metrica_sin_tipo_de_fallo_no_tiene_relacionadas():
    assert evidencia.metricas_relacionadas("system_network_io", {"direction": "receive", "device": "eth0"}) == []
    assert evidencia.metricas_relacionadas("metrica_que_no_vigila_nadie", {}) == []


# ------------------------------------------------------------------ ventanas
def test_ventanas_de_una_anomalia_cerrada():
    v = evidencia.ventanas(INICIO, FIN, AHORA)
    assert v["metricas"] == (INICIO - timedelta(minutes=60), FIN + timedelta(minutes=10))
    assert v["registros"][0] == INICIO - timedelta(minutes=10)
    assert v["durante"] == (INICIO, FIN)


def test_una_anomalia_abierta_llega_hasta_ahora_y_no_mas_de_tres_horas():
    ahora = INICIO + timedelta(minutes=5)
    assert evidencia.ventanas(INICIO, None, ahora)["metricas"][1] == ahora
    lejos = INICIO + timedelta(days=1)
    assert evidencia.ventanas(INICIO, None, lejos)["durante"][1] == INICIO + timedelta(hours=3)


# ------------------------------------------------------------------ recuperar
def test_recupera_las_tres_senales():
    repo, ch = FalsoRepositorio(), FalsoAnalitico()
    ev = evidencia.recuperar(ANOMALIA, repo, ch, AHORA)

    afectada = ev["metricas"][0]
    assert afectada["papel"] == "afectada"
    assert afectada["dimensiones"] == {"state": "used", "mountpoint": "/"}
    # la serie afectada se pide con sus dimensiones exactas, sin promediar
    assert repo.consultas[0]["filtros"] == {"state": "used", "mountpoint": "/"}
    assert afectada["resumen"]["antes_mediana"] == pytest.approx(7.6e9)
    assert afectada["resumen"]["durante_max"] == pytest.approx(10.8e9)
    assert afectada["resumen"]["cambio_relativo"] == pytest.approx(10.8 / 7.6 - 1)
    assert afectada["puntos"] and len(afectada["puntos"]) <= evidencia.PUNTOS_MAXIMOS

    relacionadas = [m for m in ev["metricas"] if m["papel"] == "relacionada"]
    assert {m["metrica"] for m in relacionadas} >= {"system_disk_io", "system_cpu_utilization"}
    assert all("puntos" not in m for m in relacionadas)

    # registros de error y trazas con error, de la consulta al motor
    assert ch.llamadas["registros"]["severidad_minima"] == 17
    assert ch.llamadas["trazas"]["solo_errores"] is True
    assert [f["cuerpo"] for f in ev["correlacion"]] == ["fallo el span", "registro de error"]
    assert ev["conteos"]["spans_error"] == 1
    assert ev["faltantes"] == []
    assert ev["metricas_relacionadas"] == evidencia.metricas_relacionadas(ANOMALIA["metrica"], ANOMALIA["dimensiones"])


def test_las_acumuladas_se_convierten_a_tasa():
    ev = evidencia.recuperar(ANOMALIA, FalsoRepositorio(), FalsoAnalitico(), AHORA)
    escritura = [m for m in ev["metricas"] if m["metrica"] == "system_disk_io"
                 and m["dimensiones"].get("direction") == "write"]
    assert escritura and escritura[0]["unidad"] == "por segundo"
    r = escritura[0]["resumen"]
    assert r["antes_mediana"] == pytest.approx(1000)
    assert r["durante_max"] == pytest.approx(900000)


def test_sin_analitico_la_evidencia_de_metricas_sale_igual():
    ev = evidencia.recuperar(ANOMALIA, FalsoRepositorio(), None, AHORA)
    assert ev["metricas"] and ev["registros_error"] == []
    assert any("no esta configurado" in f for f in ev["faltantes"])


def test_si_clickhouse_falla_se_anota_y_sigue():
    ev = evidencia.recuperar(ANOMALIA, FalsoRepositorio(), FalsoAnalitico(falla=True), AHORA)
    assert ev["metricas"] and ev["trazas_error"] == []
    assert any("registros y trazas" in f for f in ev["faltantes"])


def test_si_influx_falla_se_anota_y_sigue():
    ev = evidencia.recuperar(ANOMALIA, FalsoRepositorio(falla=True), FalsoAnalitico(), AHORA)
    assert ev["metricas"] == [] and ev["registros_error"]
    assert any(f.startswith("serie afectada") for f in ev["faltantes"])


# ------------------------------------------------------------------ API y persistencia
def _app(pg_url=None, analitico=None):
    return crear_app(FalsoRepositorio(), "/no/existe.json", pg_url=pg_url, analitico=analitico, **opciones())


def test_finanzas_no_ve_la_evidencia():
    assert con_sesion(_app(), "finanzas").get("/api/incidentes/1/evidencia").status_code == 403


def test_la_ruta_aparece_en_la_especificacion():
    rutas = con_sesion(_app(), "operador").get("/api/openapi.json").json()["paths"]
    assert "/api/incidentes/{id_incidente}/evidencia" in rutas


@pytest.fixture
def pg():
    conexion = preparar_pg(PG)
    yield conexion
    conexion.close()


def _insertar(c, estado="detectada"):
    with c.cursor() as cur:
        cur.execute(
            "INSERT INTO anomalias (activo, metrica, dimensiones, inicio, fin, estado, severidad, puntaje, "
            "evidencia, version_modelo) VALUES (%s, %s, %s, %s, %s, %s, 'alta', 658.7, %s, 'mad-v1') RETURNING id",
            (ACTIVO, "system_filesystem_usage", Jsonb({"state": "used", "mountpoint": "/"}), INICIO, FIN, estado,
             Jsonb({"ventana": 60, "valor_observado": 10.8e9, "valor_esperado": 7.6e9, "metodo": "mad-v1"})))
        (id_,) = cur.fetchone()
    c.commit()
    return id_


@con_pg
def test_api_de_evidencia_y_relacionadas_en_el_detalle(pg):
    id_ = _insertar(pg)
    api = con_sesion(_app(PG, FalsoAnalitico()), "operador")
    r = api.get(f"/api/incidentes/{id_}/evidencia")
    assert r.status_code == 200, r.text
    ev = r.json()
    assert ev["incidente"] == id_ and ev["metricas"][0]["papel"] == "afectada"
    assert ev["registros_error"] and ev["trazas_error"]
    detalle = api.get(f"/api/incidentes/{id_}").json()
    assert "system_disk_io" in detalle["evidencia"]["metricas_relacionadas"]
    assert api.get("/api/incidentes/999999/evidencia").status_code == 404


@con_pg
def test_guardar_diagnostico_con_su_evidencia(pg):
    id_ = _insertar(pg)
    ev = evidencia.recuperar({**ANOMALIA, "id": id_}, FalsoRepositorio(), FalsoAnalitico(), AHORA)
    persistencia.guardar_diagnostico(pg, id_, texto="Hipotesis: se lleno el disco", evidencia_citada=[],
                                     modelo="modelo-fijo", tokens_entrada=1200, tokens_salida=150,
                                     evidencia_recuperada=ev)
    guardada = persistencia.evidencia_guardada(pg, id_)
    assert guardada["incidente"] == id_ and guardada["metricas"][0]["papel"] == "afectada"
    detalle = con_sesion(_app(PG), "operador").get(f"/api/incidentes/{id_}").json()
    assert detalle["estado"] == "diagnosticada" and detalle["diagnostico"]["modelo"] == "modelo-fijo"


@con_pg
def test_un_diagnostico_no_cambia_una_anomalia_ya_descartada(pg):
    id_ = _insertar(pg, estado="falso_positivo")
    persistencia.guardar_diagnostico(pg, id_, texto="t", evidencia_citada=[], modelo="m", tokens_entrada=1,
                                     tokens_salida=1, evidencia_recuperada={})
    with pg.cursor() as cur:
        cur.execute("SELECT estado FROM anomalias WHERE id = %s", (id_,))
        assert cur.fetchone()[0] == "falso_positivo"
