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

from zenit import salud as salud_fuentes
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


def _serializar(v):
    if isinstance(v, dict):
        return {k: _serializar(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_serializar(x) for x in v]
    return _iso(v)


def _activo_publico(a: dict, salud_activo: Optional[dict] = None) -> dict:
    """La forma del contrato (seccion 3, Activos)."""
    s = salud_activo or {}
    return {
        "id": a["id"], "nombre": a["nombre"], "proveedor": a["proveedor"], "region": a["region"],
        "entorno": a["entorno"], "estado": s.get("estado", "retirado" if a["estado"] == "retirado" else "sin_datos"),
        "ultima_recepcion": _iso(s.get("ultima_recepcion")),
        "incidentes_abiertos": s.get("incidentes_abiertos", 0),
        "cpu_reciente": s.get("cpu_reciente"),
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

    def _pg(funcion):
        import psycopg

        from zenit import catalogo

        try:
            with catalogo.conectar(pg_url) as c:
                return funcion(c)
        except (psycopg.OperationalError, OSError) as e:
            raise HTTPException(503, detail=f"PostgreSQL no respondio: {type(e).__name__}")

    def _catalogo_pg() -> list:
        from zenit import catalogo

        return _pg(catalogo.listar_activos)

    def _inventario() -> list:
        from zenit import catalogo

        def leer(c):
            return catalogo.listar_activos(c), salud_fuentes.fuentes_pg(c), salud_fuentes.incidentes_abiertos(c)

        lista, fuentes, incidentes = _pg(leer)
        ultimas = salud_fuentes.ultima_recepcion_por_activo(fuentes)
        try:
            cpu = repositorio.cpu_reciente()
        except ErrorInflux:
            cpu = {}           # el inventario se muestra aunque el motor no responda
        ahora = datetime.now(timezone.utc)
        salida = []
        for a in lista:
            datos = {"ultima_recepcion": ultimas.get(a["id"]), "incidentes_abiertos": incidentes.get(a["id"], 0),
                     "cpu_reciente": cpu.get(a["id"])}
            datos["estado"] = salud_fuentes.estado_activo(a, datos["ultima_recepcion"], datos["incidentes_abiertos"], ahora)
            salida.append(_activo_publico(a, datos))
        return salida

    @app.get("/api/activos", tags=["activos"])
    def activos():
        """Inventario: activos del catalogo (H-009) con su estado (H-021).

        estado: ok | atencion (incidentes abiertos) | degradado (mas de 5 min
        sin recibir metricas) | sin_datos (nunca reporto) | retirado."""
        if pg_url:
            return _inventario()
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
        for a in _inventario():
            if a["id"] == activo:
                return a
        raise HTTPException(404, detail=f"no existe el activo {activo!r}")

    @app.get("/api/catalogo/descubrimientos", tags=["activos"])
    def descubrimientos():
        """Ultima ejecucion del descubrimiento por proveedor y las mas recientes (H-009, criterio 5)."""
        if not pg_url:
            return {"ultimas": [], "recientes": []}
        from zenit import catalogo

        datos = _pg(catalogo.ejecuciones)
        return {k: [{c: _iso(v) for c, v in f.items()} for f in filas] for k, filas in datos.items()}

    @app.get("/api/fuentes", tags=["fuentes"])
    def fuentes():
        """Cada fuente con su ultima recepcion, su volumen reciente y sus errores (H-021).

        Una fuente con mas de 5 minutos sin recibir metricas queda `degradado`.
        Los activos del catalogo que nunca reportaron aparecen como `sin_datos`."""
        if not pg_url:
            return []
        from zenit import catalogo

        filas, lista = _pg(lambda c: (salud_fuentes.fuentes_pg(c), catalogo.listar_activos(c, incluir_retirados=False)))
        try:
            volumen = repositorio.volumen_fuentes()
        except ErrorInflux:
            volumen = {}
        ahora = datetime.now(timezone.utc)
        salida = salud_fuentes.agrupar_fuentes(filas, volumen, ahora)
        con_fuente = {f["activo"] for f in salida}
        for a in lista:
            if a["id"] not in con_fuente:
                salida.append({"activo": a["id"], "host_name": a["nombre"], "proveedor": a["proveedor"],
                               "huerfana": False, "senales": {}, "ultima_recepcion": None,
                               "estado": "sin_datos", "minutos_sin_datos": None})
        orden = {"degradado": 0, "sin_datos": 1, "ok": 2}
        salida.sort(key=lambda f: (orden[f["estado"]], f["activo"], f["host_name"]))
        return [_serializar(f) for f in salida]

    @app.get("/api/fuentes/huerfanas", tags=["fuentes"])
    def huerfanas():
        """Origenes de telemetria que llegaron sin poder asociarse a un activo (H-011).

        `resuelve_a` dice si la clave ya esta en el catalogo (asociable);
        `asociada_a`, si la telemetria previa ya se reasigno."""
        if not pg_url:
            return []
        from zenit import catalogo

        filas = _pg(catalogo.listar_huerfanas)
        return [{k: _iso(v) for k, v in f.items()} for f in filas]

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
        """Serie de una metrica para un activo y un rango.

        Hasta 24 h, promedios de 10 s sobre los datos crudos. Hasta 30 dias, sobre el
        resumen por minuto; mas de 30 dias, sobre el resumen por hora (H-019). En
        los resumenes cada punto abarca `intervalo_s` segundos. `resolucion` dice
        que nivel se uso."""
        inicio, fin = _rango(desde, hasta)
        r = repositorio.serie(activo, metrica, inicio, fin, agrupar=agrupar or None, filtros=_filtros(filtro))
        # RF-VIS-08: la grafica avisa si la fuente esta degradada
        ultima = None
        if pg_url:
            try:
                ultima = _pg(lambda c: salud_fuentes.ultima_recepcion_por_activo(salud_fuentes.fuentes_pg(c))).get(activo)
            except HTTPException:
                ultima = None    # sin PostgreSQL la serie se sirve igual, sin el aviso
        degradada = salud_fuentes.estado_de(ultima, datetime.now(timezone.utc)) == "degradado"
        return {
            "fuente_degradada": degradada,
            "ultima_recepcion": _iso(ultima),
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
            "intervalo_s": r.intervalo_s,
            "intervalos_incompletos": r.intervalos_incompletos,
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
