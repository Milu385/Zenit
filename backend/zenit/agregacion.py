"""Trabajo de agregacion (H-018).

    python -m zenit.agregacion --cada 60        lo corre el servicio agregador

Cada ciclo:

1. Resume en zenit_1m los minutos completos de zenit_raw. Un minuto esta
   completo cuando terminaron el y los 30 s de llegada que da RNF-REN-01.
   Ademas rehace siempre los ultimos 10 minutos, por los puntos que llegan
   tarde (un agente que vacia su cola despues de un corte corto).
2. Resume en zenit_1h las horas completas de zenit_1m, rehaciendo las dos
   ultimas.
3. Guarda el progreso en PostgreSQL y el retraso en zenit_interno.

La primera vez rellena hacia atras AGREGACION_RELLENO (por defecto, lo que
retiene zenit_raw) en tramos de una hora, con un presupuesto de tiempo por
ciclo para no quedarse minutos sin resumir lo reciente. Mientras rellena, el
retraso es grande y se ve; es lo esperado.

RNF-REN-05: el retraso al cerrar cada ciclo (ahora menos lo resumido) no
pasa de dos ciclos.
"""
import argparse
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from zenit import catalogo
from zenit.influx import ClienteInflux, ErrorInflux
from zenit.protocolo_linea import construir_linea
from zenit.repositorio.agregacion_influx import resumir, tramos

log = logging.getLogger("zenit.agregacion")

LLEGADA = timedelta(seconds=30)
VENTANA_1M = timedelta(minutes=10)
VENTANA_1H = timedelta(hours=2)
TRAMO_1M = timedelta(hours=1)
TRAMO_1H = timedelta(hours=6)


def _piso(momento: datetime, paso: timedelta) -> datetime:
    s = int(paso.total_seconds())
    return datetime.fromtimestamp(int(momento.timestamp()) // s * s, timezone.utc)


def _dias(texto: str) -> timedelta:
    texto = texto.strip().lower()
    if texto.endswith("d"):
        return timedelta(days=int(texto[:-1]))
    if texto.endswith("h"):
        return timedelta(hours=int(texto[:-1]))
    raise ValueError(f"duracion no valida: {texto!r} (use 30d o 12h)")


class Agregador:
    def __init__(self, cliente: ClienteInflux, pg_url: str, relleno: timedelta = timedelta(days=30),
                 presupuesto_s: float = 45.0, bd_interna: Optional[str] = "zenit_interno"):
        self.cliente = cliente
        self.pg_url = pg_url
        self.relleno = relleno
        self.presupuesto_s = presupuesto_s
        self.bd_interna = bd_interna

    # ---------------------------------------------------------------- progreso
    def _progreso(self, conexion, nivel: str, por_defecto: datetime) -> tuple:
        """(hasta, rehacer_desde). Lee y limpia el pedido de rehacer en una transaccion."""
        with conexion.cursor() as cur:
            cur.execute("INSERT INTO agregacion_progreso (nivel, hasta) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                        (nivel, por_defecto))
            cur.execute("SELECT hasta, rehacer_desde FROM agregacion_progreso WHERE nivel = %s FOR UPDATE",
                        (nivel,))
            hasta, rehacer = cur.fetchone()
            if rehacer is not None:
                cur.execute("UPDATE agregacion_progreso SET rehacer_desde = NULL WHERE nivel = %s", (nivel,))
        conexion.commit()
        return hasta, rehacer

    def _avanzar(self, conexion, nivel: str, hasta: datetime) -> None:
        with conexion.cursor() as cur:
            cur.execute("UPDATE agregacion_progreso SET hasta = GREATEST(hasta, %s), actualizado = now() "
                        "WHERE nivel = %s", (hasta, nivel))
        conexion.commit()

    # ---------------------------------------------------------------- ciclo
    def _nivel(self, conexion, nivel, origen, destino, paso, fin, ventana, tramo, limite_t) -> tuple:
        hasta, rehacer = self._progreso(conexion, nivel, fin - self.relleno)
        desde = min(hasta, fin - ventana, *( [rehacer] if rehacer else [] ))
        desde = max(desde, fin - self.relleno)          # nada mas viejo que lo retenido
        lineas = 0
        t = desde
        for a, b in tramos(desde, fin, tramo):
            lineas += resumir(self.cliente, origen, destino, paso, a, b)
            t = b
            self._avanzar(conexion, nivel, b)
            if time.monotonic() > limite_t and b < fin:
                log.info("%s: presupuesto agotado, sigue en el proximo ciclo desde %s", nivel, b)
                break
        return t, lineas

    def ciclo(self, ahora: Optional[datetime] = None) -> dict:
        inicio = time.monotonic()
        limite_t = inicio + self.presupuesto_s
        fijo = ahora is not None          # las pruebas fijan el reloj
        ahora = ahora or datetime.now(timezone.utc)
        fin_1m = _piso(ahora - LLEGADA, timedelta(minutes=1))
        with catalogo.conectar(self.pg_url) as c:
            hecho_1m, lineas_1m = self._nivel(c, "1m", "zenit_raw", "zenit_1m", "1 minute", fin_1m,
                                              VENTANA_1M, TRAMO_1M, limite_t)
            fin_1h = _piso(hecho_1m, timedelta(hours=1))
            hecho_1h, lineas_1h = self._nivel(c, "1h", "zenit_1m", "zenit_1h", "1 hour", fin_1h,
                                              VENTANA_1H, TRAMO_1H, limite_t + 10)
        cierre = ahora if fijo else datetime.now(timezone.utc)   # el retraso se mide al cerrar el ciclo
        resultado = {
            "hasta_1m": hecho_1m, "hasta_1h": hecho_1h, "lineas_1m": lineas_1m, "lineas_1h": lineas_1h,
            "retraso_1m_s": (cierre - hecho_1m).total_seconds(), "retraso_1h_s": (cierre - hecho_1h).total_seconds(),
            "duracion_s": round(time.monotonic() - inicio, 2),
        }
        self._registrar(resultado)
        return resultado

    def _registrar(self, r: dict) -> None:
        if not self.bd_interna:
            return
        t = time.time_ns()
        lineas = [
            construir_linea("zenit_agregacion_retraso_s", {"nivel": "1m"}, r["retraso_1m_s"], t),
            construir_linea("zenit_agregacion_retraso_s", {"nivel": "1h"}, r["retraso_1h_s"], t),
            construir_linea("zenit_agregacion_duracion_s", {"nivel": "ciclo"}, r["duracion_s"], t),
        ]
        try:
            self.cliente.escribir(self.bd_interna, "\n".join(l for l in lineas if l))
        except ErrorInflux as e:
            log.warning("no se pudo registrar el retraso: %s", e)


def principal(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    p = argparse.ArgumentParser(prog="python -m zenit.agregacion")
    p.add_argument("--cada", type=int, default=0, help="segundos entre ciclos; 0 = uno solo")
    args = p.parse_args(argv)
    url = os.environ.get("PG_URL")
    if not url or not os.environ.get("INFLUX_TOKEN"):
        print("faltan PG_URL o INFLUX_TOKEN", file=sys.stderr)
        return 2
    catalogo.esperar_pg(url)
    agregador = Agregador(
        ClienteInflux(os.environ.get("INFLUX_URL", "http://influxdb:8181"), os.environ["INFLUX_TOKEN"], 60.0),
        url,
        relleno=_dias(os.environ.get("AGREGACION_RELLENO", os.environ.get("RETENCION_RAW", "30d"))),
        presupuesto_s=max(10.0, (args.cada or 60) * 0.75),
    )
    while True:
        t0 = time.monotonic()
        try:
            r = agregador.ciclo()
            print(f"{datetime.now(timezone.utc):%H:%M:%S} 1m hasta {r['hasta_1m']:%H:%M} "
                  f"(retraso {r['retraso_1m_s']:.0f} s, {r['lineas_1m']} lineas) · "
                  f"1h hasta {r['hasta_1h']:%m-%d %H:%M} · {r['duracion_s']} s", flush=True)
        except Exception:  # noqa: BLE001 - un ciclo fallido se repite en el siguiente
            log.exception("ciclo fallido")
        if not args.cada:
            return 0
        time.sleep(max(1.0, args.cada - (time.monotonic() - t0)))


if __name__ == "__main__":
    sys.exit(principal())
