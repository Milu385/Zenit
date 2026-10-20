"""Punto de entrada del detector (H-032).

    python -m zenit.deteccion --cada 60        lo corre el servicio detector
    python -m zenit.deteccion                   un ciclo y termina
    python -m zenit.deteccion --senales         revisa el catalogo de senales, sin escribir nada

Un ciclo fallido queda en los registros y se repite en el siguiente, igual
que en el agregador: el servicio no se cae por un error de un activo.

`--senales` es el equivalente de `--probar` del descubridor: dice que senales
de `senales.py` existen de verdad en esta plataforma y cuales hay que
corregir, sin tocar la tabla `anomalias`.
"""
import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone

from zenit import catalogo
from zenit.deteccion.detector import Detector

log = logging.getLogger("zenit.deteccion")


def _desde_entorno():
    """(repositorio, PG_URL) o (None, None) si falta configuracion.

    La configuracion se revisa antes de importar el cliente del motor: sin
    PG_URL no hay nada que hacer, y asi el mensaje de error no depende de que
    las dependencias del repositorio esten instaladas.
    """
    url = os.environ.get("PG_URL")
    if not url or not os.environ.get("INFLUX_TOKEN"):
        return None, None

    from zenit.influx import ClienteInflux
    from zenit.repositorio.influx import RepositorioInflux

    cliente = ClienteInflux(os.environ.get("INFLUX_URL", "http://influxdb:8181"),
                            os.environ["INFLUX_TOKEN"])
    return RepositorioInflux(cliente, os.environ.get("INFLUX_BD", "zenit_raw")), url


def _informe_senales(detector: Detector) -> int:
    informe = detector.revisar_catalogo()
    faltan = 0
    for fila in informe["filas"]:
        ok = fila["estado"] == "ok"
        faltan += not ok
        print(f"{'ok   ' if ok else 'FALTA'} {fila['activo']:28} {fila['senal']:58} {fila['estado']}")

    # El otro sentido de la revision: un nombre equivocado en el catalogo
    # aparece arriba como "falta la metrica" y aqui, al lado, con el nombre
    # que de verdad esta llegando.
    if informe["sin_vigilar"]:
        print("\nMetricas que llegan y ninguna senal vigila:")
        for activo, metricas in informe["sin_vigilar"].items():
            print(f"  {activo}: {', '.join(metricas)}")

    if faltan:
        print(f"\n{faltan} senales por corregir en zenit/deteccion/senales.py")
    else:
        print("\ntodas las senales del catalogo existen en la plataforma")
    return 1 if faltan else 0


def principal(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)   # una linea por peticion no dice nada
    p = argparse.ArgumentParser(prog="python -m zenit.deteccion")
    p.add_argument("--cada", type=int, default=0, help="segundos entre ciclos; 0 = uno solo")
    p.add_argument("--senales", action="store_true",
                   help="revisa que senales del catalogo existen en la plataforma y sale")
    args = p.parse_args(argv)

    repositorio, url = _desde_entorno()
    if repositorio is None:
        print("faltan PG_URL o INFLUX_TOKEN", file=sys.stderr)
        return 2
    catalogo.esperar_pg(url)
    detector = Detector(repositorio, url)

    if args.senales:
        return _informe_senales(detector)

    while True:
        t0 = time.monotonic()
        try:
            r = detector.ciclo()
            degradados = f", {r['degradados']} con la fuente callada" if r["degradados"] else ""
            print(f"{datetime.now(timezone.utc):%H:%M:%S} {r['activos']} activos{degradados}, "
                  f"{r['senales']} senales, {r['anomalias_abiertas']} abiertas, "
                  f"{r['anomalias_cerradas']} cerradas", flush=True)
        except Exception:  # noqa: BLE001 - un ciclo fallido se repite en el siguiente
            log.exception("ciclo fallido")
        if not args.cada:
            return 0
        time.sleep(max(1.0, args.cada - (time.monotonic() - t0)))


if __name__ == "__main__":
    sys.exit(principal())
