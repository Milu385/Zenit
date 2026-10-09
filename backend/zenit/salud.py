"""Salud de las fuentes de telemetria (H-021, RF-ING-10, RF-VIS-08).

Dos registros, escritos por la ingesta cada 10 s:

- PostgreSQL, tabla `fuentes`: ultima recepcion y acumulados por origen y
  senal. Es lo que decide si una fuente esta degradada, y no depende de
  consultar el motor de series.
- InfluxDB, base `zenit_interno` (la cuarta de las cinco que admite Core):
  `zenit_fuente_recibidas` y `zenit_fuente_errores` cada 10 s, y los
  indicadores de la propia ingesta (`zenit_ingesta_*`). Es el volumen reciente
  y su historia. Un problema de la plataforma no se mezcla con los datos de un
  activo observado porque vive en otra base.

La regla de degradado esta aqui y en ningun otro lado: mas de 5 minutos sin
recibir metricas. Antes la aproximaba la interfaz comparando el ultimo punto
con el reloj; esa regla queda reemplazada.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

log = logging.getLogger("zenit.salud")

DEGRADADA_TRAS = timedelta(minutes=5)
SENAL_LATIDO = "metricas"     # las metricas del anfitrion llegan cada 10 s pase lo que pase
BD_INTERNA = "zenit_interno"


class AcumuladorFuentes:
    """Cuentas por (activo, host_name, senal) entre dos volcados."""

    def __init__(self):
        self._candado = threading.Lock()
        self._d: dict = {}

    def sumar(self, etiquetas: dict, senal: str, recibidas: int, errores: int = 0) -> None:
        if not recibidas and not errores:
            return
        clave = (etiquetas["zenit_asset_id"], etiquetas["host_name"], senal)
        ahora = datetime.now(timezone.utc)
        with self._candado:
            d = self._d.get(clave)
            if d is None:
                d = self._d[clave] = {"proveedor": etiquetas.get("cloud_provider"), "recibidas": 0,
                                      "errores": 0, "primera": ahora, "ultima": ahora}
            d["recibidas"] += recibidas
            d["errores"] += errores
            if recibidas:
                d["ultima"] = ahora

    def tomar(self) -> dict:
        with self._candado:
            d, self._d = self._d, {}
        return d

    def devolver(self, deltas: dict) -> None:
        with self._candado:
            for k, d in deltas.items():
                a = self._d.get(k)
                if a is None:
                    self._d[k] = d
                else:
                    a["recibidas"] += d["recibidas"]
                    a["errores"] += d["errores"]
                    a["primera"] = min(a["primera"], d["primera"])


# ------------------------------------------------------------------ escritura
def guardar_pg(conexion, deltas: dict) -> None:
    with conexion.cursor() as cur:
        for (activo, host, senal), d in deltas.items():
            cur.execute(
                "INSERT INTO fuentes (activo, host_name, senal, proveedor, primera, ultima, recibidas, errores) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (activo, host_name, senal) DO UPDATE SET "
                "proveedor = COALESCE(EXCLUDED.proveedor, fuentes.proveedor), "
                "ultima = GREATEST(fuentes.ultima, EXCLUDED.ultima), "
                "recibidas = fuentes.recibidas + EXCLUDED.recibidas, errores = fuentes.errores + EXCLUDED.errores",
                (activo, host, senal, d["proveedor"], d["primera"], d["ultima"], d["recibidas"], d["errores"]),
            )
    conexion.commit()


def lineas_internas(deltas: dict, indicadores: dict, momento_ns: int) -> list:
    """Protocolo de linea para zenit_interno."""
    from zenit.protocolo_linea import construir_linea

    lineas = []
    for (activo, host, senal), d in deltas.items():
        etiquetas = {"zenit_asset_id": activo, "host_name": host, "senal": senal,
                     "cloud_provider": d["proveedor"] or "desconocido"}
        lineas.append(construir_linea("zenit_fuente_recibidas", etiquetas, float(d["recibidas"]), momento_ns))
        if d["errores"]:
            lineas.append(construir_linea("zenit_fuente_errores", etiquetas, float(d["errores"]), momento_ns))
    for nombre, valor in indicadores.items():
        if valor is not None:
            lineas.append(construir_linea(f"zenit_ingesta_{nombre}", {"servicio": "ingesta"}, float(valor), momento_ns))
    return [l for l in lineas if l]


# ------------------------------------------------------------------ lectura (API)
def fuentes_pg(conexion) -> list:
    columnas = ("activo", "host_name", "senal", "proveedor", "primera", "ultima", "recibidas", "errores")
    with conexion.cursor() as cur:
        cur.execute(f"SELECT {', '.join(columnas)} FROM fuentes ORDER BY activo, host_name, senal")
        return [dict(zip(columnas, f)) for f in cur.fetchall()]


def incidentes_abiertos(conexion) -> dict:
    """{activo: n} de anomalias sin cerrar ni descartar."""
    try:
        with conexion.cursor() as cur:
            cur.execute("SELECT activo, count(*) FROM anomalias WHERE fin IS NULL "
                        "AND estado NOT IN ('cerrada', 'falso_positivo') GROUP BY activo")
            return dict(cur.fetchall())
    except Exception:  # noqa: BLE001 - la tabla existe desde la epica 2, pero no se depende de ella
        conexion.rollback()
        return {}


def estado_de(ultima: Optional[datetime], ahora: datetime) -> str:
    if ultima is None:
        return "sin_datos"
    return "degradado" if ahora - ultima > DEGRADADA_TRAS else "ok"


def agrupar_fuentes(filas: list, volumen: dict, ahora: datetime) -> list:
    """Una entrada por (activo, host_name) con sus senales.

    volumen: {(activo, host, senal): {"recibidas_5min": n, "errores_1h": n}} del motor.
    """
    salida: dict = {}
    for f in filas:
        k = (f["activo"], f["host_name"])
        e = salida.setdefault(k, {"activo": f["activo"], "host_name": f["host_name"], "proveedor": f["proveedor"],
                                  "huerfana": f["activo"] == "sin_resolver", "senales": {}})
        v = volumen.get((f["activo"], f["host_name"], f["senal"]), {})
        e["senales"][f["senal"]] = {
            "ultima_recepcion": f["ultima"], "estado": estado_de(f["ultima"], ahora),
            "recibidas_total": f["recibidas"], "errores_total": f["errores"],
            "recibidas_5min": v.get("recibidas_5min", 0), "errores_1h": v.get("errores_1h", 0),
        }
    for e in salida.values():
        latido = e["senales"].get(SENAL_LATIDO) or max(e["senales"].values(), key=lambda s: s["ultima_recepcion"])
        e["ultima_recepcion"] = latido["ultima_recepcion"]
        e["estado"] = latido["estado"]
        e["minutos_sin_datos"] = round((ahora - latido["ultima_recepcion"]).total_seconds() / 60, 1)
    return list(salida.values())


def ultima_recepcion_por_activo(filas: list) -> dict:
    """{activo: ultima recepcion de metricas}, o de cualquier senal si no tiene metricas."""
    latido: dict = {}
    cualquiera: dict = {}
    for f in filas:
        d = latido if f["senal"] == SENAL_LATIDO else cualquiera
        d[f["activo"]] = max(d.get(f["activo"], f["ultima"]), f["ultima"])
    return {**cualquiera, **latido}


def estado_activo(catalogo: dict, ultima: Optional[datetime], incidentes: int, ahora: datetime) -> str:
    """ok | atencion | degradado | sin_datos | retirado (contrato, seccion 3)."""
    if catalogo.get("estado") == "retirado":
        return "retirado"
    salud = estado_de(ultima, ahora)
    if salud != "ok":
        return salud
    return "atencion" if incidentes else "ok"
