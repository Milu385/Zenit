"""Operaciones de mantenimiento sobre InfluxDB que no son consultas de la API.

Viven en repositorio/ por la regla de RNF-MAN-01: el SQL contra el motor de
series solo se escribe aqui y en admin.py.
"""
import logging
from datetime import datetime, timedelta, timezone

from zenit.esquema import COLUMNA_TIEMPO, COLUMNA_VALOR, SIN_RESOLVER, es_identificador
from zenit.influx import ClienteInflux
from zenit.protocolo_linea import construir_linea

log = logging.getLogger("zenit.repositorio.mantenimiento")

_ESQUEMAS_DE_SISTEMA = "('information_schema', 'system')"


def tablas_con(cliente: ClienteInflux, bd: str, *columnas: str) -> dict:
    """{tabla: [columnas]} de las tablas que tienen todas las columnas pedidas."""
    filas = cliente.consultar(
        bd,
        "SELECT table_name, column_name FROM information_schema.columns "
        f"WHERE table_schema NOT IN {_ESQUEMAS_DE_SISTEMA}",
    )
    tablas: dict = {}
    for f in filas:
        if es_identificador(f["table_name"]):
            tablas.setdefault(f["table_name"], []).append(f["column_name"])
    return {t: c for t, c in sorted(tablas.items()) if all(x in c for x in columnas)}


def _literal(momento: datetime) -> str:
    momento = momento.astimezone(timezone.utc).replace(tzinfo=None)
    return f"TIMESTAMP '{momento.strftime('%Y-%m-%d %H:%M:%S.%f')}'"


def _tramos(desde: datetime, hasta: datetime, paso: timedelta = timedelta(hours=6)):
    t = desde
    while t < hasta:
        yield t, min(t + paso, hasta)
        t += paso


def reasignar_sin_resolver(cliente: ClienteInflux, bd: str, host_name: str, activo: str,
                           desde: datetime, hasta: datetime, lineas_por_lote: int = 5000) -> int:
    """Copia las filas huerfanas de un origen al activo que ahora lo resuelve (H-011).

    InfluxDB 3 Core no tiene UPDATE ni DELETE de filas. Se reescribe cada fila
    con las mismas etiquetas, la misma marca de tiempo y zenit_asset_id
    cambiado; las filas con sin_resolver quedan, y la vista de huerfanas las
    muestra como asociadas. Repetirlo es inofensivo: el motor trata una fila
    con la misma serie y el mismo instante como la misma fila.

    Devuelve cuantas filas escribio.
    """
    total = 0
    for tabla, columnas in tablas_con(cliente, bd, "zenit_asset_id", "host_name", COLUMNA_VALOR).items():
        etiquetas = [c for c in columnas if c not in (COLUMNA_TIEMPO, COLUMNA_VALOR) and es_identificador(c)]
        for d, h in _tramos(desde, hasta):
            filas = cliente.consultar(
                bd,
                f'SELECT *, CAST(time AS BIGINT) AS time_ns FROM "{tabla}" '
                f"WHERE \"zenit_asset_id\" = '{SIN_RESOLVER}' AND \"host_name\" = $origen "
                f"AND time >= {_literal(d)} AND time < {_literal(h)}",
                {"origen": host_name},
            )
            lote = []
            for f in filas:
                if f.get(COLUMNA_VALOR) is None:
                    continue
                tags = {c: f[c] for c in etiquetas if f.get(c) is not None}
                tags["zenit_asset_id"] = activo
                linea = construir_linea(tabla, tags, float(f[COLUMNA_VALOR]), int(f["time_ns"]))
                if linea:
                    lote.append(linea)
                if len(lote) >= lineas_por_lote:
                    cliente.escribir(bd, "\n".join(lote))
                    total += len(lote)
                    lote = []
            if lote:
                cliente.escribir(bd, "\n".join(lote))
                total += len(lote)
    log.info("%s: %d filas reasignadas a %s", host_name, total, activo)
    return total
