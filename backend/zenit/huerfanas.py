"""Asociacion de la telemetria huerfana (H-011, criterio 3).

Una senal que llega antes de que su activo este en el catalogo se guarda con
zenit_asset_id = sin_resolver. Cuando el catalogo ya tiene la clave (porque el
descubrimiento lo encontro o porque se admitio a mano), esa telemetria previa
se reasigna al activo en cada almacen. Lo corre el descubridor despues de cada
pasada, y a mano:

    docker compose exec descubridor python -m zenit.admin asociar-huerfanas
"""
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from zenit import catalogo
from zenit.influx import ClienteInflux
from zenit.repositorio.mantenimiento_influx import reasignar_sin_resolver

log = logging.getLogger("zenit.huerfanas")

# El agente reenvia desde su cola puntos con su marca de tiempo original, que
# puede ser anterior a la primera recepcion. Se busca desde un poco antes.
MARGEN = timedelta(hours=2)


def asociar(conexion, influx: Optional[ClienteInflux], bd: str = "zenit_raw", analitico=None) -> list:
    """Reasigna lo pendiente. Devuelve [(clave, activo, filas_series, filas_analitico)]."""
    hechas = []
    for clave, activo, tipo_clave, primera, _ultima in catalogo.pendientes_de_asociar(conexion):
        if tipo_clave != "host.name":
            # los almacenes guardan host_name; otra clave no se puede filtrar
            log.warning("%s llego por %s: se asocia a mano, no automaticamente", clave, tipo_clave)
            continue
        desde = primera - MARGEN
        hasta = datetime.now(timezone.utc) + timedelta(minutes=1)
        n_series = reasignar_sin_resolver(influx, bd, clave, activo, desde, hasta) if influx else 0
        n_analitico = analitico.reasignar_sin_resolver(clave, activo) if analitico else 0
        catalogo.retroceder_agregacion(conexion, desde)   # los resumenes se rehacen con el id nuevo
        catalogo.marcar_asociada(conexion, clave, activo)
        hechas.append((clave, activo, n_series, n_analitico))
        log.info("huerfana %s asociada a %s: %d filas de series, %d de registros y trazas",
                 clave, activo, n_series, n_analitico)
    return hechas


def asociar_desde_entorno(url: str) -> list:
    influx = None
    if os.environ.get("INFLUX_URL") and os.environ.get("INFLUX_TOKEN"):
        influx = ClienteInflux(os.environ["INFLUX_URL"], os.environ["INFLUX_TOKEN"], tiempo_limite=60.0)
    analitico = None
    if os.environ.get("CLICKHOUSE_URL"):
        from zenit.repositorio.analitico import RepositorioAnalitico

        analitico = RepositorioAnalitico.desde_entorno()
    with catalogo.conectar(url) as c:
        return asociar(c, influx, os.environ.get("INFLUX_BD", "zenit_raw"), analitico)
