"""Emisor de prueba para medir la ingesta (P-REN-01, P-REN-02, H-016, H-031).

Manda OTLP al agente del nodo, asi que lo medido recorre el mismo camino que
la telemetria real: agente, cola, mTLS, borde, ingesta y almacen.

    latencia      un punto de zenit.sonda cada 10 s; su valor es el instante de
                  emision en segundos (P-REN-01, plan de pruebas 6.2)
    completitud   N puntos por segundo de zenit.sonda.completitud con marca de
                  tiempo unica; al final dice cuantos mando (P-REN-02, 6.3)
    registros     N registros por segundo (H-016: al menos 500 sin perdida)

Al terminar imprime cuanto mando, con la ventana exacta, para contarlo en la
plataforma con zenit.admin contar.
"""
import argparse
import os
import time
from datetime import datetime, timezone

import grpc
from opentelemetry.proto.collector.logs.v1 import logs_service_pb2, logs_service_pb2_grpc
from opentelemetry.proto.collector.metrics.v1 import metrics_service_pb2, metrics_service_pb2_grpc
from opentelemetry.proto.common.v1 import common_pb2
from opentelemetry.proto.logs.v1 import logs_pb2
from opentelemetry.proto.metrics.v1 import metrics_pb2
from opentelemetry.proto.resource.v1 import resource_pb2

RECURSO = resource_pb2.Resource(attributes=[
    common_pb2.KeyValue(key="service.name", value=common_pb2.AnyValue(string_value="sonda"))])


def _metricas(nombre, puntos):
    m = metrics_pb2.Metric(name=nombre, unit="1", gauge=metrics_pb2.Gauge(data_points=[
        metrics_pb2.NumberDataPoint(as_double=v, time_unix_nano=t) for v, t in puntos]))
    return metrics_service_pb2.ExportMetricsServiceRequest(resource_metrics=[metrics_pb2.ResourceMetrics(
        resource=RECURSO, scope_metrics=[metrics_pb2.ScopeMetrics(metrics=[m])])])


def _registros(textos_y_tiempos):
    return logs_service_pb2.ExportLogsServiceRequest(resource_logs=[logs_pb2.ResourceLogs(
        resource=RECURSO, scope_logs=[logs_pb2.ScopeLogs(log_records=[
            logs_pb2.LogRecord(time_unix_nano=t, severity_number=9, severity_text="INFO",
                               body=common_pb2.AnyValue(string_value=x)) for x, t in textos_y_tiempos])])])


def _exportar(llamada, peticion, intentos=20):
    for i in range(intentos):
        try:
            llamada(peticion, timeout=10)
            return
        except grpc.RpcError as e:
            if i == intentos - 1:
                raise
            print(f"el agente respondio {e.code().name}, reintento", flush=True)
            time.sleep(1)


def _ahora():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def latencia(canal, n: int, cada: int) -> None:
    stub = metrics_service_pb2_grpc.MetricsServiceStub(canal)
    print(f"desde {_ahora()}: {n} puntos de zenit.sonda, uno cada {cada} s", flush=True)
    for _ in range(n):
        # a mitad de cada intervalo de 10 s: exactamente un punto por intervalo,
        # asi el promedio de 10 s que devuelve la API es el valor emitido
        ahora = time.time()
        siguiente = (int(ahora - cada / 2) // cada + 1) * cada + cada / 2
        time.sleep(max(0.0, siguiente - ahora))
        t = time.time_ns()
        _exportar(stub.Export, _metricas("zenit.sonda", [(t / 1e9, t)]))
    print(f"hasta {_ahora()}: {n} puntos enviados", flush=True)


def completitud(canal, por_segundo: int, duracion: int) -> None:
    stub = metrics_service_pb2_grpc.MetricsServiceStub(canal)
    enviados, inicio = 0, time.time()
    print(f"desde {_ahora()}: {por_segundo} puntos por segundo durante {duracion} s", flush=True)
    while time.time() - inicio < duracion:
        segundo = time.monotonic()
        base = time.time_ns()
        # marcas distintas dentro del mismo segundo: cada punto es una fila
        _exportar(stub.Export, _metricas("zenit.sonda.completitud",
                                         [(float(enviados + k), base + k * 1000) for k in range(por_segundo)]))
        enviados += por_segundo
        time.sleep(max(0.0, 1.0 - (time.monotonic() - segundo)))
    print(f"hasta {_ahora()}: {enviados} puntos enviados", flush=True)


def registros(canal, por_segundo: int, duracion: int) -> None:
    stub = logs_service_pb2_grpc.LogsServiceStub(canal)
    enviados, inicio = 0, time.time()
    print(f"desde {_ahora()}: {por_segundo} registros por segundo durante {duracion} s", flush=True)
    while time.time() - inicio < duracion:
        segundo = time.monotonic()
        base = time.time_ns()
        _exportar(stub.Export, _registros([(f"sonda {enviados + k}", base + k * 1000) for k in range(por_segundo)]))
        enviados += por_segundo
        time.sleep(max(0.0, 1.0 - (time.monotonic() - segundo)))
    print(f"hasta {_ahora()}: {enviados} registros enviados", flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("modo", choices=["latencia", "completitud", "registros"])
    p.add_argument("--n", type=int, default=360, help="latencia: cuantos puntos (360 = una hora)")
    p.add_argument("--cada", type=int, default=10)
    p.add_argument("--por-segundo", type=int, default=200)
    p.add_argument("--duracion", type=int, default=3600, help="segundos")
    p.add_argument("--destino", default=os.environ.get("DESTINO_SONDA", "agente:4317"))
    a = p.parse_args()
    canal = grpc.insecure_channel(a.destino)     # dentro del nodo; el agente cifra hacia la plataforma
    if a.modo == "latencia":
        latencia(canal, a.n, a.cada)
    elif a.modo == "completitud":
        completitud(canal, a.por_segundo, a.duracion)
    else:
        registros(canal, a.por_segundo, a.duracion)


if __name__ == "__main__":
    main()
