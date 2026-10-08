"""Punto de entrada: de OTLP real por gRPC hasta el almacen.

Cada prueba levanta el servidor gRPC de verdad en un puerto libre y le habla
con el cliente OTLP estandar, igual que el Collector del nodo observado.
"""
import json
import math
import os
import time

import grpc
import pytest
from opentelemetry.proto.collector.metrics.v1 import metrics_service_pb2_grpc

import falso_influx
import otlp
from destinos import DestinoInflux, DestinoJsonl
from extraccion import extraer_puntos, resolver_identidad
from servidor import Catalogo, PuntoDeEntrada, servidor_grpc
from zenit.influx import ClienteInflux

TOKEN = "secreto-de-prueba"
T0 = 1_789_741_930_000_000_000  # 2026-09-18T14:32:10Z


# ------------------------------------------------------------------ utilidades
@pytest.fixture
def catalogo(tmp_path):
    ruta = tmp_path / "catalogo.json"
    ruta.write_text(json.dumps({"zenit-nodo-aws": "activo-aws-nodo-01"}))
    return Catalogo(ruta, cada_s=0)


@pytest.fixture
def influx():
    almacen, url, srv = falso_influx.levantar()
    almacen.bases["zenit_raw"] = {"retencion": "7d", "tablas": {}}
    yield almacen, url
    srv.shutdown()


def armar(catalogo, url_influx, **opciones):
    nucleo = PuntoDeEntrada(catalogo, servicio_por_defecto="host")
    c = nucleo.contadores
    nucleo.destino_metricas = DestinoInflux(
        ClienteInflux(url_influx, "apiv3_prueba"), "zenit_raw",
        al_escribir=lambda n: c.sumar("metricas", "emitidas", n),
        al_rechazar=lambda n: c.sumar("metricas", "rechazadas_por_almacen", n),
        espera_lote_s=0.05, **opciones,
    )
    srv = servidor_grpc(nucleo, TOKEN, 0)
    puerto = srv.add_insecure_port("127.0.0.1:0")
    srv.start()
    canal = grpc.insecure_channel(f"127.0.0.1:{puerto}")
    stub = metrics_service_pb2_grpc.MetricsServiceStub(canal)
    return nucleo, srv, stub


def enviar(stub, peticion, token=TOKEN):
    return stub.Export(peticion, metadata=[("authorization", f"Bearer {token}")], timeout=5)


def esperar_reposo(nucleo, segundos=5):
    fin = time.time() + segundos
    while time.time() < fin:
        if nucleo.contadores.instantanea()["senales"]["metricas"]["en_cola"] == 0:
            return nucleo.contadores.instantanea()["senales"]["metricas"]
        time.sleep(0.05)
    raise AssertionError(f"no llego al reposo: {nucleo.contadores.instantanea()}")


def filas(almacen, tabla):
    return almacen.bases["zenit_raw"]["tablas"].get(tabla, [])


# ------------------------------------------------------------------ extraccion pura
def test_gauge_con_dos_puntos_produce_dos_puntos_con_valor():
    m = otlp.gauge("system.cpu.utilization", [
        otlp.punto(0.42, T0, cpu="cpu0", state="user"),
        otlp.punto(0.17, T0, cpu="cpu1", state="system"),
    ])
    puntos = list(extraer_puntos(m, ahora_ns=0))
    assert [p.valores for p in puntos] == [[("", 0.42)], [("", 0.17)]]
    assert puntos[0].tabla == "system_cpu_utilization"
    assert puntos[0].dimensiones == {"cpu": "cpu0", "state": "user"}
    assert puntos[0].tiempo_ns == T0


def test_suma_entera_se_vuelve_flotante():
    m = otlp.suma("system.network.io", [otlp.punto(1234, T0, device="eth0", direction="receive")])
    (p,) = extraer_puntos(m, 0)
    assert p.valores == [("", 1234.0)]
    assert isinstance(p.valores[0][1], float)


def test_histograma_se_descompone_en_conteo_y_suma():
    (p,) = extraer_puntos(otlp.histograma("http.server.duration", 10, 2.5, T0, http_route="/pedidos"), 0)
    assert p.tabla == "http_server_duration"
    assert p.valores == [("_count", 10.0), ("_sum", 2.5)]


def test_punto_sin_valor_registrado_se_marca():
    m = otlp.gauge("x", [otlp.punto(1.0, T0, flags=1)])
    (p,) = extraer_puntos(m, 0)
    assert p.sin_valor and p.valores == []


def test_punto_sin_tiempo_toma_el_de_llegada():
    m = otlp.gauge("x", [otlp.punto(1.0, 0)])
    (p,) = extraer_puntos(m, ahora_ns=42)
    assert p.tiempo_ns == 42


def test_identidad_resuelta_huerfana_y_valores_por_defecto():
    cat = {"zenit-nodo-aws": "activo-aws-nodo-01"}
    ok = resolver_identidad({"host.name": "zenit-nodo-aws", "cloud.provider": "aws"}, cat, "host")
    assert ok.etiquetas["zenit_asset_id"] == "activo-aws-nodo-01"
    assert ok.etiquetas["service_name"] == "host"
    assert ok.etiquetas["cloud_region"] == "desconocido"
    assert not ok.huerfana and ok.clave_nativa == "host.name"

    app = resolver_identidad({"host.name": "zenit-nodo-aws", "service.name": "pedidos"}, cat, "host")
    assert app.etiquetas["service_name"] == "pedidos"

    nada = resolver_identidad({"host.name": "otro"}, cat, "host")
    assert nada.huerfana and nada.etiquetas["zenit_asset_id"] == "sin_resolver"
    assert nada.etiquetas["host_name"] == "otro"


def test_resuelve_por_la_primera_clave_que_existe_en_el_catalogo():
    cat = {"zenit-nodo-aws": "activo-aws-nodo-01"}
    r = resolver_identidad({"host.id": "i-0abc", "host.name": "zenit-nodo-aws"}, cat, "host")
    assert r.etiquetas["zenit_asset_id"] == "activo-aws-nodo-01"
    assert r.clave_nativa == "host.name"


# ------------------------------------------------------------------ catalogo
def test_catalogo_se_relee_sin_reiniciar(tmp_path):
    ruta = tmp_path / "catalogo.json"
    ruta.write_text('{"a": "activo-a"}')
    cat = Catalogo(ruta, cada_s=0)
    assert cat.actual() == {"a": "activo-a"}

    # reemplazo con inodo nuevo, que es lo que hacen sed -i y muchos editores
    nuevo = tmp_path / "nuevo.json"
    nuevo.write_text('{"a": "activo-a", "b": "activo-b"}')
    os.replace(nuevo, ruta)
    assert cat.actual() == {"a": "activo-a", "b": "activo-b"}


def test_catalogo_roto_conserva_el_anterior(tmp_path):
    ruta = tmp_path / "catalogo.json"
    ruta.write_text('{"a": "activo-a"}')
    cat = Catalogo(ruta, cada_s=0)
    ruta.write_text('{"a": "activo-a", ')  # coma colgante
    assert cat.actual() == {"a": "activo-a"}
    assert cat.estado()["errores"] == 1


# ------------------------------------------------------------------ gRPC real
def test_de_otlp_al_almacen_con_valor_y_tiempo(catalogo, influx):
    almacen, url = influx
    nucleo, srv, stub = armar(catalogo, url)
    try:
        enviar(stub, otlp.peticion(otlp.gauge("system.cpu.utilization", [
            otlp.punto(0.42, T0, cpu="cpu0", state="user"),
            otlp.punto(0.17, T0, cpu="cpu1", state="system"),
        ])))
        c = esperar_reposo(nucleo)
    finally:
        srv.stop(0)

    assert c["recibidas"] == 2 and c["emitidas"] == 2 and c["huerfanas"] == 0
    guardadas = filas(almacen, "system_cpu_utilization")
    assert sorted(f["value"] for f in guardadas) == [0.17, 0.42]
    f = next(f for f in guardadas if f["cpu"] == "cpu0")
    assert f["time"] == T0
    assert f["zenit_asset_id"] == "activo-aws-nodo-01"
    assert f["service_name"] == "host"
    assert {"host_name", "cloud_provider", "cloud_region", "deployment_environment"} <= set(f)
    assert "zenit_activo_id" not in f


def test_huerfana_se_almacena_y_se_cuenta(catalogo, influx):
    almacen, url = influx
    nucleo, srv, stub = armar(catalogo, url)
    try:
        enviar(stub, otlp.peticion(otlp.gauge("system.memory.utilization", [otlp.punto(0.5, T0, state="used")]),
                                   res=otlp.recurso("nodo-desconocido")))
        c = esperar_reposo(nucleo)
    finally:
        srv.stop(0)
    assert c["recibidas"] == c["emitidas"] == c["huerfanas"] == 1
    (f,) = filas(almacen, "system_memory_utilization")
    assert f["zenit_asset_id"] == "sin_resolver" and f["host_name"] == "nodo-desconocido"


def test_conteo_de_entrada_igual_al_de_salida_por_punto(catalogo, influx):
    """El criterio de H-004, contado como se debe: por punto de datos."""
    almacen, url = influx
    nucleo, srv, stub = armar(catalogo, url)
    try:
        for lote in range(5):
            puntos = [otlp.punto(float(i), T0 + lote * 10**10, cpu=f"cpu{i % 2}", state=f"s{i}") for i in range(8)]
            enviar(stub, otlp.peticion(
                otlp.gauge("system.cpu.utilization", puntos),
                otlp.suma("system.network.io", [otlp.punto(100 + lote, T0 + lote * 10**10, device="eth0", direction="transmit")]),
                otlp.gauge("rota", [otlp.punto(math.nan, T0), otlp.punto(1.0, T0, flags=1)]),
                otlp.histograma("http.server.duration", 3, 0.3, T0 + lote * 10**10),
            ))
        c = esperar_reposo(nucleo)
    finally:
        srv.stop(0)

    assert c["recibidas"] == 5 * (8 + 1 + 2 + 1)
    assert c["sin_valor"] == 5 * 2
    assert c["emitidas"] == 5 * (8 + 1 + 1)
    assert c["recibidas"] == c["emitidas"] + c["sin_valor"] + c["errores"] + c["rechazadas_por_almacen"]
    total_filas = sum(len(v) for v in almacen.bases["zenit_raw"]["tablas"].values())
    assert total_filas == 5 * (8 + 1 + 2)  # el histograma da dos filas por punto
    assert len(filas(almacen, "http_server_duration_count")) == 5


def test_token_incorrecto_se_rechaza_y_se_cuenta(catalogo, influx):
    _, url = influx
    nucleo, srv, stub = armar(catalogo, url)
    try:
        with pytest.raises(grpc.RpcError) as e:
            enviar(stub, otlp.peticion(otlp.gauge("x", [otlp.punto(1.0, T0)])), token="malo")
        assert e.value.code() == grpc.StatusCode.UNAUTHENTICATED
    finally:
        srv.stop(0)
    estado = nucleo.contadores.instantanea()
    assert estado["rechazadas"] == 1 and estado["recibidas"] == 0


def test_almacen_caido_se_reintenta_sin_perder(catalogo, influx):
    almacen, url = influx
    almacen.fallar_proximas = 2   # dos respuestas 503 seguidas
    nucleo, srv, stub = armar(catalogo, url)
    try:
        enviar(stub, otlp.peticion(otlp.gauge("x", [otlp.punto(1.0, T0), otlp.punto(2.0, T0 + 1)])))
        c = esperar_reposo(nucleo, segundos=10)
    finally:
        srv.stop(0)
    assert c["emitidas"] == 2
    assert nucleo.destino_metricas.reintentos == 2
    assert len(filas(almacen, "x")) == 2


def test_cola_llena_devuelve_unavailable_para_que_el_agente_reintente(catalogo, influx):
    almacen, url = influx
    almacen.fallar_proximas = 10**6  # el almacen no responde nunca
    nucleo, srv, stub = armar(catalogo, url, capacidad_lineas=3)
    try:
        enviar(stub, otlp.peticion(otlp.gauge("x", [otlp.punto(1.0, T0), otlp.punto(2.0, T0)])))
        with pytest.raises(grpc.RpcError) as e:
            enviar(stub, otlp.peticion(otlp.gauge("x", [otlp.punto(3.0, T0), otlp.punto(4.0, T0)])))
        assert e.value.code() == grpc.StatusCode.UNAVAILABLE
    finally:
        srv.stop(0)
        nucleo.destino_metricas.detener(0)
    estado = nucleo.contadores.instantanea()
    assert estado["contrapresion"] == 1
    # la peticion devuelta no cuenta como recibida: el agente la va a reenviar
    assert estado["senales"]["metricas"]["recibidas"] == 2


def test_una_peticion_invalida_no_arrastra_a_las_demas(catalogo, influx, tmp_path):
    """Hallazgo de la revision: un 400 descartaba el lote entero, con datos
    de todos los nodos. Ahora se reintenta peticion por peticion."""
    almacen, url = influx
    almacen.tipos[("zenit_raw", "mala", "value")] = "entero"  # la tabla "mala" fuerza un 400
    nucleo = PuntoDeEntrada(catalogo)
    c = nucleo.contadores
    destino = DestinoInflux(
        ClienteInflux(url, "apiv3_prueba"), "zenit_raw",
        al_escribir=lambda n: c.sumar("metricas", "emitidas", n),
        al_rechazar=lambda n: c.sumar("metricas", "rechazadas_por_almacen", n),
        espera_lote_s=0.5, carpeta_rechazos=tmp_path / "rechazadas",
    )
    nucleo.destino_metricas = destino
    # tres peticiones que caen en el mismo lote: buena, mala, buena
    assert nucleo.exportar_metricas(otlp.peticion(otlp.gauge("buena", [otlp.punto(1.0, T0)])))
    assert nucleo.exportar_metricas(otlp.peticion(otlp.gauge("mala", [otlp.punto(2.0, T0), otlp.punto(3.0, T0)])))
    assert nucleo.exportar_metricas(otlp.peticion(otlp.gauge("buena", [otlp.punto(4.0, T0 + 1)])))
    estado = esperar_reposo(nucleo)
    assert estado["emitidas"] == 2 and estado["rechazadas_por_almacen"] == 2
    assert len(filas(almacen, "buena")) == 2
    (archivo,) = (tmp_path / "rechazadas").glob("metricas-*.lp")
    assert archivo.read_text().count("mala,") == 2


def test_error_5xx_persistente_termina_en_rechazo_y_no_bloquea(catalogo, influx, tmp_path, monkeypatch):
    almacen, url = influx
    nucleo = PuntoDeEntrada(catalogo)
    c = nucleo.contadores
    cliente = ClienteInflux(url, "apiv3_prueba")
    from zenit.influx import ErrorInflux

    def falla_500(bd, cuerpo):
        if "rota" in cuerpo:
            raise ErrorInflux("500 TooManyFieldFamilies", 500)
        return escribir_real(bd, cuerpo)
    escribir_real = cliente.escribir
    monkeypatch.setattr(cliente, "escribir", falla_500)
    import destinos
    monkeypatch.setattr(destinos.DestinoInflux, "_dormir", lambda self, s: None)
    nucleo.destino_metricas = DestinoInflux(
        cliente, "zenit_raw",
        al_escribir=lambda n: c.sumar("metricas", "emitidas", n),
        al_rechazar=lambda n: c.sumar("metricas", "rechazadas_por_almacen", n),
        espera_lote_s=0.3, carpeta_rechazos=tmp_path, intentos_5xx=3,
    )
    nucleo.exportar_metricas(otlp.peticion(otlp.gauge("rota", [otlp.punto(1.0, T0)])))
    nucleo.exportar_metricas(otlp.peticion(otlp.gauge("sana", [otlp.punto(1.0, T0)])))
    estado = esperar_reposo(nucleo)
    assert estado["rechazadas_por_almacen"] == 1 and estado["emitidas"] == 1


def test_al_detener_con_el_almacen_caido_no_cuenta_como_emitido(catalogo, influx):
    """Hallazgo de la revision: lo que no se escribio se contaba como emitido."""
    almacen, url = influx
    almacen.fallar_proximas = 10**6
    nucleo, srv, stub = armar(catalogo, url)
    try:
        enviar(stub, otlp.peticion(otlp.gauge("x", [otlp.punto(1.0, T0), otlp.punto(2.0, T0)])))
        time.sleep(1.5)
    finally:
        srv.stop(0)
    inicio = time.time()
    nucleo.destino_metricas.detener(esperar_s=1)
    assert time.time() - inicio < 5          # la espera de reintento se interrumpe
    c = nucleo.contadores.instantanea()["senales"]["metricas"]
    assert c["emitidas"] == 0 and c["en_cola"] == 2


def test_peticion_mas_grande_que_la_cola_entra_si_la_cola_esta_vacia(catalogo, influx):
    almacen, url = influx
    nucleo, srv, stub = armar(catalogo, url, capacidad_lineas=3)
    try:
        enviar(stub, otlp.peticion(otlp.gauge("x", [otlp.punto(float(i), T0 + i) for i in range(10)])))
        c = esperar_reposo(nucleo)
    finally:
        srv.stop(0)
    assert c["emitidas"] == 10


def test_histograma_sin_suma_no_inventa_un_cero():
    m = otlp.histograma("http.server.duration", 4, 0.0, T0)
    m.histogram.data_points[0].ClearField("sum")
    (p,) = extraer_puntos(m, 0)
    assert p.valores == [("_count", 4.0)]


def test_destino_jsonl_conserva_valor_y_tiempo(catalogo, tmp_path):
    nucleo = PuntoDeEntrada(catalogo)
    nucleo.destino_metricas = DestinoJsonl(tmp_path, "metricas", al_escribir=lambda n: None)
    assert nucleo.exportar_metricas(otlp.peticion(otlp.gauge("system.cpu.utilization",
                                                              [otlp.punto(0.42, T0, cpu="cpu0")])))
    (archivo,) = tmp_path.glob("metricas-*.jsonl")
    registro = json.loads(archivo.read_text())
    assert registro["value"] == 0.42 and registro["time_ns"] == T0
    assert registro["tabla"] == "system_cpu_utilization"
    assert nucleo.contadores.instantanea()["senales"]["metricas"]["emitidas"] == 1


def test_base_aun_no_creada_se_reintenta_en_vez_de_perder(catalogo):
    """Si la ingesta arranca antes que crear-bases, los datos esperan en cola."""
    almacen, url, srv_influx = falso_influx.levantar()
    nucleo, srv, stub = armar(catalogo, url)
    try:
        enviar(stub, otlp.peticion(otlp.gauge("x", [otlp.punto(1.0, T0)])))
        time.sleep(1.5)
        assert nucleo.contadores.instantanea()["senales"]["metricas"]["en_cola"] == 1
        almacen.bases["zenit_raw"] = {"retencion": "7d", "tablas": {}}
        c = esperar_reposo(nucleo, segundos=10)
    finally:
        srv.stop(0)
        srv_influx.shutdown()
    assert c["emitidas"] == 1 and c["rechazadas_por_almacen"] == 0


# ------------------------------------------------------------------ registros (verdad de referencia)
def _peticion_de_registros(cuerpo):
    from opentelemetry.proto.collector.logs.v1 import logs_service_pb2
    from opentelemetry.proto.logs.v1 import logs_pb2

    reg = logs_pb2.LogRecord(time_unix_nano=T0)
    reg.body.string_value = cuerpo
    return logs_service_pb2.ExportLogsServiceRequest(resource_logs=[logs_pb2.ResourceLogs(
        resource=otlp.recurso(), scope_logs=[logs_pb2.ScopeLogs(log_records=[reg])])])


def _stub_registros(nucleo):
    from opentelemetry.proto.collector.logs.v1 import logs_service_pb2_grpc

    srv = servidor_grpc(nucleo, TOKEN, 0)
    puerto = srv.add_insecure_port("127.0.0.1:0")
    srv.start()
    return srv, logs_service_pb2_grpc.LogsServiceStub(grpc.insecure_channel(f"127.0.0.1:{puerto}"))


def test_registros_se_guardan_con_las_etiquetas_del_activo(catalogo, tmp_path):
    nucleo = PuntoDeEntrada(catalogo, servicio_por_defecto="host")
    nucleo.destinos_archivo["registros"] = DestinoJsonl(
        tmp_path, "registros", lambda n: nucleo.contadores.sumar("registros", "emitidas", n))
    srv, stub = _stub_registros(nucleo)
    try:
        enviar(stub, _peticion_de_registros('{"uid":"u1","evento":"inicio"}'))
        (archivo,) = tmp_path.glob("registros-*.jsonl")
        linea = json.loads(archivo.read_text())
        assert linea["zenit_asset_id"] == "activo-aws-nodo-01"
        assert json.loads(linea["cuerpo"]) == {"uid": "u1", "evento": "inicio"}
    finally:
        srv.stop(0)


def test_si_no_se_puede_guardar_un_registro_el_agente_debe_reintentar(catalogo):
    """Responder OK sin guardar perderia la verdad de referencia en silencio."""
    nucleo = PuntoDeEntrada(catalogo, servicio_por_defecto="host")

    class DiscoLleno:
        def aceptar_registros(self, registros, puntos):
            raise OSError(28, "No space left on device")

    nucleo.destinos_archivo["registros"] = DiscoLleno()
    srv, stub = _stub_registros(nucleo)
    try:
        with pytest.raises(grpc.RpcError) as e:
            enviar(stub, _peticion_de_registros('{"uid":"u1","evento":"inicio"}'))
        assert e.value.code() == grpc.StatusCode.UNAVAILABLE  # el Collector reintenta UNAVAILABLE
        assert nucleo.contadores.instantanea()["senales"]["registros"]["errores"] == 1
    finally:
        srv.stop(0)
