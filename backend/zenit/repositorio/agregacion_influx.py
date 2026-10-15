"""SQL del trabajo de agregacion (H-018). Vive en repositorio/ por RNF-MAN-01.

Dos pasos, siempre sobre intervalos completos y alineados:

- zenit_raw -> zenit_1m: por tabla y por serie (todas sus etiquetas), avg,
  min, max y count de `value` en cada minuto;
- zenit_1m -> zenit_1h: el promedio ponderado por la cuenta, el minimo de
  los minimos, el maximo de los maximos y la suma de las cuentas.

Las etiquetas son las mismas en los tres niveles; los campos no (el esquema
de medicion, seccion 6). Esa asimetria la absorbe RepositorioInflux.

Idempotencia: cada resumen se escribe con la marca de tiempo del inicio de su
intervalo y las mismas etiquetas de la serie. Para el motor, reescribirlo es
reemplazarlo, asi que repetir un intervalo no duplica nada.

Incompleto: un minuto con menos de 6 observaciones (10 s) tuvo perdida. No se
descarta ni se rellena; queda con su value_count real, y la capa de
repositorio lo informa como intervalo incompleto.
"""
import logging
from datetime import datetime, timedelta, timezone

from zenit.esquema import COLUMNA_TIEMPO, COLUMNA_VALOR, es_identificador
from zenit.influx import ClienteInflux
from zenit.protocolo_linea import construir_linea_campos

from .mantenimiento_influx import tablas_con

log = logging.getLogger("zenit.agregacion")

CAMPOS_RESUMEN = ("value_avg", "value_min", "value_max", "value_count")
OBSERVACIONES_POR_MINUTO = 6        # a 10 s
OBSERVACIONES_POR_HORA = 360


def _literal(momento: datetime) -> str:
    momento = momento.astimezone(timezone.utc).replace(tzinfo=None)
    return f"TIMESTAMP '{momento.strftime('%Y-%m-%d %H:%M:%S')}'"


def _etiquetas(columnas: list) -> list:
    fuera = {COLUMNA_TIEMPO, COLUMNA_VALOR, *CAMPOS_RESUMEN}
    return sorted(c for c in columnas if c not in fuera and es_identificador(c))


def _a_lineas(tabla: str, etiquetas: list, filas: list) -> list:
    lineas = []
    for f in filas:
        if f.get("n") in (None, 0):
            continue
        tags = {c: f[c] for c in etiquetas if f.get(c) is not None}
        linea = construir_linea_campos(
            tabla, tags,
            {"value_avg": f.get("prom"), "value_min": f.get("mini"), "value_max": f.get("maxi"),
             "value_count": f.get("n")},
            int(f["t"]),
        )
        if linea:
            lineas.append(linea)
    return lineas


def _escribir(cliente: ClienteInflux, bd: str, lineas: list, por_lote: int = 5000) -> int:
    for i in range(0, len(lineas), por_lote):
        cliente.escribir(bd, "\n".join(lineas[i:i + por_lote]))
    return len(lineas)


def resumir(cliente: ClienteInflux, origen: str, destino: str, paso: str,
            desde: datetime, hasta: datetime) -> int:
    """Resume [desde, hasta) de cada tabla de `origen` en `destino`.

    paso: '1 minute' (de crudo a minuto) o '1 hour' (de minuto a hora).
    Devuelve cuantas lineas escribio.
    """
    if paso not in ("1 minute", "1 hour"):
        raise ValueError(paso)
    desde_crudo = origen == "zenit_raw"
    requeridas = (COLUMNA_VALOR,) if desde_crudo else ("value_avg", "value_count")
    total = 0
    for tabla, columnas in tablas_con(cliente, origen, "zenit_asset_id", *requeridas).items():
        etiquetas = _etiquetas(columnas)
        grupo = ", ".join(f'"{c}"' for c in etiquetas)
        if desde_crudo:
            medidas = ('avg("value") AS prom, min("value") AS mini, max("value") AS maxi, '
                       'CAST(count("value") AS DOUBLE) AS n')
        else:
            medidas = ('sum("value_avg" * "value_count") / sum("value_count") AS prom, '
                       'min("value_min") AS mini, max("value_max") AS maxi, sum("value_count") AS n')
        sql = (
            f"SELECT CAST(date_bin(INTERVAL '{paso}', time, TIMESTAMP '1970-01-01 00:00:00') AS BIGINT) AS t, "
            f"{grupo + ', ' if grupo else ''}{medidas} FROM \"{tabla}\" "
            f"WHERE time >= {_literal(desde)} AND time < {_literal(hasta)} "
            f"GROUP BY 1{', ' + grupo if grupo else ''}"
        )
        filas = cliente.consultar(origen, sql)
        total += _escribir(cliente, destino, _a_lineas(tabla, etiquetas, filas))
    return total


def tramos(desde: datetime, hasta: datetime, paso: timedelta):
    t = desde
    while t < hasta:
        yield t, min(t + paso, hasta)
        t += paso
