"""Constructores de mensajes OTLP para las pruebas.

Reproducen lo que manda el Collector del nodo observado: un recurso con los
atributos de identidad y metricas con varios puntos, cada uno con sus propias
dimensiones.
"""
import time

from opentelemetry.proto.collector.metrics.v1 import metrics_service_pb2
from opentelemetry.proto.common.v1 import common_pb2
from opentelemetry.proto.metrics.v1 import metrics_pb2
from opentelemetry.proto.resource.v1 import resource_pb2


def kv(clave, valor):
    if isinstance(valor, bool):
        v = common_pb2.AnyValue(bool_value=valor)
    elif isinstance(valor, int):
        v = common_pb2.AnyValue(int_value=valor)
    else:
        v = common_pb2.AnyValue(string_value=valor)
    return common_pb2.KeyValue(key=clave, value=v)


def recurso(nodo="zenit-nodo-aws", **extra):
    attrs = {
        "host.name": nodo,
        "deployment.environment": "lab",
        "cloud.provider": "aws",
        "cloud.region": "us-east-1",
        **extra,
    }
    return resource_pb2.Resource(attributes=[kv(k, v) for k, v in attrs.items()])


def punto(valor, t_ns=None, flags=0, **dims):
    p = metrics_pb2.NumberDataPoint(
        attributes=[kv(k, v) for k, v in dims.items()],
        time_unix_nano=t_ns if t_ns is not None else time.time_ns(),
        flags=flags,
    )
    if isinstance(valor, int):
        p.as_int = valor
    elif valor is not None:
        p.as_double = valor
    return p


def gauge(nombre, puntos, unidad="1"):
    return metrics_pb2.Metric(name=nombre, unit=unidad, gauge=metrics_pb2.Gauge(data_points=puntos))


def suma(nombre, puntos, unidad="By"):
    return metrics_pb2.Metric(
        name=nombre, unit=unidad,
        sum=metrics_pb2.Sum(data_points=puntos, is_monotonic=True,
                            aggregation_temporality=metrics_pb2.AGGREGATION_TEMPORALITY_CUMULATIVE))


def histograma(nombre, cuenta, total, t_ns=None, **dims):
    p = metrics_pb2.HistogramDataPoint(
        attributes=[kv(k, v) for k, v in dims.items()],
        time_unix_nano=t_ns or time.time_ns(),
        count=cuenta, sum=total,
        bucket_counts=[cuenta], explicit_bounds=[],
    )
    return metrics_pb2.Metric(name=nombre, unit="s",
                              histogram=metrics_pb2.Histogram(data_points=[p]))


def peticion(*metricas, res=None):
    return metrics_service_pb2.ExportMetricsServiceRequest(
        resource_metrics=[
            metrics_pb2.ResourceMetrics(
                resource=res or recurso(),
                scope_metrics=[metrics_pb2.ScopeMetrics(metrics=list(metricas))],
            )
        ]
    )
