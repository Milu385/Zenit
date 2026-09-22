"""Punto de entrada OTLP de Zenit.

Recibe OTLP por gRPC, aplica el esquema canonico, resuelve el activo
contra el catalogo y escribe al destino. En H-004 el destino es un
archivo JSONL; en H-005 sera el almacen real.

El TLS lo termina el borde (nginx); aqui solo se valida el token.
"""
import hmac
import json
import os
import threading
import time
from concurrent import futures
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import grpc
from opentelemetry.proto.collector.logs.v1 import logs_service_pb2, logs_service_pb2_grpc
from opentelemetry.proto.collector.metrics.v1 import metrics_service_pb2, metrics_service_pb2_grpc
from opentelemetry.proto.collector.trace.v1 import trace_service_pb2, trace_service_pb2_grpc

PUERTO_OTLP = int(os.environ.get("PUERTO_OTLP", "4317"))
PUERTO_ADMIN = int(os.environ.get("PUERTO_ADMIN", "8080"))
RUTA_SALIDA = Path(os.environ.get("RUTA_SALIDA", "/datos/salida"))
RUTA_CATALOGO = Path(os.environ.get("RUTA_CATALOGO", "/etc/zenit/catalogo.json"))
TOKEN_INGESTA = os.environ.get("TOKEN_INGESTA", "")

RUTA_SALIDA.mkdir(parents=True, exist_ok=True)


# ------------------------------------------------------------ autenticacion
class Autenticacion(grpc.ServerInterceptor):
    """Valida el token compartido. Es lo unico que protege el 4317 cuando
    el nodo esta detras de un NAT y no se puede filtrar por IP."""

    def __init__(self, token):
        self._token = token
        self._rechazo = grpc.unary_unary_rpc_method_handler(
            lambda _peticion, contexto: contexto.abort(
                grpc.StatusCode.UNAUTHENTICATED, "token invalido o ausente"))

    def intercept_service(self, continuar, detalles):
        if not self._token:          # sin token configurado: modo local
            return continuar(detalles)
        metadatos = dict(detalles.invocation_metadata or ())
        recibido = metadatos.get("authorization", "")
        if hmac.compare_digest(recibido, f"Bearer {self._token}"):
            return continuar(detalles)
        sumar("rechazadas")
        return self._rechazo

# ---------------------------------------------------------------- contadores
# Se exponen desde el principio: el criterio se verifica comparando
# el conteo de entrada con el de salida.
CONTADORES = {"recibidas": 0, "emitidas": 0, "huerfanas": 0,
              "errores": 0, "rechazadas": 0}
_candado = threading.Lock()


def sumar(clave, n=1):
    with _candado:
        CONTADORES[clave] += n


# ---------------------------------------------------------------- catalogo
def cargar_catalogo():
    """Sustituto del catalogo de activos real. En H-005/H-006 esto
    pasa a ser una consulta a base de datos."""
    if RUTA_CATALOGO.exists():
        return json.loads(RUTA_CATALOGO.read_text())
    return {}


CATALOGO = cargar_catalogo()

# Claves nativas por las que intentamos resolver, en orden de preferencia.
CLAVES_NATIVAS = ("host.id", "host.name", "container.id", "service.instance.id")


# ---------------------------------------------------- esquema canonico
def valor_de(any_value):
    """Convierte un AnyValue de OTLP a un tipo de Python."""
    campo = any_value.WhichOneof("value")
    if campo is None:
        return None
    if campo == "array_value":
        return [valor_de(v) for v in any_value.array_value.values]
    if campo == "kvlist_value":
        return {kv.key: valor_de(kv.value) for kv in any_value.kvlist_value.values}
    return getattr(any_value, campo)


def canonizar(clave: str) -> str:
    """Puntos a guiones bajos. La conversion ocurre AQUI y una sola vez,
    no en cada consulta."""
    return clave.replace(".", "_").replace("-", "_").lower()


OBLIGATORIOS = ("deployment_environment", "cloud_provider", "cloud_region")


def atributos_de_recurso(recurso):
    crudos = {kv.key: valor_de(kv.value) for kv in recurso.attributes}
    canonicos = {canonizar(k): v for k, v in crudos.items()}

    # activo: se resuelve por la primera clave nativa disponible
    activo, clave_usada = None, None
    for clave in CLAVES_NATIVAS:
        if clave in crudos:
            activo = CATALOGO.get(str(crudos[clave]))
            clave_usada = clave
            if activo:
                break

    canonicos["zenit_clave_nativa"] = canonizar(clave_usada) if clave_usada else None
    canonicos["zenit_activo_id"] = activo
    # NINGUNA SEÑAL SE DESCARTA: si no resuelve, se marca y se guarda igual
    canonicos["zenit_huerfana"] = activo is None

    for obligatorio in OBLIGATORIOS:
        canonicos.setdefault(obligatorio, "desconocido")

    return canonicos


def escribir(senal: str, registro: dict):
    """Destino de mentira. H-005 lo reemplaza por el almacen real."""
    archivo = RUTA_SALIDA / f"{senal}-{time.strftime('%Y%m%d')}.jsonl"
    with archivo.open("a") as f:
        f.write(json.dumps(registro, default=str) + "\n")


def procesar(senal, bloques, extraer):
    """bloques: resource_metrics / resource_spans / resource_logs."""
    for bloque in bloques:
        recurso = atributos_de_recurso(bloque.resource)
        for punto in extraer(bloque):
            sumar("recibidas")
            try:
                escribir(senal, {**recurso, **punto, "zenit_senal": senal})
                sumar("emitidas")
                if recurso["zenit_huerfana"]:
                    sumar("huerfanas")
            except Exception:
                sumar("errores")


# ---------------------------------------------------------------- servicios
class Metricas(metrics_service_pb2_grpc.MetricsServiceServicer):
    def Export(self, request, context):
        def extraer(bloque):
            for alcance in bloque.scope_metrics:
                for metrica in alcance.metrics:
                    yield {"nombre": metrica.name, "unidad": metrica.unit,
                           "tipo": metrica.WhichOneof("data")}
        procesar("metricas", request.resource_metrics, extraer)
        return metrics_service_pb2.ExportMetricsServiceResponse()


class Trazas(trace_service_pb2_grpc.TraceServiceServicer):
    def Export(self, request, context):
        def extraer(bloque):
            for alcance in bloque.scope_spans:
                for span in alcance.spans:
                    yield {"traza_id": span.trace_id.hex(),
                           "span_id": span.span_id.hex(),
                           "nombre": span.name,
                           "inicio_ns": span.start_time_unix_nano,
                           "fin_ns": span.end_time_unix_nano}
        procesar("trazas", request.resource_spans, extraer)
        return trace_service_pb2.ExportTraceServiceResponse()


class Registros(logs_service_pb2_grpc.LogsServiceServicer):
    def Export(self, request, context):
        def extraer(bloque):
            for alcance in bloque.scope_logs:
                for reg in alcance.log_records:
                    yield {"severidad": reg.severity_text,
                           "cuerpo": valor_de(reg.body),
                           "momento_ns": reg.time_unix_nano}
        procesar("registros", request.resource_logs, extraer)
        return logs_service_pb2.ExportLogsServiceResponse()


# ---------------------------------------------------------------- admin HTTP
class Admin(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/salud":
            cuerpo = json.dumps({"estado": "ok"}).encode()
        elif self.path == "/metricas":
            with _candado:
                cuerpo = json.dumps(dict(CONTADORES)).encode()
        else:
            self.send_response(404); self.end_headers(); return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def log_message(self, *_):
        pass


def principal():
    threading.Thread(
        target=lambda: HTTPServer(("0.0.0.0", PUERTO_ADMIN), Admin).serve_forever(),
        daemon=True).start()

    servidor = grpc.server(
        futures.ThreadPoolExecutor(max_workers=16),
        interceptors=[Autenticacion(TOKEN_INGESTA)])
    metrics_service_pb2_grpc.add_MetricsServiceServicer_to_server(Metricas(), servidor)
    trace_service_pb2_grpc.add_TraceServiceServicer_to_server(Trazas(), servidor)
    logs_service_pb2_grpc.add_LogsServiceServicer_to_server(Registros(), servidor)
    servidor.add_insecure_port(f"0.0.0.0:{PUERTO_OTLP}")
    servidor.start()
    print(f"ingesta OTLP escuchando en {PUERTO_OTLP}, admin en {PUERTO_ADMIN}", flush=True)
    servidor.wait_for_termination()


if __name__ == "__main__":
    principal()
