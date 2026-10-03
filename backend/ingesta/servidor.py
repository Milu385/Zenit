"""Punto de entrada OTLP de Zenit.

Recibe OTLP por gRPC, aplica el esquema canonico, resuelve el activo contra el
catalogo y escribe al destino. Las metricas van al almacen de series (H-005);
trazas y registros siguen en archivo hasta la epica 1.

El TLS lo termina el borde (nginx); aqui solo se valida el token.

Cambios respecto a la version de H-004:
- se baja hasta los puntos de datos, con su valor y su marca de tiempo;
- los contadores cuentan puntos, no metricas;
- etiqueta zenit_asset_id (antes zenit_activo_id) y service_name siempre
  presente;
- el nombre de la metrica se convierte una sola vez, aqui;
- el catalogo se relee cuando cambia el archivo, sin reiniciar;
- si el almacen no da abasto, se responde UNAVAILABLE y el agente reintenta.
"""
import hmac
import json
import logging
import os
import signal
import threading
import time
from concurrent import futures
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

import grpc
from opentelemetry.proto.collector.logs.v1 import logs_service_pb2, logs_service_pb2_grpc
from opentelemetry.proto.collector.metrics.v1 import metrics_service_pb2, metrics_service_pb2_grpc
from opentelemetry.proto.collector.trace.v1 import trace_service_pb2, trace_service_pb2_grpc

from destinos import DestinoInflux, DestinoJsonl, Lote
from extraccion import atributos, extraer_puntos, resolver_identidad, valor_de
from zenit.influx import ClienteInflux
from zenit.protocolo_linea import construir_linea

log = logging.getLogger("ingesta")

SENALES = ("metricas", "trazas", "registros")


# ------------------------------------------------------------------ contadores
class Contadores:
    """Se exponen desde el principio: el criterio de H-004 se verifica
    comparando el conteo de entrada con el de salida.

    Para metricas la unidad es el punto de datos. Se cumple siempre que

        recibidas = emitidas + sin_valor + errores + rechazadas_por_almacen + en_cola

    y cuando el sistema esta en reposo, en_cola es cero.
    """

    CAMPOS = ("recibidas", "emitidas", "huerfanas", "sin_valor", "errores", "rechazadas_por_almacen")

    def __init__(self):
        self._candado = threading.Lock()
        self._valores = {s: dict.fromkeys(self.CAMPOS, 0) for s in SENALES}
        self.peticiones_rechazadas = 0   # token invalido
        self.contrapresion = 0           # peticiones devueltas por cola llena

    def sumar(self, senal: str, campo: str, n: int = 1) -> None:
        if n:
            with self._candado:
                self._valores[senal][campo] += n

    def rechazo(self) -> None:
        with self._candado:
            self.peticiones_rechazadas += 1

    def sin_espacio(self) -> None:
        with self._candado:
            self.contrapresion += 1

    def instantanea(self) -> dict:
        with self._candado:
            por_senal = {s: dict(v) for s, v in self._valores.items()}
            rechazadas = self.peticiones_rechazadas
            contrapresion = self.contrapresion
        for v in por_senal.values():
            v["en_cola"] = (
                v["recibidas"] - v["emitidas"] - v["sin_valor"] - v["errores"] - v["rechazadas_por_almacen"]
            )
        totales = {c: sum(v[c] for v in por_senal.values()) for c in ("recibidas", "emitidas", "huerfanas", "errores")}
        return {
            **totales,
            "rechazadas": rechazadas,
            "contrapresion": contrapresion,
            "senales": por_senal,
        }


# ------------------------------------------------------------------ catalogo
class Catalogo:
    """Correspondencia de identificador nativo a activo canonico.

    Se relee cuando el archivo cambia, como mucho cada `cada_s` segundos, asi
    que admitir un nodo ya no exige reiniciar el punto de entrada. Si el
    archivo nuevo esta mal formado se conserva el anterior y se cuenta el
    error. En la epica 1 (H-009) esto pasa a ser una consulta a base de datos.
    """

    def __init__(self, ruta: Path, cada_s: float = 5.0):
        self._ruta = Path(ruta)
        self._cada = cada_s
        self._datos: dict = {}
        self._firma = None
        self._revisado = 0.0
        self._candado = threading.Lock()
        self.cargado: Optional[str] = None
        self.errores = 0
        self._recargar()

    def _firma_actual(self):
        try:
            st = self._ruta.stat()
            return (st.st_ino, st.st_mtime_ns, st.st_size)
        except FileNotFoundError:
            return None

    def _recargar(self) -> None:
        firma = self._firma_actual()
        if firma == self._firma:
            return
        if firma is None:
            log.warning("no existe el catalogo %s: toda senal sera huerfana", self._ruta)
            self._datos, self._firma = {}, None
            return
        try:
            datos = json.loads(self._ruta.read_text())
            if not isinstance(datos, dict):
                raise ValueError("el catalogo debe ser un objeto JSON")
            self._datos = {str(k): str(v) for k, v in datos.items()}
            self._firma = firma
            self.cargado = datetime.now(timezone.utc).isoformat(timespec="seconds")
            log.info("catalogo cargado: %d activos", len(self._datos))
        except (ValueError, OSError) as e:
            self.errores += 1
            self._firma = firma  # no reintentar el mismo archivo roto en bucle
            log.error("catalogo invalido, se conserva el anterior: %s", e)

    def actual(self) -> dict:
        ahora = time.monotonic()
        with self._candado:
            if ahora - self._revisado >= self._cada:
                self._revisado = ahora
                self._recargar()
            return self._datos

    def estado(self) -> dict:
        return {"activos": len(self._datos), "cargado": self.cargado, "errores": self.errores}


# ------------------------------------------------------------------ nucleo
class PuntoDeEntrada:
    """Todo el estado del servicio, inyectable para las pruebas."""

    def __init__(self, catalogo: Catalogo, servicio_por_defecto: str = "host"):
        self.catalogo = catalogo
        self.servicio_por_defecto = servicio_por_defecto
        self.contadores = Contadores()
        self.destino_metricas = None
        self.destinos_archivo: dict = {}

    def identidad(self, recurso):
        return resolver_identidad(atributos(recurso.attributes), self.catalogo.actual(), self.servicio_por_defecto)

    # ---- metricas
    def exportar_metricas(self, request) -> bool:
        """Convierte y entrega. Devuelve False si el destino no tiene espacio."""
        ahora = time.time_ns()
        lineas, registros = [], []
        validos = huerfanos = sin_valor = errores = 0
        jsonl = isinstance(self.destino_metricas, DestinoJsonl)

        for bloque in request.resource_metrics:
            ident = self.identidad(bloque.resource)
            for alcance in bloque.scope_metrics:
                for metrica in alcance.metrics:
                    for punto in extraer_puntos(metrica, ahora):
                        if punto.sin_valor:
                            sin_valor += 1
                            continue
                        if not 0 <= punto.tiempo_ns < 2**63:
                            log.warning("marca de tiempo fuera de rango en %s: %s", metrica.name, punto.tiempo_ns)
                            errores += 1
                            continue
                        try:
                            etiquetas = {**punto.dimensiones, **ident.etiquetas}
                            propias, suyos = [], []
                            for sufijo, valor in punto.valores:
                                linea = construir_linea(punto.tabla + sufijo, etiquetas, valor, punto.tiempo_ns)
                                if linea is None:   # NaN o infinito
                                    propias = None
                                    break
                                propias.append(linea)
                                if jsonl:
                                    suyos.append({
                                        **etiquetas, "tabla": punto.tabla + sufijo,
                                        "value": valor, "time_ns": punto.tiempo_ns,
                                    })
                        except Exception:  # un punto malo no tumba la peticion
                            log.exception("punto no convertible en %s", metrica.name)
                            errores += 1
                            continue
                        if propias is None:
                            sin_valor += 1
                            continue
                        lineas.extend(propias)
                        registros.extend(suyos)
                        validos += 1
                        huerfanos += ident.huerfana

        # Se cuenta como recibido ANTES de entregar: el escritor puede sumar
        # emitidas apenas lo tiene, y en_cola nunca debe quedar negativo. Si
        # el destino no tiene espacio, se descuenta: el agente lo reenviara.
        c = self.contadores
        total = validos + sin_valor + errores
        c.sumar("metricas", "recibidas", total)
        if jsonl:
            aceptado = True
            if validos:
                self.destino_metricas.aceptar_registros(registros, 0)
        else:
            aceptado = self.destino_metricas.aceptar(Lote(lineas, validos))
        if not aceptado:
            c.sumar("metricas", "recibidas", -total)
            c.sin_espacio()
            return False

        c.sumar("metricas", "sin_valor", sin_valor)
        c.sumar("metricas", "errores", errores)
        c.sumar("metricas", "huerfanas", huerfanos)
        if jsonl:
            c.sumar("metricas", "emitidas", validos)
        return True

    # ---- trazas y registros: a archivo hasta la epica 1
    def exportar_archivo(self, senal: str, bloques, extraer) -> bool:
        """False si no se pudo escribir: quien llama responde UNAVAILABLE y el
        agente reintenta desde su cola persistente. Responder OK sin haber
        guardado perderia la linea en silencio (la verdad de referencia del
        laboratorio viaja por aqui)."""
        registros, huerfanos = [], 0
        for bloque in bloques:
            ident = self.identidad(bloque.resource)
            for item in extraer(bloque):
                registros.append({**ident.etiquetas, **item})
                huerfanos += ident.huerfana
        if not registros:
            return True
        self.contadores.sumar(senal, "recibidas", len(registros))
        self.contadores.sumar(senal, "huerfanas", huerfanos)
        try:
            self.destinos_archivo[senal].aceptar_registros(registros, len(registros))
        except OSError:
            log.exception("no se pudo escribir %s", senal)
            self.contadores.sumar(senal, "errores", len(registros))
            return False
        return True

    def estado(self) -> dict:
        d = self.destino_metricas
        return {
            **self.contadores.instantanea(),
            "destino": {
                "tipo": "influx" if isinstance(d, DestinoInflux) else "jsonl",
                "lineas_pendientes": getattr(d, "pendientes", 0),
                "reintentos": getattr(d, "reintentos", 0),
                "ultimo_error": getattr(d, "ultimo_error", None),
            },
            "catalogo": self.catalogo.estado(),
        }


# ------------------------------------------------------------------ autenticacion
class Autenticacion(grpc.ServerInterceptor):
    """Valida el token compartido. Es lo unico que protege el 4317 cuando
    el nodo esta detras de un NAT y no se puede filtrar por IP."""

    def __init__(self, token: str, contadores: Contadores):
        self._token = token
        self._contadores = contadores
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
        self._contadores.rechazo()
        return self._rechazo


# ------------------------------------------------------------------ servicios gRPC
class Metricas(metrics_service_pb2_grpc.MetricsServiceServicer):
    def __init__(self, nucleo: PuntoDeEntrada):
        self.nucleo = nucleo

    def Export(self, request, context):
        if not self.nucleo.exportar_metricas(request):
            context.abort(grpc.StatusCode.UNAVAILABLE, "almacen saturado, reintentar")
        return metrics_service_pb2.ExportMetricsServiceResponse()


class Trazas(trace_service_pb2_grpc.TraceServiceServicer):
    def __init__(self, nucleo: PuntoDeEntrada):
        self.nucleo = nucleo

    def Export(self, request, context):
        def extraer(bloque):
            for alcance in bloque.scope_spans:
                for span in alcance.spans:
                    yield {"traza_id": span.trace_id.hex(),
                           "span_id": span.span_id.hex(),
                           "padre_id": span.parent_span_id.hex(),
                           "nombre": span.name,
                           "inicio_ns": span.start_time_unix_nano,
                           "fin_ns": span.end_time_unix_nano}
        if not self.nucleo.exportar_archivo("trazas", request.resource_spans, extraer):
            context.abort(grpc.StatusCode.UNAVAILABLE, "no se pudo guardar, reintentar")
        return trace_service_pb2.ExportTraceServiceResponse()


class Registros(logs_service_pb2_grpc.LogsServiceServicer):
    def __init__(self, nucleo: PuntoDeEntrada):
        self.nucleo = nucleo

    def Export(self, request, context):
        def extraer(bloque):
            for alcance in bloque.scope_logs:
                for reg in alcance.log_records:
                    yield {"severidad": reg.severity_text,
                           "cuerpo": valor_de(reg.body),
                           "momento_ns": reg.time_unix_nano or reg.observed_time_unix_nano}
        if not self.nucleo.exportar_archivo("registros", request.resource_logs, extraer):
            context.abort(grpc.StatusCode.UNAVAILABLE, "no se pudo guardar, reintentar")
        return logs_service_pb2.ExportLogsServiceResponse()


# ------------------------------------------------------------------ admin HTTP
def servidor_admin(nucleo: PuntoDeEntrada, puerto: int) -> ThreadingHTTPServer:
    class Admin(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/salud":
                cuerpo = json.dumps({"estado": "ok"}).encode()
            elif self.path == "/metricas":
                cuerpo = json.dumps(nucleo.estado()).encode()
            else:
                self.send_response(404); self.end_headers(); return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)

        def log_message(self, *_):
            pass

    return ThreadingHTTPServer(("0.0.0.0", puerto), Admin)


def servidor_grpc(nucleo: PuntoDeEntrada, token: str, puerto: int) -> grpc.Server:
    servidor = grpc.server(
        futures.ThreadPoolExecutor(max_workers=16),
        interceptors=[Autenticacion(token, nucleo.contadores)],
        options=[("grpc.max_receive_message_length", 16 * 1024 * 1024)],
    )
    metrics_service_pb2_grpc.add_MetricsServiceServicer_to_server(Metricas(nucleo), servidor)
    trace_service_pb2_grpc.add_TraceServiceServicer_to_server(Trazas(nucleo), servidor)
    logs_service_pb2_grpc.add_LogsServiceServicer_to_server(Registros(nucleo), servidor)
    servidor.add_insecure_port(f"0.0.0.0:{puerto}")
    return servidor


# ------------------------------------------------------------------ arranque
def construir_desde_entorno() -> PuntoDeEntrada:
    salida = Path(os.environ.get("RUTA_SALIDA", "/datos/salida"))
    nucleo = PuntoDeEntrada(
        Catalogo(Path(os.environ.get("RUTA_CATALOGO", "/etc/zenit/catalogo/catalogo.json"))),
        servicio_por_defecto=os.environ.get("SERVICIO_POR_DEFECTO", "host"),
    )
    c = nucleo.contadores

    destino = os.environ.get("DESTINO_METRICAS") or ("influx" if os.environ.get("INFLUX_URL") else "jsonl")
    if destino == "influx":
        cliente = ClienteInflux(os.environ["INFLUX_URL"], os.environ.get("INFLUX_TOKEN", ""))
        nucleo.destino_metricas = DestinoInflux(
            cliente,
            os.environ.get("INFLUX_BD", "zenit_raw"),
            al_escribir=lambda n: c.sumar("metricas", "emitidas", n),
            al_rechazar=lambda n: c.sumar("metricas", "rechazadas_por_almacen", n),
            capacidad_lineas=int(os.environ.get("CAPACIDAD_LINEAS", "200000")),
            lineas_por_lote=int(os.environ.get("LINEAS_POR_LOTE", "5000")),
            carpeta_rechazos=salida / "rechazadas",
        )
    else:
        nucleo.destino_metricas = DestinoJsonl(salida, "metricas", al_escribir=lambda n: None)

    for senal in ("trazas", "registros"):
        nucleo.destinos_archivo[senal] = DestinoJsonl(
            salida, senal, al_escribir=lambda n, s=senal: c.sumar(s, "emitidas", n))
    return nucleo


def principal():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    nucleo = construir_desde_entorno()
    puerto_otlp = int(os.environ.get("PUERTO_OTLP", "4317"))
    puerto_admin = int(os.environ.get("PUERTO_ADMIN", "8080"))

    admin = servidor_admin(nucleo, puerto_admin)
    threading.Thread(target=admin.serve_forever, daemon=True).start()

    servidor = servidor_grpc(nucleo, os.environ.get("TOKEN_INGESTA", ""), puerto_otlp)
    servidor.start()
    log.info("ingesta OTLP en %d, admin en %d, metricas hacia %s",
             puerto_otlp, puerto_admin, type(nucleo.destino_metricas).__name__)

    terminar = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: terminar.set())
    signal.signal(signal.SIGINT, lambda *_: terminar.set())
    terminar.wait()
    log.info("deteniendo: se deja de aceptar y se vacia la cola")
    servidor.stop(grace=5).wait()
    nucleo.destino_metricas.detener(esperar_s=15)   # el compose da 30 s
    admin.shutdown()


if __name__ == "__main__":
    principal()
