"""Evidencia de las tres senales de un incidente (H-039, RF-DIA-01, RF-ALM-07).

Para la ventana de un incidente reune, del mismo activo:

1. **La serie afectada**: la metrica que se desvio, filtrada por las
   dimensiones exactas de la anomalia (por ejemplo `device=sda1`,
   `direction=write`), con sus puntos y un resumen antes y durante.
2. **Las metricas relacionadas**: las senales del detector que miran el mismo
   tipo de fallo (`fallas` en `zenit.deteccion.senales`). Un disco que se llena
   trae la escritura en disco y la espera de CPU; una CPU saturada trae la
   carga y los procesos en ejecucion. Solo el resumen, no los puntos.
3. **Registros de error** (severidad 17 o mas) y **trazas con error** del
   activo en la ventana.
4. **La correlacion** entre registros y trazas, que sale de una consulta al
   motor analitico (`RepositorioAnalitico.correlacion`), no de una union hecha
   aqui.

La evidencia existe siempre: si un almacen no responde, lo que falta queda
anotado en `faltantes` y lo demas se devuelve igual (P-DIA-05 empieza aqui).

Es lo que H-040 le entrega al modelo (despues de redactarlo, H-041) y lo que
queda guardado con el diagnostico para poder verificarlo despues.
"""
from __future__ import annotations

import logging
import statistics
from datetime import datetime, timedelta, timezone
from typing import Optional

from zenit.deteccion.senales import ACUMULADA, SENALES, Senal, a_tasa

log = logging.getLogger("zenit.diagnostico")

# Cuanto antes del inicio se mira: la linea base del detector usa 60 minutos.
ANTES = timedelta(minutes=60)
# Contexto previo para registros y trazas: lo que paso justo antes de abrirse.
ANTES_REGISTROS = timedelta(minutes=10)
# Cuanto despues del fin: la recuperacion tambien dice algo.
DESPUES = timedelta(minutes=10)
# Tope de la parte "durante": una anomalia abierta horas no necesita horas de puntos.
DURANTE_MAXIMO = timedelta(hours=3)

PUNTOS_MAXIMOS = 180      # serie afectada; se submuestrea de forma pareja
SERIES_POR_SENAL = 3      # relacionadas: las que mas cambiaron
REGISTROS_MAXIMOS = 50
TRAZAS_MAXIMAS = 20
CORRELACION_MAXIMA = 50
SEVERIDAD_ERROR = 17      # OpenTelemetry: 17 a 20 es ERROR


# ------------------------------------------------------------------ senales
def _coincide(senal: Senal, metrica: str, dimensiones: dict) -> bool:
    return senal.metrica == metrica and all(dimensiones.get(k) == v for k, v in senal.filtros.items())


def senal_de(metrica: str, dimensiones: Optional[dict]) -> Optional[Senal]:
    """La senal del detector que produjo una anomalia de esta serie, si hay una."""
    dimensiones = dimensiones or {}
    candidatas = [s for s in SENALES if _coincide(s, metrica, dimensiones)]
    # la mas especifica primero (mas filtros que coinciden)
    candidatas.sort(key=lambda s: len(s.filtros), reverse=True)
    return candidatas[0] if candidatas else None


def senales_relacionadas(metrica: str, dimensiones: Optional[dict]) -> list[Senal]:
    """Las otras senales que miran el mismo tipo de fallo que la de la anomalia."""
    propia = senal_de(metrica, dimensiones)
    if propia is None or not propia.fallas:
        return []
    return [s for s in SENALES
            if s is not propia and set(s.fallas) & set(propia.fallas)
            and not (s.metrica == propia.metrica and dict(s.filtros) == dict(propia.filtros))]


def metricas_relacionadas(metrica: str, dimensiones: Optional[dict]) -> list[str]:
    """Nombres de las metricas relacionadas, sin repetir y sin la propia, para
    `evidencia.metricas_relacionadas` del contrato."""
    vistas: list[str] = []
    for s in senales_relacionadas(metrica, dimensiones):
        if s.metrica != metrica and s.metrica not in vistas:
            vistas.append(s.metrica)
    return vistas


# ------------------------------------------------------------------ ventana
def _utc(v: Optional[datetime]) -> Optional[datetime]:
    if v is None:
        return None
    return v.replace(tzinfo=timezone.utc) if v.tzinfo is None else v.astimezone(timezone.utc)


def ventanas(inicio: datetime, fin: Optional[datetime], ahora: datetime) -> dict:
    """Las tres ventanas de la evidencia.

    - `metricas`: desde una hora antes (lo normal) hasta el fin mas 10 minutos.
    - `registros`: desde 10 minutos antes hasta el mismo final.
    - `durante`: el tramo de la anomalia, para el resumen antes/durante.
    Si sigue abierta, el fin es ahora. Nada pasa de ahora ni de 3 horas despues del inicio.
    """
    inicio, ahora = _utc(inicio), _utc(ahora)
    fin_efectivo = min(_utc(fin) or ahora, ahora, inicio + DURANTE_MAXIMO)
    hasta = min(fin_efectivo + DESPUES, ahora)
    if hasta <= inicio:
        hasta = inicio + timedelta(minutes=1)
    return {
        "metricas": (inicio - ANTES, hasta),
        "registros": (inicio - ANTES_REGISTROS, hasta),
        "durante": (inicio, max(fin_efectivo, inicio + timedelta(minutes=1))),
    }


# ------------------------------------------------------------------ series
def _a_momento(valor) -> datetime:
    if isinstance(valor, datetime):
        return _utc(valor)
    return datetime.fromtimestamp(valor / 1000, timezone.utc)


def _puntos(serie, senal: Optional[Senal]) -> list:
    puntos = [(_a_momento(t), float(v)) for t, v in serie.puntos if v is not None]
    puntos.sort(key=lambda p: p[0])
    if senal is not None and senal.clase == ACUMULADA:
        puntos = a_tasa(puntos)
    return puntos


def _resumen(puntos: list, inicio: datetime, fin: datetime) -> dict:
    antes = [v for t, v in puntos if t < inicio]
    durante = [v for t, v in puntos if inicio <= t <= fin]
    r = {
        "antes_mediana": statistics.median(antes) if antes else None,
        "durante_mediana": statistics.median(durante) if durante else None,
        "durante_min": min(durante) if durante else None,
        "durante_max": max(durante) if durante else None,
        "puntos_antes": len(antes),
        "puntos_durante": len(durante),
    }
    # El cambio se mide contra el maximo, no contra la mediana: el detector
    # solo mira subidas, y la mediana de "durante" se diluye con los minutos
    # de normalidad que el cierre exige antes de cerrar (en Azure, la
    # anomalia 29 tenia 0,07 % de cambio por mediana y 177 % por maximo).
    # El cambio absoluto queda aunque la linea base sea cero (CPU wait).
    base, pico, mediana = r["antes_mediana"], r["durante_max"], r["durante_mediana"]
    r["cambio_absoluto"] = pico - base if base is not None and pico is not None else None
    r["cambio_relativo"] = (pico - base) / abs(base) if base not in (None, 0) and pico is not None else None
    r["cambio_relativo_mediana"] = ((mediana - base) / abs(base)
                                    if base not in (None, 0) and mediana is not None else None)
    return r


def _submuestrear(puntos: list, maximo: int = PUNTOS_MAXIMOS) -> list:
    """Reduce la serie a `maximo` puntos sin perder los picos.

    Se parte la serie en `maximo` tramos seguidos y de cada uno queda el punto
    mas alto, con su momento. Tomar un punto de cada tantos (lo que hacia
    antes) podia saltarse justo el pico que abrio la anomalia: en Azure, la
    520 (red de Kaderno) se abrio por minutos con rafagas de 10 s de unos
    700 B/s, y la serie que vio el modelo solo tenia los valores de 373.
    El detector solo mira subidas, asi que el maximo es lo que hay que
    conservar. El resumen (`_resumen`) se calcula sobre la serie completa.
    """
    if len(puntos) <= maximo:
        return puntos
    paso = len(puntos) / maximo
    salida = []
    for i in range(maximo):
        tramo = puntos[int(i * paso):max(int((i + 1) * paso), int(i * paso) + 1)]
        salida.append(max(tramo, key=lambda p: p[1]))
    return salida


def _serie_afectada(repositorio, activo, metrica, dimensiones, v) -> dict:
    senal = senal_de(metrica, dimensiones)
    desde, hasta = v["metricas"]
    r = repositorio.serie(activo, metrica, desde, hasta, filtros=dimensiones or None)
    puntos = _puntos(r.series[0], senal) if r.series else []
    salida = {
        "metrica": metrica,
        "dimensiones": dimensiones or {},
        "papel": "afectada",
        "clase": senal.clase if senal else None,
        "unidad": "por segundo" if senal is not None and senal.clase == ACUMULADA else None,
        "resolucion_s": r.intervalo_s,
        "resumen": _resumen(puntos, *v["durante"]),
        "puntos": [[t.isoformat(), val] for t, val in _submuestrear(puntos)],
    }
    return salida


def _serie_relacionada(repositorio, activo, senal: Senal, v) -> list:
    desde, hasta = v["metricas"]
    r = repositorio.serie(activo, senal.metrica, desde, hasta, agrupar=senal.agrupar,
                          filtros=dict(senal.filtros) or None)
    salida = []
    for serie in r.series:
        puntos = _puntos(serie, senal)
        if not puntos:
            continue
        salida.append({
            "metrica": senal.metrica,
            "dimensiones": {**dict(senal.filtros), **dict(serie.etiquetas)},
            "papel": "relacionada",
            "clase": senal.clase,
            "unidad": "por segundo" if senal.clase == ACUMULADA else None,
            "resolucion_s": r.intervalo_s,
            "resumen": _resumen(puntos, *v["durante"]),
        })
    # las que mas cambiaron primero; sin cambio relativo medible (base cero), por el absoluto
    salida.sort(key=lambda s: (abs(s["resumen"]["cambio_relativo"] or 0), abs(s["resumen"]["cambio_absoluto"] or 0)),
                reverse=True)
    return salida[:SERIES_POR_SENAL]


# ------------------------------------------------------------------ todo junto
def recuperar(anomalia: dict, repositorio, analitico=None, ahora: Optional[datetime] = None) -> dict:
    """La evidencia de las tres senales para una fila de `anomalias`.

    `anomalia` necesita `id`, `activo`, `metrica`, `dimensiones`, `inicio` y
    `fin`. `repositorio` es la capa de series (nunca InfluxDB directo) y
    `analitico` el repositorio de registros y trazas, que puede faltar.
    """
    from zenit.clickhouse import ErrorClickHouse
    from zenit.influx import ErrorAlmacen
    from zenit.repositorio import ErrorConsulta

    ahora = _utc(ahora) or datetime.now(timezone.utc)
    activo, metrica = anomalia["activo"], anomalia["metrica"]
    dimensiones = anomalia.get("dimensiones") or {}
    v = ventanas(anomalia["inicio"], anomalia.get("fin"), ahora)
    faltantes: list[str] = []

    metricas: list[dict] = []
    try:
        metricas.append(_serie_afectada(repositorio, activo, metrica, dimensiones, v))
    except (ErrorAlmacen, ErrorConsulta) as e:
        faltantes.append(f"serie afectada: {e}")
    for senal in senales_relacionadas(metrica, dimensiones):
        try:
            metricas.extend(_serie_relacionada(repositorio, activo, senal, v))
        except (ErrorAlmacen, ErrorConsulta) as e:
            faltantes.append(f"{senal.clave}: {e}")

    registros, trazas, correlacion, conteos = [], [], [], None
    if analitico is None:
        faltantes.append("registros y trazas: el almacen analitico no esta configurado")
    else:
        desde, hasta = v["registros"]
        try:
            registros = analitico.registros(activo, desde, hasta, severidad_minima=SEVERIDAD_ERROR,
                                            limite=REGISTROS_MAXIMOS)
            trazas = analitico.trazas(activo, desde, hasta, solo_errores=True, limite=TRAZAS_MAXIMAS)
            filas = analitico.correlacion(activo, desde, hasta, limite=CORRELACION_MAXIMA * 4)
            correlacion = [f for f in filas
                           if f.get("estado_span") == "error"
                           or str(f.get("severidad") or "").upper().startswith(("ERROR", "FATAL"))][:CORRELACION_MAXIMA]
            conteos = analitico.resumen(activo, desde, hasta)
        except (ErrorClickHouse, ErrorConsulta) as e:
            faltantes.append(f"registros y trazas: {e}")

    return {
        "incidente": anomalia.get("id"),
        "activo": activo,
        "metrica": metrica,
        "dimensiones": dimensiones,
        "recuperada": ahora.isoformat(),
        "ventana": {"desde": v["metricas"][0].isoformat(), "hasta": v["metricas"][1].isoformat()},
        "ventana_registros": {"desde": v["registros"][0].isoformat(), "hasta": v["registros"][1].isoformat()},
        "metricas": metricas,
        "metricas_relacionadas": metricas_relacionadas(metrica, dimensiones),
        "registros_error": registros,
        "trazas_error": trazas,
        "correlacion": correlacion,
        "conteos": conteos,
        "faltantes": faltantes,
    }
