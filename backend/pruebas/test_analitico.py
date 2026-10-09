"""H-016 y H-017: registros y trazas en ClickHouse, de OTLP a la API.

Contra un ClickHouse real (el mismo 24.8 de la imagen):

    clickhouse server -- --http_port=18123 --tcp_port=19000 --path=/tmp/chdatos/
    ZENIT_CH_PRUEBAS=http://127.0.0.1:18123 python -m pytest pruebas/test_analitico.py

Sin esa variable, estas pruebas se saltan.
"""
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import grpc
import pytest
from fastapi.testclient import TestClient
from opentelemetry.proto.collector.logs.v1 import logs_service_pb2, logs_service_pb2_grpc
from opentelemetry.proto.collector.trace.v1 import trace_service_pb2, trace_service_pb2_grpc
from opentelemetry.proto.common.v1 import common_pb2
from opentelemetry.proto.logs.v1 import logs_pb2
from opentelemetry.proto.trace.v1 import trace_pb2

import otlp
from destinos import DestinoPorLotes
from servidor import Catalogo, PuntoDeEntrada, servidor_grpc
from zenit.api.main import crear_app
from zenit.clickhouse import ClienteClickHouse
from zenit.repositorio.analitico import RepositorioAnalitico

CH = os.environ.get("ZENIT_CH_PRUEBAS")
pytestmark = pytest.mark.skipif(not CH, reason="sin ZENIT_CH_PRUEBAS")
ESQUEMA = Path(__file__).resolve().parents[2] / "deploy" / "clickhouse" / "01-esquema.sql"
TOKEN = "t"
TRAZA = "0af7651916cd43dd8448eb211c80319c"


@pytest.fixture
def ch():
    cliente = ClienteClickHouse(CH, os.environ.get("ZENIT_CH_USUARIO", "default"), "")
    cliente.ejecutar("DROP DATABASE IF EXISTS zenit SYNC")
    cliente.aplicar_esquema(ESQUEMA.read_text())
    yield cliente
    cliente.cerrar()


def _ingesta(tmp_path, ch, capacidad=100_000):
    ruta = tmp_path / "catalogo.json"
    ruta.write_text(json.dumps({"zenit-nodo-do": "activo-do-nodo-01"}))
    nucleo = PuntoDeEntrada(Catalogo(ruta, cada_s=0))
    c = nucleo.contadores
    from destinos import DestinoJsonl

    for senal in ("registros", "trazas"):
        nucleo.destinos_analiticos[senal] = DestinoPorLotes(
            ch, senal, al_escribir=lambda n, s=senal: c.sumar(s, "emitidas", n),
            al_rechazar=lambda n, s=senal: c.sumar(s, "rechazadas_por_almacen", n),
            capacidad_lineas=capacidad, espera_lote_s=0.05, carpeta_rechazos=tmp_path / "rechazadas",
            prefijo_rechazos=senal, extension_rechazos=".jsonl")
    nucleo.verdad = DestinoJsonl(tmp_path / "salida", "registros", al_escribir=lambda n: None)
    srv = servidor_grpc(nucleo, TOKEN, 0)
    puerto = srv.add_insecure_port("127.0.0.1:0")
    srv.start()
    canal = grpc.insecure_channel(f"127.0.0.1:{puerto}")
    return nucleo, srv, logs_service_pb2_grpc.LogsServiceStub(canal), trace_service_pb2_grpc.TraceServiceStub(canal)


def _kv(k, v):
    return common_pb2.KeyValue(key=k, value=common_pb2.AnyValue(string_value=v))


def _registro(texto, t_ns, sev=9, sevtxt="INFO", traza=b"", span=b"", **attrs):
    return logs_pb2.LogRecord(time_unix_nano=t_ns, severity_number=sev, severity_text=sevtxt,
                              body=common_pb2.AnyValue(string_value=texto), trace_id=traza, span_id=span,
                              attributes=[_kv(k, v) for k, v in attrs.items()])


def _pedir_registros(registros, nodo="zenit-nodo-do", servicio="pedidos"):
    res = otlp.recurso(nodo, **({"service.name": servicio} if servicio else {}))
    return logs_service_pb2.ExportLogsServiceRequest(resource_logs=[logs_pb2.ResourceLogs(
        resource=res, scope_logs=[logs_pb2.ScopeLogs(log_records=registros)])])


def _span(nombre, inicio, dur_ms, span_id, padre=b"", error=False):
    return trace_pb2.Span(trace_id=bytes.fromhex(TRAZA), span_id=span_id, parent_span_id=padre, name=nombre,
                          kind=trace_pb2.Span.SPAN_KIND_SERVER, start_time_unix_nano=inicio,
                          end_time_unix_nano=inicio + dur_ms * 1_000_000,
                          status=trace_pb2.Status(code=2 if error else 1))


def _pedir_trazas(spans, nodo="zenit-nodo-do", servicio="pedidos"):
    return trace_service_pb2.ExportTraceServiceRequest(resource_spans=[trace_pb2.ResourceSpans(
        resource=otlp.recurso(nodo, **{"service.name": servicio}), scope_spans=[trace_pb2.ScopeSpans(spans=spans)])])


def _reposo(nucleo, senal, segundos=10):
    fin = time.time() + segundos
    while time.time() < fin:
        v = nucleo.contadores.instantanea()["senales"][senal]
        if v["en_cola"] == 0:
            return v
        time.sleep(0.05)
    raise AssertionError(nucleo.contadores.instantanea())


META = [("authorization", f"Bearer {TOKEN}")]


def test_registros_y_trazas_de_otlp_a_clickhouse_con_etiquetas_canonicas(tmp_path, ch):
    nucleo, srv, logs, trazas = _ingesta(tmp_path, ch)
    ahora = time.time_ns()
    span = bytes.fromhex("b7ad6b7169203331")
    try:
        logs.Export(_pedir_registros([
            _registro("pedido creado", ahora, traza=bytes.fromhex(TRAZA), span=span),
            _registro("catalogo no respondio", ahora + 1000, sev=17, sevtxt="ERROR"),
            _registro('{"evento":"inicio","uid":"x"}', ahora, sev=0, sevtxt="", **{"log.file.name": "verdad.jsonl"}),
        ]), metadata=META, timeout=5)
        trazas.Export(_pedir_trazas([_span("POST /pedidos", ahora, 120, span),
                                     _span("GET /productos", ahora + 10**6, 80, bytes.fromhex("00f067aa0ba902b7"),
                                           padre=span, error=True)]), metadata=META, timeout=5)
        r = _reposo(nucleo, "registros")
        t = _reposo(nucleo, "trazas")
    finally:
        srv.stop(0)
    assert (r["recibidas"], r["emitidas"]) == (3, 3) and (t["recibidas"], t["emitidas"]) == (2, 2)
    filas = ch.consultar("SELECT zenit_asset_id, service_name, cloud_provider, severidad_num, traza_id, "
                         "atributos FROM registros ORDER BY severidad_num")
    assert {f["zenit_asset_id"] for f in filas} == {"activo-do-nodo-01"}
    assert {f["service_name"] for f in filas} == {"pedidos"} and filas[0]["cloud_provider"] == "aws"
    assert [f["severidad_num"] for f in filas] == [0, 9, 17]
    assert filas[1]["traza_id"] == TRAZA
    # la verdad de referencia, ademas, queda en el archivo que lee el cargador
    (archivo,) = (tmp_path / "salida").glob("registros-*.jsonl")
    (linea,) = archivo.read_text().splitlines()
    assert json.loads(json.loads(linea)["cuerpo"])["uid"] == "x"
    spans = ch.consultar("SELECT nombre, tipo, estado, duracion_ns, padre_id FROM trazas ORDER BY inicio")
    assert [(s["nombre"], s["estado"], s["duracion_ns"]) for s in spans] == [
        ("POST /pedidos", "ok", 120_000_000), ("GET /productos", "error", 80_000_000)]
    assert spans[0]["tipo"] == "servidor" and spans[1]["padre_id"] == "b7ad6b7169203331"


def test_quinientos_eventos_por_segundo_sin_perdida(tmp_path, ch):
    """H-016, criterio 3, en pequeno: 5000 registros en 10 s por gRPC, todos
    almacenados. La medicion sostenida va con scripts/carga/registros.py."""
    nucleo, srv, logs, _ = _ingesta(tmp_path, ch)
    base = time.time_ns()
    try:
        for s in range(10):
            inicio = time.monotonic()
            logs.Export(_pedir_registros([_registro(f"evento {s}-{i}", base + (s * 500 + i) * 1000)
                                          for i in range(500)]), metadata=META, timeout=10)
            time.sleep(max(0.0, 1.0 - (time.monotonic() - inicio)))
        r = _reposo(nucleo, "registros", 20)
    finally:
        srv.stop(0)
    (f,) = ch.consultar("SELECT count() AS n FROM registros")
    assert r["recibidas"] == r["emitidas"] == int(f["n"]) == 5000


def test_clickhouse_caido_aplica_contrapresion_y_no_pierde(tmp_path, ch):
    caido = ClienteClickHouse("http://127.0.0.1:1", "default", "")
    nucleo, srv, logs, _ = _ingesta(tmp_path, caido, capacidad=3)
    try:
        logs.Export(_pedir_registros([_registro("uno", time.time_ns())] * 3), metadata=META, timeout=5)
        with pytest.raises(grpc.RpcError) as e:
            logs.Export(_pedir_registros([_registro("dos", time.time_ns())]), metadata=META, timeout=5)
        assert e.value.code() == grpc.StatusCode.UNAVAILABLE
        v = nucleo.contadores.instantanea()["senales"]["registros"]
        assert (v["recibidas"], v["en_cola"], v["emitidas"]) == (3, 3, 0)    # nada contado como perdido
    finally:
        srv.stop(0)
        for d in nucleo.destinos_analiticos.values():
            d.detener(0)


def test_un_lote_invalido_va_a_rechazos_y_no_bloquea(tmp_path, ch):
    destino = DestinoPorLotes(ch, "registros", al_escribir=lambda n: None, al_rechazar=lambda n: None,
                              espera_lote_s=0.05, carpeta_rechazos=tmp_path, prefijo_rechazos="registros",
                              extension_rechazos=".jsonl")
    from destinos import Lote

    destino.aceptar(Lote(['{"momento":"no es una fecha","zenit_asset_id":"a"}'], 1))
    destino.aceptar(Lote([json.dumps({"momento": "2026-10-09 12:00:00.000000001", "zenit_asset_id": "a",
                                      "cuerpo": "bien"})], 1))
    time.sleep(1.5)
    destino.detener(2)
    assert [f["cuerpo"] for f in ch.consultar("SELECT cuerpo FROM registros")] == ["bien"]
    assert "no es una fecha" in (tmp_path / f"registros-{time.strftime('%Y%m%d')}.jsonl").read_text()


def _sembrar(ch, activo="activo-do-nodo-01", host="zenit-nodo-do"):
    ahora = datetime.now(timezone.utc).replace(microsecond=0)
    m = lambda d: (ahora - timedelta(minutes=d)).strftime("%Y-%m-%d %H:%M:%S") + ".000000001"
    canon = {"zenit_asset_id": activo, "host_name": host, "service_name": "pedidos", "cloud_provider": "do",
             "cloud_region": "nyc3", "deployment_environment": "lab"}
    ch.escribir("registros", "\n".join(json.dumps(x) for x in [
        {**canon, "momento": m(5), "severidad": "INFO", "severidad_num": 9, "cuerpo": "pedido 1",
         "traza_id": TRAZA, "span_id": "b7ad6b7169203331"},
        {**canon, "momento": m(4), "severidad": "ERROR", "severidad_num": 17, "cuerpo": "Catalogo no respondio"},
        {**canon, "momento": m(90), "severidad": "INFO", "severidad_num": 9, "cuerpo": "viejo"},
    ]))
    ch.escribir("trazas", "\n".join(json.dumps(x) for x in [
        {**canon, "inicio": m(5), "fin": m(5), "duracion_ns": 120_000_000, "traza_id": TRAZA,
         "span_id": "b7ad6b7169203331", "padre_id": "", "nombre": "POST /pedidos", "tipo": "servidor", "estado": "ok"},
        {**canon, "service_name": "catalogo", "inicio": m(5), "fin": m(5), "duracion_ns": 80_000_000,
         "traza_id": TRAZA, "span_id": "00f067aa0ba902b7", "padre_id": "b7ad6b7169203331",
         "nombre": "GET /productos", "tipo": "servidor", "estado": "error"},
    ]))
    return ahora


def test_repositorio_por_activo_y_ventana_y_correlacion(ch):
    ahora = _sembrar(ch)
    repo = RepositorioAnalitico(ch)
    hora = (ahora - timedelta(hours=1), ahora)
    regs = repo.registros("activo-do-nodo-01", *hora)
    assert [r["cuerpo"] for r in regs] == ["Catalogo no respondio", "pedido 1"]    # sin "viejo", mas reciente primero
    assert regs[0]["momento"].endswith("+00:00")
    assert [r["cuerpo"] for r in repo.registros("activo-do-nodo-01", *hora, severidad_minima=17)] == [
        "Catalogo no respondio"]
    assert [r["cuerpo"] for r in repo.registros("activo-do-nodo-01", *hora, texto="catalogo")] == [
        "Catalogo no respondio"]
    assert repo.registros("otro-activo", *hora) == []
    (t,) = repo.trazas("activo-do-nodo-01", *hora)
    assert (t["traza_id"], t["spans"], t["errores"], t["raiz"], t["servicios"]) == (
        TRAZA, 2, 1, "POST /pedidos", ["catalogo", "pedidos"])
    assert [s["nombre"] for s in repo.traza(TRAZA)] == ["POST /pedidos", "GET /productos"]
    corr = repo.correlacion("activo-do-nodo-01", *hora)
    con_span = [c for c in corr if c["operacion"]]
    assert len(con_span) == 1 and con_span[0]["operacion"] == "POST /pedidos" and con_span[0]["duracion_ns"] == 120_000_000
    assert repo.resumen("activo-do-nodo-01", *hora) == {"registros": 2, "registros_error": 1, "spans": 2,
                                                        "spans_error": 1}


def test_parametros_y_validaciones(ch):
    from zenit.repositorio import ErrorConsulta

    _sembrar(ch)
    repo = RepositorioAnalitico(ch)
    ahora = datetime.now(timezone.utc)
    # un valor malicioso viaja como parametro y no rompe ni filtra nada
    assert repo.registros("x' OR 1=1 --", ahora - timedelta(hours=1), ahora) == []
    with pytest.raises(ErrorConsulta):
        repo.registros("a", ahora - timedelta(days=8), ahora)
    with pytest.raises(ErrorConsulta):
        repo.traza("no-es-hex")


def test_reasignar_huerfanas_no_duplica_aunque_se_repita(ch):
    _sembrar(ch, activo="sin_resolver", host="zenit-nodo-x")
    repo = RepositorioAnalitico(ch)
    # un intento anterior que se corto despues de copiar los registros
    ch.ejecutar("INSERT INTO registros SELECT * REPLACE ('activo-x' AS zenit_asset_id, toUInt8(1) AS reasignado) "
                "FROM registros WHERE zenit_asset_id = 'sin_resolver'")
    assert repo.reasignar_sin_resolver("zenit-nodo-x", "activo-x") == 5
    assert repo.reasignar_sin_resolver("zenit-nodo-x", "activo-x") == 0
    (f,) = ch.consultar("SELECT countIf(zenit_asset_id = 'activo-x') AS movidas, "
                        "countIf(zenit_asset_id = 'sin_resolver') AS quedan FROM registros")
    assert (int(f["movidas"]), int(f["quedan"])) == (3, 0)
    (f,) = ch.consultar("SELECT count() AS n FROM trazas WHERE zenit_asset_id = 'activo-x'")
    assert int(f["n"]) == 2


def test_api_de_registros_trazas_y_correlacion(ch):
    ahora = _sembrar(ch)

    class RepoQuieto:
        def disponible(self):
            return True

    api = TestClient(crear_app(RepoQuieto(), "/no/existe.json", analitico=RepositorioAnalitico(ch)))
    q = {"desde": (ahora - timedelta(hours=1)).isoformat(), "hasta": ahora.isoformat()}
    assert len(api.get("/api/activos/activo-do-nodo-01/registros", params=q).json()["registros"]) == 2
    assert api.get("/api/activos/activo-do-nodo-01/trazas", params={**q, "solo_errores": True}).json()["trazas"][0][
        "errores"] == 1
    assert len(api.get(f"/api/trazas/{TRAZA}").json()["spans"]) == 2
    assert api.get("/api/trazas/" + "0" * 32).status_code == 404
    c = api.get("/api/activos/activo-do-nodo-01/correlacion", params=q).json()
    assert c["resumen"]["spans_error"] == 1 and len(c["registros"]) == 2
    assert api.get("/api/salud").json()["analitico"] is True
    sin = TestClient(crear_app(RepoQuieto(), "/no/existe.json"))
    assert sin.get("/api/activos/a/registros").status_code == 503


def test_reasignar_no_borra_lo_que_llego_despues_de_copiar(ch, monkeypatch):
    """Hallazgo de la revision: el DELETE borraba filas que nunca se copiaron."""
    _sembrar(ch, activo="sin_resolver", host="zenit-nodo-x")
    repo = RepositorioAnalitico(ch)
    original = ch.ejecutar

    def ejecutar(sql, parametros=None):
        original(sql, parametros)
        if sql.startswith("INSERT INTO registros"):       # la ingesta escribe una huerfana mas en ese instante
            ch.escribir("registros", json.dumps({
                "momento": "2026-10-09 12:00:00.000000007", "zenit_asset_id": "sin_resolver",
                "host_name": "zenit-nodo-x", "cuerpo": "tardia"}))

    monkeypatch.setattr(ch, "ejecutar", ejecutar)
    repo.reasignar_sin_resolver("zenit-nodo-x", "activo-x")
    monkeypatch.setattr(ch, "ejecutar", original)
    quedan = ch.consultar("SELECT cuerpo FROM registros WHERE zenit_asset_id = 'sin_resolver'")
    assert [f["cuerpo"] for f in quedan] == ["tardia"]
    assert repo.reasignar_sin_resolver("zenit-nodo-x", "activo-x") == 1
