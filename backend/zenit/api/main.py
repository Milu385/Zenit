"""API de consulta de Zenit (H-006).

Solo habla con la capa de repositorio; no conoce el motor. La especificacion
OpenAPI se genera sola y queda en /api/docs y /api/openapi.json, que es lo que
pide el criterio 4 de H-006.

La autorizacion por rol (RNF-SEG-04) llega con la gobernanza. Mientras tanto
la API no publica puerto: solo la alcanza el borde, y el borde solo admite las
IP del equipo.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from zenit.influx import ClienteInflux, ErrorInflux
from zenit.repositorio import ErrorConsulta, RepositorioSeries


def _utc(momento: Optional[datetime], por_defecto: datetime) -> datetime:
    if momento is None:
        return por_defecto
    if momento.tzinfo is None:
        return momento.replace(tzinfo=timezone.utc)
    return momento.astimezone(timezone.utc)


def _rango(desde: Optional[datetime], hasta: Optional[datetime]):
    fin = _utc(hasta, datetime.now(timezone.utc))
    inicio = _utc(desde, fin - timedelta(hours=1))
    return inicio, fin


def _filtros(crudos: list) -> dict:
    salida = {}
    for f in crudos:
        clave, separador, valor = f.partition(":")
        if not separador or not clave:
            raise HTTPException(422, detail=f"filtro mal formado {f!r}: se espera dimension:valor")
        salida[clave] = valor
    return salida


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def _activo_publico(a: dict) -> dict:
    """La forma del contrato (seccion 3, Activos). El estado de salud lo
    completa H-021; aqui solo se distingue retirado."""
    return {
        "id": a["id"], "nombre": a["nombre"], "proveedor": a["proveedor"], "region": a["region"],
        "entorno": a["entorno"], "estado": "retirado" if a["estado"] == "retirado" else "sin_datos",
        "origen": a["origen"], "visto": _iso(a["visto"]), "retirado_en": _iso(a["retirado_en"]),
        "claves": [c for c in a["claves"] if ":" not in c],                 # host.name: lo que llega en la telemetria
        "ids_nativos": [c for c in a["claves"] if ":" in c],                 # proveedor:tipo/id
    }


def crear_app(repositorio: RepositorioSeries, ruta_catalogo: Path, pg_url: Optional[str] = None) -> FastAPI:
    app = FastAPI(
        title="Zenit",
        version="0.2.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )

    @app.exception_handler(ErrorConsulta)
    async def _consulta_invalida(_, e: ErrorConsulta):
        return JSONResponse(status_code=422, content={"detail": str(e)})

    @app.exception_handler(ErrorInflux)
    async def _almacen_caido(_, e: ErrorInflux):
        return JSONResponse(status_code=503, content={"detail": "el almacen de series no respondio", "causa": str(e)})

    @app.get("/api/salud", tags=["sistema"])
    def salud():
        return {"estado": "ok", "almacen": repositorio.disponible()}

    def _catalogo_pg() -> list:
        from zenit import catalogo

        try:
            with catalogo.conectar(pg_url) as c:
                return catalogo.listar_activos(c)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(503, detail=f"el catalogo no respondio: {type(e).__name__}")

    @app.get("/api/activos", tags=["activos"])
    def activos():
        """Activos del catalogo (H-009), con las claves por las que se resuelven."""
        if pg_url:
            return [_activo_publico(a) for a in _catalogo_pg()]
        try:
            datos = json.loads(Path(ruta_catalogo).read_text())
        except FileNotFoundError:
            return []
        except ValueError:
            raise HTTPException(500, detail="el catalogo no es JSON valido")
        agrupados: dict = {}
        for clave, activo in datos.items():
            agrupados.setdefault(str(activo), []).append(str(clave))
        return [{"id": a, "claves": sorted(c)} for a, c in sorted(agrupados.items())]

    @app.get("/api/activos/{activo}", tags=["activos"])
    def activo_por_id(activo: str):
        if not pg_url:
            raise HTTPException(404, detail="sin catalogo en PostgreSQL")
        for a in _catalogo_pg():
            if a["id"] == activo:
                return _activo_publico(a)
        raise HTTPException(404, detail=f"no existe el activo {activo!r}")

    @app.get("/api/catalogo/descubrimientos", tags=["activos"])
    def descubrimientos():
        """Ultima ejecucion del descubrimiento por proveedor y las mas recientes (H-009, criterio 5)."""
        if not pg_url:
            return {"ultimas": [], "recientes": []}
        from zenit import catalogo

        try:
            with catalogo.conectar(pg_url) as c:
                datos = catalogo.ejecuciones(c)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(503, detail=f"el catalogo no respondio: {type(e).__name__}")
        return {k: [{c: _iso(v) for c, v in f.items()} for f in filas] for k, filas in datos.items()}

    @app.get("/api/activos/{activo}/metricas", tags=["series"])
    def metricas(activo: str):
        """Metricas con datos de este activo en la ultima hora."""
        return {"activo": activo, "metricas": repositorio.metricas_de(activo)}

    @app.get("/api/activos/{activo}/metricas/{metrica}/dimensiones", tags=["series"])
    def dimensiones(activo: str, metrica: str,
                    desde: Optional[datetime] = None, hasta: Optional[datetime] = None):
        """Dimensiones propias de la metrica (cpu, state, device...) y sus valores."""
        inicio, fin = _rango(desde, hasta)
        return {"activo": activo, "metrica": metrica,
                "dimensiones": repositorio.dimensiones(activo, metrica, inicio, fin)}

    @app.get("/api/activos/{activo}/series/{metrica}", tags=["series"])
    def serie(
        activo: str,
        metrica: str,
        desde: Optional[datetime] = Query(None, description="ISO 8601. Por defecto, una hora antes de 'hasta'."),
        hasta: Optional[datetime] = Query(None, description="ISO 8601. Por defecto, ahora."),
        agrupar: Optional[str] = Query(None, description="Dimension por la que se separan las lineas, p. ej. state."),
        filtro: list[str] = Query([], description="dimension:valor, repetible. P. ej. cpu:cpu0"),
    ):
        """Serie de una metrica para un activo y un rango, promediada cada 10 s."""
        inicio, fin = _rango(desde, hasta)
        r = repositorio.serie(activo, metrica, inicio, fin, agrupar=agrupar or None, filtros=_filtros(filtro))
        return {
            "activo": r.activo,
            "metrica": r.metrica,
            "resolucion": r.resolucion.value,
            "desde": r.desde.isoformat(),
            "hasta": r.hasta.isoformat(),
            "agrupar": r.agrupar,
            "series": [{"etiquetas": dict(s.etiquetas), "puntos": s.puntos} for s in r.series],
            "intervalos_esperados": r.intervalos_esperados,
            "intervalos_vacios": r.intervalos_vacios,
            "series_omitidas": r.series_omitidas,
        }

    return app


def _desde_entorno() -> FastAPI:
    from zenit.repositorio.influx import RepositorioInflux

    cliente = ClienteInflux(os.environ.get("INFLUX_URL", "http://influxdb:8181"), os.environ.get("INFLUX_TOKEN", ""))
    return crear_app(
        RepositorioInflux(cliente, os.environ.get("INFLUX_BD", "zenit_raw")),
        Path(os.environ.get("RUTA_CATALOGO", "/etc/zenit/catalogo/catalogo.json")),
        pg_url=os.environ.get("PG_URL") or None,
    )


app = _desde_entorno()
