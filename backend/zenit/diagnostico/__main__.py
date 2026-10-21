"""Servicio de diagnostico (H-040): `python -m zenit.diagnostico --cada 10`.

Cada ciclo busca incidentes recien detectados, de severidad media o alta,
que todavia no tienen diagnostico, y les genera la hipotesis. Corre cada 10 s
para que el diagnostico llegue en menos de 60 s desde la deteccion
(RNF-REN-06). Los de severidad baja se diagnostican a pedido, desde la
interfaz, para no gastar tokens en ruido.

Un incidente que falla tres veces seguidas no se reintenta en este proceso.
Sin version del modelo, sin clave o sin redaccion (H-041), el servicio
espera y lo dice en los registros; no envia nada.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time

log = logging.getLogger("zenit.diagnostico")

RECIENTES_MIN = 30
INTENTOS = 3


def pendientes(conexion, severidades: list[str], recientes_min: int = RECIENTES_MIN) -> list[int]:
    with conexion.cursor() as cur:
        cur.execute(
            "SELECT a.id FROM anomalias a WHERE a.estado = 'detectada' AND a.severidad = ANY(%s) "
            "AND a.detectada_en > now() - make_interval(mins => %s) "
            "AND NOT EXISTS (SELECT 1 FROM diagnosticos d WHERE d.anomalia_id = a.id) "
            "ORDER BY a.detectada_en",
            (severidades, recientes_min),
        )
        return [f[0] for f in cur.fetchall()]


def principal(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    p = argparse.ArgumentParser(prog="python -m zenit.diagnostico")
    p.add_argument("--cada", type=int, default=0, help="segundos entre ciclos; 0 = uno solo")
    p.add_argument("--severidades", default=os.environ.get("ZENIT_DIAGNOSTICO_SEVERIDADES", "alta,media"))
    args = p.parse_args(argv)

    from zenit import catalogo
    from zenit.deteccion.__main__ import _desde_entorno
    from zenit.diagnostico.hipotesis import SinRedaccion, cargar_redactor, diagnosticar
    from zenit.diagnostico.modelo import ClienteAnthropic, ErrorModelo, SinConfiguracion

    repositorio, pg_url = _desde_entorno()
    if repositorio is None:
        print("faltan PG_URL o INFLUX_TOKEN", file=sys.stderr)
        return 2
    analitico = None
    if os.environ.get("CLICKHOUSE_URL"):
        from zenit.repositorio.analitico import RepositorioAnalitico

        analitico = RepositorioAnalitico.desde_entorno()
    severidades = [s.strip() for s in args.severidades.split(",") if s.strip()]
    fallos: dict[int, int] = {}

    while True:
        t0 = time.monotonic()
        try:
            cliente = ClienteAnthropic.desde_entorno()
            redactor = cargar_redactor()
            with catalogo.conectar(pg_url) as conexion:
                for id_ in pendientes(conexion, severidades):
                    if fallos.get(id_, 0) >= INTENTOS:
                        continue
                    try:
                        diagnosticar(conexion, id_, repositorio, analitico, cliente, redactor=redactor)
                        fallos.pop(id_, None)
                    except ErrorModelo as e:
                        conexion.rollback()
                        fallos[id_] = fallos.get(id_, 0) + 1
                        log.warning("incidente %s sin diagnostico (intento %s de %s): %s",
                                    id_, fallos[id_], INTENTOS, e)
        except (SinConfiguracion, SinRedaccion) as e:
            log.warning("diagnostico en espera: %s", e)
        except Exception:  # noqa: BLE001 - el servicio sigue; el error queda en los registros
            log.exception("fallo un ciclo del diagnostico")
        if not args.cada:
            return 0
        time.sleep(max(1.0, args.cada - (time.monotonic() - t0)))


if __name__ == "__main__":
    sys.exit(principal())
