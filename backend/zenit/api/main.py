"""API de consulta de Zenit (H-006).

Solo habla con la capa de repositorio; no conoce el motor. La especificacion
OpenAPI se genera sola y queda en /api/docs y /api/openapi.json, que es lo que
pide el criterio 4 de H-006.

Toda ruta exige sesion (Authorization: Bearer) y declara el permiso que
necesita (RNF-SEG-04): ver zenit/api/autorizacion.py y la matriz en
zenit/gobernanza/permisos.py, que sigue la seccion 2 del contrato v1.1. Ademas,
la API no publica puerto: solo la alcanza el borde, y el borde solo admite las
IP del equipo.
"""
import json
import logging
import os
import secrets
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from zenit.api.autorizacion import autenticada, crear_autorizador, identidad, publica, requiere
from zenit.gobernanza import claves, configuraciones, costos
from zenit.gobernanza.permisos import permisos_de
from zenit.gobernanza.sesiones import Intentos, Sesiones
from zenit.gobernanza.usuarios import RepositorioUsuarios, UsuariosMemoria
from zenit.influx import ClienteInflux, ErrorInflux
from zenit.repositorio import ErrorConsulta, RepositorioSeries

log = logging.getLogger("zenit.api")


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


class PeticionSesion(BaseModel):
    usuario: str = Field(min_length=1, max_length=120)
    clave: str = Field(min_length=1, max_length=200)


def _leer_catalogo(ruta: Path) -> dict:
    try:
        datos = json.loads(Path(ruta).read_text())
    except FileNotFoundError:
        return {}
    except ValueError:
        raise HTTPException(500, detail="el catalogo no es JSON valido")
    return {str(k): str(v) for k, v in datos.items()}


def crear_app(
    repositorio: RepositorioSeries,
    ruta_catalogo: Path,
    *,
    usuarios: Optional[RepositorioUsuarios] = None,
    secreto_sesion: Optional[bytes] = None,
    instantaneas: Optional[configuraciones.RepositorioInstantaneas] = None,
    politica: Optional[configuraciones.Politica] = None,
    facturas: Optional[costos.RepositorioCostos] = None,
    token_nodos: str = "",
) -> FastAPI:
    usuarios = usuarios if usuarios is not None else UsuariosMemoria()
    if secreto_sesion is None:
        # Sin secreto fijo las sesiones no sobreviven a un reinicio de la API,
        # pero nunca quedan firmadas con un valor conocido.
        log.warning("ZENIT_SECRETO_SESION no esta definido: las sesiones caducan al reiniciar la API")
        secreto_sesion = secrets.token_bytes(32)
    sesiones = Sesiones(secreto_sesion)
    intentos = Intentos()
    instantaneas = instantaneas if instantaneas is not None else configuraciones.InstantaneasMemoria()
    politica = politica or configuraciones.Politica()
    facturas = facturas if facturas is not None else costos.CostosMemoria()

    app = FastAPI(
        title="Zenit",
        version="0.4.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
        dependencies=[Depends(crear_autorizador(sesiones, usuarios, token_nodos))],
    )

    @app.exception_handler(ErrorConsulta)
    async def _consulta_invalida(_, e: ErrorConsulta):
        return JSONResponse(status_code=422, content={"detail": str(e)})

    @app.exception_handler(ErrorInflux)
    async def _almacen_caido(_, e: ErrorInflux):
        return JSONResponse(status_code=503, content={"detail": "el almacen de series no respondio", "causa": str(e)})

    @app.get("/api/salud", tags=["sistema"])
    @publica
    def salud():
        return {"estado": "ok", "almacen": repositorio.disponible()}

    @app.get("/api/activos", tags=["activos"])
    @requiere("activos:ver")
    def activos():
        """Activos del catalogo, con las claves nativas por las que se resuelven."""
        datos = _leer_catalogo(ruta_catalogo)
        agrupados: dict = {}
        for clave, activo in datos.items():
            agrupados.setdefault(str(activo), []).append(str(clave))
        return [{"id": a, "claves": sorted(c)} for a, c in sorted(agrupados.items())]

    @app.get("/api/activos/{activo}/metricas", tags=["series"])
    @requiere("activos:ver")
    def metricas(activo: str):
        """Metricas con datos de este activo en la ultima hora."""
        return {"activo": activo, "metricas": repositorio.metricas_de(activo)}

    @app.get("/api/activos/{activo}/metricas/{metrica}/dimensiones", tags=["series"])
    @requiere("activos:ver")
    def dimensiones(activo: str, metrica: str,
                    desde: Optional[datetime] = None, hasta: Optional[datetime] = None):
        """Dimensiones propias de la metrica (cpu, state, device...) y sus valores."""
        inicio, fin = _rango(desde, hasta)
        return {"activo": activo, "metrica": metrica,
                "dimensiones": repositorio.dimensiones(activo, metrica, inicio, fin)}

    @app.get("/api/activos/{activo}/series/{metrica}", tags=["series"])
    @requiere("activos:ver")
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

    # ------------------------------------------------------------ sesion

    @app.post("/api/sesion", tags=["sesion"])
    @publica
    def iniciar_sesion(datos: PeticionSesion):
        """Entrega el token que va en `Authorization: Bearer` (contrato, seccion 3)."""
        if intentos.bloqueado(datos.usuario):
            raise HTTPException(429, detail="demasiados intentos fallidos; espera unos minutos")
        u = usuarios.obtener(datos.usuario)
        # se verifica aunque el usuario no exista, para que tarde lo mismo
        valida = claves.verificar(datos.clave, u.clave_hash if u else None)
        if u is None or not valida:
            intentos.fallo(datos.usuario)
            # el mismo mensaje exista o no el usuario
            raise HTTPException(401, detail="usuario o clave incorrectos")
        intentos.exito(datos.usuario)
        token, sesion = sesiones.emitir(u.usuario)
        return {
            "token": token,
            "usuario": u.usuario,
            "rol": u.rol,
            "expira": datetime.fromtimestamp(sesion.expira, timezone.utc).isoformat(),
        }

    @app.get("/api/yo", tags=["sesion"])
    @autenticada
    def yo(request: Request):
        i = identidad(request)
        return {"usuario": i.usuario, "rol": i.rol, "permisos": permisos_de(i.rol)}

    # ---------------------------------------------------- configuraciones

    @app.post("/api/gobernanza/configuraciones/instantaneas", tags=["gobernanza"], status_code=201)
    @requiere("configuraciones:enviar")
    async def recibir_instantanea(request: Request):
        """Fuera del contrato v1.1 (propuesto en NOTAS.md). La envia
        scripts/instantanea-nodo.sh con el token de nodos (TOKEN_INSTANTANEAS)
        o la sesion de un administrador."""
        cuerpo = await request.body()
        if len(cuerpo) > 1024 * 1024:
            raise HTTPException(413, detail="la instantanea pasa de 1 MB")
        try:
            i = configuraciones.leer_instantanea(json.loads(cuerpo), _leer_catalogo(ruta_catalogo))
        except ValueError as e:
            raise HTTPException(422, detail=str(e))
        instantaneas.guardar(i)
        return {"nodo": i.nodo, "activo": i.activo, "tomada_en": i.tomada_en.isoformat()}

    @app.get("/api/gobernanza/configuraciones", tags=["gobernanza"])
    @requiere("configuraciones:ver")
    def reporte_configuraciones():
        """Hallazgos de las reglas sobre la ultima instantanea de cada nodo."""
        catalogo = _leer_catalogo(ruta_catalogo)
        ultimas = [
            i if i.activo else configuraciones.Instantanea(**{**i.__dict__, "activo": catalogo.get(i.nodo)})
            for i in instantaneas.ultimas()
        ]
        return [h.__dict__ for h in configuraciones.evaluar(ultimas, politica)]

    # -------------------------------------------------------------- costos

    @app.post("/api/gobernanza/costos/facturas", tags=["gobernanza"], status_code=201)
    @requiere("costos:importar")
    async def importar_factura(request: Request, proveedor: str = Query(..., description="digitalocean")):
        """Fuera del contrato v1.1 (propuesto en NOTAS.md). Importa la factura
        exportada por el proveedor, el CSV tal cual en el cuerpo. Importar dos
        veces el mismo archivo no duplica los cargos."""
        cuerpo = await request.body()
        try:
            f = costos.importar(cuerpo, proveedor, _leer_catalogo(ruta_catalogo), identidad(request).usuario)
        except costos.FacturaInvalida as e:
            raise HTTPException(422, detail=str(e))
        return costos.resumen_importacion(f, facturas.guardar(f))

    @app.get("/api/gobernanza/costos", tags=["gobernanza"])
    @requiere("costos:ver")
    def reporte_costos(desde: Optional[date] = None, hasta: Optional[date] = None):
        """Costo atribuido a cada activo; lo no asignable va aparte. Sin desde ni
        hasta, todo lo importado."""
        cargos = facturas.cargos()
        por_defecto = costos.periodo_por_defecto(cargos)
        inicio, fin = desde or por_defecto[0], hasta or por_defecto[1]
        if inicio > fin:
            raise HTTPException(422, detail="desde no puede ser posterior a hasta")
        return costos.reporte(cargos, inicio, fin)

    return app


def _desde_entorno() -> FastAPI:
    from zenit.gobernanza.configuraciones import InstantaneasPg
    from zenit.gobernanza.costos import CostosPg
    from zenit.gobernanza.usuarios import UsuariosPg
    from zenit.repositorio.influx import RepositorioInflux

    cliente = ClienteInflux(os.environ.get("INFLUX_URL", "http://influxdb:8181"), os.environ.get("INFLUX_TOKEN", ""))
    pg = os.environ.get("PG_URL")
    secreto = os.environ.get("ZENIT_SECRETO_SESION", "")
    ruta_politica = os.environ.get("RUTA_POLITICA", "/etc/zenit/gobernanza/politica.json")
    return crear_app(
        RepositorioInflux(cliente, os.environ.get("INFLUX_BD", "zenit_raw")),
        Path(os.environ.get("RUTA_CATALOGO", "/etc/zenit/catalogo/catalogo.json")),
        usuarios=UsuariosPg(pg) if pg else None,
        secreto_sesion=bytes.fromhex(secreto) if secreto else None,
        instantaneas=InstantaneasPg(pg) if pg else None,
        politica=configuraciones.Politica.desde_archivo(Path(ruta_politica)),
        facturas=CostosPg(pg) if pg else None,
        token_nodos=os.environ.get("TOKEN_INSTANTANEAS", ""),
    )


app = _desde_entorno()
