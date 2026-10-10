"""Capa de repositorio y API, con el SQL ejecutado de verdad por DataFusion."""
import json
from datetime import datetime, timedelta, timezone

import pytest

import falso_influx
from sesion_prueba import con_sesion, opciones
from zenit.api.main import crear_app
from zenit.influx import ClienteInflux
from zenit.protocolo_linea import construir_linea
from zenit.repositorio import ErrorConsulta
from zenit.repositorio.influx import RepositorioInflux

ACTIVO = "activo-aws-nodo-01"
CANON = {
    "zenit_asset_id": ACTIVO, "host_name": "zenit-nodo-aws", "service_name": "host",
    "cloud_provider": "aws", "cloud_region": "us-east-1", "deployment_environment": "lab",
}
FIN = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=2)
# alineado a 10 s para que las cuentas de intervalos sean exactas
FIN = FIN - timedelta(seconds=FIN.second % 10)
INICIO = FIN - timedelta(minutes=10)


def ns(momento: datetime) -> int:
    return int(momento.timestamp()) * 10**9


@pytest.fixture
def entorno(tmp_path):
    almacen, url, srv = falso_influx.levantar()
    almacen.bases["zenit_raw"] = {"retencion": "7d", "tablas": {}}
    cliente = ClienteInflux(url, "apiv3_prueba")

    lineas = []
    # 10 minutos de CPU a 10 s, dos nucleos y dos estados, con un hueco de 1 minuto
    for paso in range(60):
        if 30 <= paso < 36:
            continue
        t = ns(INICIO + timedelta(seconds=10 * paso))
        for cpu in ("cpu0", "cpu1"):
            for estado, base in (("user", 0.20), ("system", 0.05)):
                valor = base + (0.5 if paso >= 45 else 0.0)  # escalon de carga
                lineas.append(construir_linea("system_cpu_utilization",
                                              {**CANON, "cpu": cpu, "state": estado}, valor, t))
        lineas.append(construir_linea("system_network_io",
                                      {**CANON, "device": "eth0", "direction": "receive"}, 1000.0 * paso, t))
    # otro activo, que no debe aparecer en las consultas del primero
    lineas.append(construir_linea("system_cpu_utilization",
                                  {**CANON, "zenit_asset_id": "otro", "cpu": "cpu0", "state": "user"}, 9.9, ns(FIN - timedelta(seconds=30))))
    # una metrica vieja, fuera de la ventana de "metricas recientes"
    lineas.append(construir_linea("system_paging_usage", {**CANON, "state": "used"}, 1.0, ns(FIN - timedelta(hours=3))))
    cliente.escribir("zenit_raw", "\n".join(lineas))

    catalogo = tmp_path / "catalogo.json"
    catalogo.write_text(json.dumps({"zenit-nodo-aws": ACTIVO, "i-0abc": ACTIVO, "zenit-nodo-onprem": "activo-onprem-01"}))
    repo = RepositorioInflux(cliente, "zenit_raw", cache_s=0)
    yield repo, con_sesion(crear_app(repo, catalogo, **opciones())), almacen
    srv.shutdown()


# ------------------------------------------------------------------ repositorio
def test_metricas_recientes_del_activo(entorno):
    repo, _, _ = entorno
    assert repo.metricas_de(ACTIVO) == ["system_cpu_utilization", "system_network_io"]
    assert repo.metricas_de("nadie") == []


def test_dimensiones_propias_sin_las_canonicas(entorno):
    repo, _, _ = entorno
    dims = repo.dimensiones(ACTIVO, "system_cpu_utilization", INICIO, FIN)
    assert dims == {"cpu": ["cpu0", "cpu1"], "state": ["system", "user"]}


def test_serie_agrupada_por_estado(entorno):
    repo, _, _ = entorno
    r = repo.serie(ACTIVO, "system_cpu_utilization", INICIO, FIN, agrupar="state")
    assert [s.etiquetas for s in r.series] == [{"state": "system"}, {"state": "user"}]
    user = r.series[1].puntos
    assert user[0] == [ns(INICIO) // 10**6, pytest.approx(0.20)]
    assert user[-1][1] == pytest.approx(0.70)          # el escalon de carga se ve
    assert r.intervalos_esperados == 60
    assert r.intervalos_vacios == 6                     # el minuto de corte


def test_serie_sin_agrupar_promedia_todo(entorno):
    repo, _, _ = entorno
    r = repo.serie(ACTIVO, "system_cpu_utilization", INICIO, FIN)
    assert len(r.series) == 1 and r.series[0].etiquetas == {}
    assert r.series[0].puntos[0][1] == pytest.approx((0.20 + 0.05) / 2)


def test_filtro_por_dimension(entorno):
    repo, _, _ = entorno
    r = repo.serie(ACTIVO, "system_cpu_utilization", INICIO, FIN, agrupar="cpu", filtros={"state": "system"})
    assert [s.etiquetas["cpu"] for s in r.series] == ["cpu0", "cpu1"]
    assert r.series[0].puntos[0][1] == pytest.approx(0.05)


def test_no_mezcla_activos(entorno):
    repo, _, _ = entorno
    r = repo.serie(ACTIVO, "system_cpu_utilization", INICIO, FIN, agrupar="state")
    assert all(p[1] < 1 for s in r.series for p in s.puntos)


@pytest.mark.parametrize("metrica,agrupar,filtros", [
    ('system_cpu_utilization"; DROP TABLE x; --', None, None),
    ("no_existe", None, None),
    ("system_cpu_utilization", "zenit_asset_id", None),   # canonica, no es dimension
    ("system_cpu_utilization", "device", None),
    ("system_cpu_utilization", None, {"state\" OR 1=1 --": "x"}),
])
def test_consultas_invalidas_se_rechazan_antes_de_llegar_al_motor(entorno, metrica, agrupar, filtros):
    repo, _, _ = entorno
    with pytest.raises(ErrorConsulta):
        repo.serie(ACTIVO, metrica, INICIO, FIN, agrupar=agrupar, filtros=filtros)


def test_valor_de_filtro_malicioso_viaja_como_parametro(entorno):
    repo, _, _ = entorno
    r = repo.serie(ACTIVO, "system_cpu_utilization", INICIO, FIN, filtros={"state": "x' OR '1'='1"})
    assert r.series == []


def test_rango_invalido(entorno):
    repo, _, _ = entorno
    with pytest.raises(ErrorConsulta):
        repo.serie(ACTIVO, "system_cpu_utilization", FIN, INICIO)
    with pytest.raises(ErrorConsulta):
        repo.serie(ACTIVO, "system_cpu_utilization", FIN - timedelta(hours=25), FIN)


# ------------------------------------------------------------------ API
def test_api_activos_desde_el_catalogo(entorno):
    _, api, _ = entorno
    r = api.get("/api/activos").json()
    assert r == [
        {"id": "activo-aws-nodo-01", "claves": ["i-0abc", "zenit-nodo-aws"]},
        {"id": "activo-onprem-01", "claves": ["zenit-nodo-onprem"]},
    ]


def test_api_serie_completa(entorno):
    _, api, _ = entorno
    r = api.get(f"/api/activos/{ACTIVO}/series/system_cpu_utilization",
                params={"desde": INICIO.isoformat(), "hasta": FIN.isoformat(), "agrupar": "state"})
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["resolucion"] == "10s"
    assert [s["etiquetas"]["state"] for s in cuerpo["series"]] == ["system", "user"]
    assert cuerpo["intervalos_vacios"] == 6


def test_api_filtro_repetible(entorno):
    _, api, _ = entorno
    r = api.get(f"/api/activos/{ACTIVO}/series/system_cpu_utilization",
                params=[("desde", INICIO.isoformat()), ("hasta", FIN.isoformat()),
                        ("filtro", "state:user"), ("filtro", "cpu:cpu1")])
    assert r.status_code == 200
    assert r.json()["series"][0]["puntos"][0][1] == pytest.approx(0.20)


def test_api_errores_legibles(entorno):
    _, api, _ = entorno
    r = api.get(f"/api/activos/{ACTIVO}/series/no_existe")
    assert r.status_code == 422 and "no existe" in r.json()["detail"]
    r = api.get(f"/api/activos/{ACTIVO}/series/system_cpu_utilization", params={"filtro": "sin-dos-puntos"})
    assert r.status_code == 422


def test_api_almacen_caido_responde_503(entorno, monkeypatch):
    repo, api, _ = entorno
    from zenit.influx import ErrorInflux

    def caido(*a, **k):
        raise ErrorInflux("sin respuesta")
    monkeypatch.setattr(repo._cliente, "consultar", caido)
    repo.olvidar_esquema()
    r = api.get(f"/api/activos/{ACTIVO}/metricas")
    assert r.status_code == 503


def test_endpoint_en_la_especificacion_openapi(entorno):
    """Criterio 4 de H-006."""
    _, api, _ = entorno
    rutas = api.get("/api/openapi.json").json()["paths"]
    assert "/api/activos/{activo}/series/{metrica}" in rutas


def test_rango_no_alineado_no_esconde_un_hueco(entorno):
    """Hallazgo de la revision: con un rango desalineado, el conteo viejo
    daba 0 vacios aunque faltara un minuto."""
    repo, _, _ = entorno
    r = repo.serie(ACTIVO, "system_cpu_utilization", INICIO + timedelta(seconds=5), FIN + timedelta(seconds=5))
    assert r.intervalos_vacios == 6
    assert r.desde == INICIO and r.hasta == FIN


def test_contar_vacios_ignora_el_desfase_del_agente():
    from zenit.repositorio.influx import contar_vacios
    inicio = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
    fin = inicio + timedelta(minutes=2)
    base = int(inicio.timestamp() * 1000)
    # muestras cada 10 s con desfase: dos caen en un intervalo, el siguiente queda vacio
    alternado = [base + k * 10_000 for k in range(12) if k != 5]
    assert contar_vacios(alternado, inicio, fin) == 0
    # un corte real de un minuto
    corte = [base + k * 10_000 for k in range(12) if not 3 <= k < 9]
    assert contar_vacios(corte, inicio, fin) == 6
    # sin datos: todo vacio
    assert contar_vacios([], inicio, fin) == 12
