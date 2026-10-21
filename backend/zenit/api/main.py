"""API de consulta de Zenit (H-006).

Solo habla con la capa de repositorio; no conoce el motor. La especificacion
OpenAPI se genera sola y queda en /api/docs y /api/openapi.json, que es lo que
pide el criterio 4 de H-006.

Toda ruta exige sesion (Authorization: Bearer) y declara el permiso que
necesita (RNF-SEG-04): ver zenit/api/autorizacion.py y la matriz en
zenit/gobernanza/permisos.py, que sigue la seccion 2 del contrato v1.2. Ademas,
la API no publica puerto: solo la alcanza el borde, y el borde solo admite las
IP del equipo.
"""
import json
import logging
import os
import secrets
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from zenit import incidentes
from zenit import salud as salud_fuentes
from zenit.clickhouse import ErrorClickHouse
from zenit.api.autorizacion import autenticada, crear_autorizador, identidad, publica, requiere
from zenit.gobernanza import claves, configuraciones, costos, subutilizados
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


class PeticionSesion(BaseModel):
    usuario: str = Field(min_length=1, max_length=120)
    clave: str = Field(min_length=1, max_length=200)


class PeticionEstado(BaseModel):
    """Las dos transiciones manuales de un incidente (H-037); otro valor es 422."""
    estado: Literal["confirmada", "falso_positivo"]


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
    pg_url: Optional[str] = None,
    analitico=None,
    *,
    usuarios: Optional[RepositorioUsuarios] = None,
    secreto_sesion: Optional[bytes] = None,
    instantaneas: Optional[configuraciones.RepositorioInstantaneas] = None,
    politica: Optional[configuraciones.Politica] = None,
    facturas: Optional[costos.RepositorioCostos] = None,
    token_nodos: str = "",
    cliente_modelo=None,
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

    @app.exception_handler(ErrorClickHouse)
    async def _analitico_caido(_, e: ErrorClickHouse):
        return JSONResponse(status_code=503, content={"detail": "el almacen de registros y trazas no respondio",
                                                      "causa": str(e)})

    @app.exception_handler(ErrorInflux)
    async def _almacen_caido(_, e: ErrorInflux):
        return JSONResponse(status_code=503, content={"detail": "el almacen de series no respondio", "causa": str(e)})

    @app.get("/api/salud", tags=["sistema"])
    @publica
    def salud():
        respuesta = {"estado": "ok", "almacen": repositorio.disponible()}
        if analitico is not None:
            respuesta["analitico"] = analitico.disponible()
        return respuesta

    def _pg(funcion):
        import psycopg

        from zenit import catalogo

        try:
            with catalogo.conectar(pg_url) as c:
                return funcion(c)
        except (psycopg.OperationalError, OSError) as e:
            raise HTTPException(503, detail=f"PostgreSQL no respondio: {type(e).__name__}")

    def _claves() -> dict:
        """{clave nativa: activo} para la gobernanza. Con PostgreSQL es el
        catalogo de la epica 1 (H-009); sin el, el catalogo.json de respaldo."""
        if pg_url:
            from zenit import catalogo

            return _pg(catalogo.mapa_claves)
        return _leer_catalogo(ruta_catalogo)

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
    @requiere("activos:ver")
    def activos():
        """Inventario: activos del catalogo (H-009) con su estado (H-021).

        estado: ok | atencion (incidentes abiertos) | degradado (mas de 5 min
        sin recibir metricas) | sin_datos (nunca reporto) | retirado."""
        if pg_url:
            return _inventario()
        datos = _leer_catalogo(ruta_catalogo)
        agrupados: dict = {}
        for clave, activo in datos.items():
            agrupados.setdefault(str(activo), []).append(str(clave))
        return [{"id": a, "claves": sorted(c)} for a, c in sorted(agrupados.items())]

    @app.get("/api/activos/{activo}", tags=["activos"])
    @requiere("activos:ver")
    def activo_por_id(activo: str):
        if not pg_url:
            raise HTTPException(404, detail="sin catalogo en PostgreSQL")
        for a in _inventario():
            if a["id"] == activo:
                return a
        raise HTTPException(404, detail=f"no existe el activo {activo!r}")

    @app.get("/api/catalogo/descubrimientos", tags=["activos"])
    @requiere("fuentes:ver")
    def descubrimientos():
        """Ultima ejecucion del descubrimiento por proveedor y las mas recientes (H-009, criterio 5)."""
        if not pg_url:
            return {"ultimas": [], "recientes": []}
        from zenit import catalogo

        datos = _pg(catalogo.ejecuciones)
        return {k: [{c: _iso(v) for c, v in f.items()} for f in filas] for k, filas in datos.items()}

    @app.get("/api/fuentes", tags=["fuentes"])
    @requiere("fuentes:ver")
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
    @requiere("fuentes:ver")
    def huerfanas():
        """Origenes de telemetria que llegaron sin poder asociarse a un activo (H-011).

        `resuelve_a` dice si la clave ya esta en el catalogo (asociable);
        `asociada_a`, si la telemetria previa ya se reasigno."""
        if not pg_url:
            return []
        from zenit import catalogo

        filas = _pg(catalogo.listar_huerfanas)
        return [{k: _iso(v) for k, v in f.items()} for f in filas]

    # ---------------------------------------------------------------- incidentes (H-038)
    @app.get("/api/incidentes", tags=["incidentes"])
    @requiere("incidentes:ver")
    def listar_incidentes(
        estado: Optional[str] = Query(None, description="detectada | diagnosticada | confirmada | falso_positivo | cerrada"),
        activo: Optional[str] = None,
        desde: Optional[datetime] = Query(None, description="incidentes que seguian abiertos en este momento o despues"),
        hasta: Optional[datetime] = Query(None, description="incidentes que empezaron antes de este momento"),
    ):
        """Incidentes ordenados por severidad (alta primero) y luego por inicio, el mas reciente primero (H-038).

        Un incidente es una anomalia escrita por el detector. `tiene_diagnostico`
        dice si ya hay una hipotesis generada; la evidencia esta en el detalle."""
        if estado is not None and estado not in incidentes.ESTADOS:
            raise HTTPException(422, detail=f"estado desconocido {estado!r}: se espera uno de {', '.join(incidentes.ESTADOS)}")
        if desde is not None and hasta is not None and _utc(desde, desde) > _utc(hasta, hasta):
            raise HTTPException(422, detail="desde es posterior a hasta")
        if not pg_url:
            return []
        return _pg(lambda c: incidentes.listar(c, estado=estado, activo=activo, desde=desde, hasta=hasta))

    @app.get("/api/incidentes/{id_incidente}", tags=["incidentes"])
    @requiere("incidentes:ver")
    def detalle_incidente(id_incidente: int):
        """Un incidente con su evidencia y su diagnostico mas reciente (H-038).

        La evidencia esta siempre, aunque el diagnostico no exista o haya
        fallado; en ese caso `diagnostico` es null."""
        encontrado = _pg(lambda c: incidentes.obtener(c, id_incidente)) if pg_url else None
        if encontrado is None:
            raise HTTPException(404, detail=f"no existe el incidente {id_incidente}")
        return encontrado

    @app.get("/api/incidentes/{id_incidente}/evidencia", tags=["incidentes"])
    @requiere("incidentes:ver")
    def evidencia_incidente(id_incidente: int):
        """Lo que paso en metricas, registros y trazas en la ventana del incidente (H-039).

        La serie afectada con sus dimensiones exactas, las metricas relacionadas
        con el mismo tipo de fallo, los registros de error, las trazas con error
        y la correlacion entre ambos, que sale de una consulta al motor
        analitico. Si un almacen no responde, `faltantes` dice que no se pudo
        recuperar y el resto se devuelve igual."""
        from zenit.diagnostico.evidencia import recuperar

        fila = _pg(lambda c: incidentes.anomalia(c, id_incidente)) if pg_url else None
        if fila is None:
            raise HTTPException(404, detail=f"no existe el incidente {id_incidente}")
        return _serializar(recuperar(fila, repositorio, analitico))

    @app.post("/api/incidentes/{id_incidente}/diagnostico", tags=["incidentes"])
    @requiere("incidentes:gestionar")
    def pedir_diagnostico(id_incidente: int):
        """Genera o regenera la hipotesis del incidente, en el momento (H-040).

        Responde 200 con el diagnostico. Si el proveedor del modelo falla, 502,
        y el incidente y su evidencia quedan como estaban (P-DIA-05). Sin
        configuracion del modelo o sin la redaccion de H-041, 503: no se envia
        nada sin redactar."""
        from zenit.diagnostico.hipotesis import SinRedaccion, diagnosticar
        from zenit.diagnostico.modelo import ErrorModelo, SinConfiguracion

        if not pg_url:
            raise HTTPException(404, detail=f"no existe el incidente {id_incidente}")
        if cliente_modelo is None:
            raise HTTPException(503, detail="el diagnostico no esta configurado "
                                            "(ZENIT_MODELO_DIAGNOSTICO y ANTHROPIC_API_KEY)")
        try:
            d = _pg(lambda c: diagnosticar(c, id_incidente, repositorio, analitico, cliente_modelo))
        except (SinConfiguracion, SinRedaccion) as e:
            raise HTTPException(503, detail=str(e))
        except ErrorModelo as e:
            raise HTTPException(502, detail=f"no se pudo generar el diagnostico: {e}")
        if d is None:
            raise HTTPException(404, detail=f"no existe el incidente {id_incidente}")
        return d

    @app.post("/api/incidentes/{id_incidente}/estado", tags=["incidentes"])
    @requiere("incidentes:gestionar")
    def marcar_incidente(id_incidente: int, datos: PeticionEstado, request: Request):
        """Confirma un incidente o lo marca como falso positivo (H-037).

        Son las dos unicas transiciones manuales; el resto las hace el detector.
        Guarda quien y cuando en `marcada_por` y `marcada_en`. Un incidente
        cerrado o ya descartado no cambia (409). Responde el incidente con la
        forma de la lista."""
        if not pg_url:
            raise HTTPException(404, detail=f"no existe el incidente {id_incidente}")
        r = _pg(lambda c: incidentes.marcar(c, id_incidente, datos.estado, identidad(request).usuario))
        if r is None:
            raise HTTPException(404, detail=f"no existe el incidente {id_incidente}")
        if r is incidentes.TERMINAL:
            raise HTTPException(409, detail=f"el incidente {id_incidente} ya esta cerrado o descartado")
        return r

    # ---------------------------------------------------------------- registros y trazas
    def _analitico():
        if analitico is None:
            raise HTTPException(503, detail="registros y trazas no estan configurados (CLICKHOUSE_URL)")
        return analitico

    @app.get("/api/activos/{activo}/registros", tags=["registros y trazas"])
    @requiere("registros:ver")
    def registros(activo: str, desde: Optional[datetime] = None, hasta: Optional[datetime] = None,
                  severidad_minima: int = Query(0, ge=0, le=24, description="17 = error, 13 = aviso, 9 = info"),
                  texto: Optional[str] = Query(None, max_length=200), servicio: Optional[str] = None,
                  limite: int = Query(200, ge=1, le=2000)):
        """Registros del activo en la ventana, del mas reciente al mas viejo (H-016)."""
        inicio, fin = _rango(desde, hasta)
        filas = _analitico().registros(activo, inicio, fin, severidad_minima=severidad_minima, texto=texto,
                                       servicio=servicio, limite=limite)
        return {"activo": activo, "desde": inicio.isoformat(), "hasta": fin.isoformat(), "registros": filas}

    @app.get("/api/activos/{activo}/trazas", tags=["registros y trazas"])
    @requiere("registros:ver")
    def trazas(activo: str, desde: Optional[datetime] = None, hasta: Optional[datetime] = None,
               solo_errores: bool = False, limite: int = Query(100, ge=1, le=2000)):
        """Trazas que tocaron el activo en la ventana, resumidas por traza (H-017)."""
        inicio, fin = _rango(desde, hasta)
        filas = _analitico().trazas(activo, inicio, fin, solo_errores=solo_errores, limite=limite)
        return {"activo": activo, "desde": inicio.isoformat(), "hasta": fin.isoformat(), "trazas": filas}

    @app.get("/api/trazas/{traza_id}", tags=["registros y trazas"])
    @requiere("registros:ver")
    def traza(traza_id: str):
        """Todos los spans de una traza, en cualquier activo que haya cruzado."""
        spans = _analitico().traza(traza_id)
        if not spans:
            raise HTTPException(404, detail="no hay spans con ese traza_id en los ultimos 7 dias")
        return {"traza_id": traza_id, "spans": spans}

    @app.get("/api/activos/{activo}/correlacion", tags=["registros y trazas"])
    @requiere("registros:ver")
    def correlacion(activo: str, desde: Optional[datetime] = None, hasta: Optional[datetime] = None,
                    limite: int = Query(200, ge=1, le=2000)):
        """Las senales del activo en una ventana (RF-ALM-07): conteos y los
        registros unidos con el span que los produjo, por el mismo identificador
        canonico. Es la evidencia que usa el diagnostico."""
        inicio, fin = _rango(desde, hasta)
        a = _analitico()
        return {"activo": activo, "desde": inicio.isoformat(), "hasta": fin.isoformat(),
                "resumen": a.resumen(activo, inicio, fin), "registros": a.correlacion(activo, inicio, fin, limite)}

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
        # desactivado (H-044): igual que una clave mala, para no revelar que existe
        if u is None or not u.activo or not valida:
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
            i = configuraciones.leer_instantanea(json.loads(cuerpo), _claves())
        except ValueError as e:
            raise HTTPException(422, detail=str(e))
        instantaneas.guardar(i)
        return {"nodo": i.nodo, "activo": i.activo, "tomada_en": i.tomada_en.isoformat()}

    @app.get("/api/gobernanza/configuraciones", tags=["gobernanza"])
    @requiere("configuraciones:ver")
    def reporte_configuraciones():
        """Hallazgos de las reglas sobre la ultima instantanea de cada nodo."""
        catalogo = _claves()
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
            f = costos.importar(cuerpo, proveedor, _claves(), identidad(request).usuario)
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

    @app.get("/api/gobernanza/costos/analisis", tags=["gobernanza"])
    @requiere("costos:ver")
    def analisis_costos(desde: Optional[date] = None, hasta: Optional[date] = None):
        """Fuera del contrato v1.2 (H-047, NOTAS.md). Subutilizados: el costo de
        cada activo cruzado con su CPU y memoria del resumen por hora, con el
        criterio en la respuesta. Tendencia: el gasto por mes, por entorno y
        por activo. No recomienda apagar ni redimensionar nada."""
        cargos = facturas.cargos()
        por_defecto = costos.periodo_por_defecto(cargos)
        inicio, fin = desde or por_defecto[0], hasta or por_defecto[1]
        if inicio > fin:
            raise HTTPException(422, detail="desde no puede ser posterior a hasta")
        entornos = {}
        if pg_url:
            entornos = {a["id"]: a["entorno"] for a in _catalogo_pg()}
        # el uso se mide como mucho en los ultimos 180 dias del periodo (lo que retiene zenit_1h)
        uso_hasta = datetime.combine(fin + timedelta(days=1), datetime.min.time(), timezone.utc)
        uso_desde = max(datetime.combine(inicio, datetime.min.time(), timezone.utc), uso_hasta - timedelta(days=180))
        try:
            uso, uso_disponible = repositorio.uso_por_hora(uso_desde, min(uso_hasta, datetime.now(timezone.utc))), True
        except ErrorInflux:
            uso, uso_disponible = {}, False
        r = subutilizados.analizar(cargos, uso, entornos, inicio, fin)
        r["uso"] = {"desde": uso_desde.isoformat(), "hasta": uso_hasta.isoformat(), "disponible": uso_disponible}
        return r

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
        pg_url=pg or None,
        analitico=_analitico_desde_entorno(),
        usuarios=UsuariosPg(pg) if pg else None,
        secreto_sesion=bytes.fromhex(secreto) if secreto else None,
        instantaneas=InstantaneasPg(pg) if pg else None,
        politica=configuraciones.Politica.desde_archivo(Path(ruta_politica)),
        facturas=CostosPg(pg) if pg else None,
        token_nodos=os.environ.get("TOKEN_INSTANTANEAS", ""),
        cliente_modelo=_modelo_desde_entorno(),
    )


def _modelo_desde_entorno():
    """El cliente del modelo si esta configurado; si no, None y la ruta de
    diagnostico responde 503 diciendo que falta."""
    from zenit.diagnostico.modelo import ClienteAnthropic, SinConfiguracion

    try:
        return ClienteAnthropic.desde_entorno()
    except SinConfiguracion as e:
        log.warning("diagnostico sin configurar: %s", e)
        return None


def _analitico_desde_entorno():
    if not os.environ.get("CLICKHOUSE_URL"):
        return None
    from zenit.repositorio.analitico import RepositorioAnalitico

    return RepositorioAnalitico.desde_entorno()


app = _desde_entorno()
