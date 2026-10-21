"""Implementacion del repositorio sobre InfluxDB 3 Core.

Es el UNICO lugar del proyecto donde se escribe SQL contra el motor
(criterio 3 de H-006, verificable por revision: `grep -rn "SELECT" backend/`
solo debe encontrar este archivo, las pruebas y la herramienta de
administracion).

Reglas de construccion de las consultas:

- los valores que vienen del usuario (activo, valores de filtro) van siempre
  como parametros $nombre, nunca interpolados;
- los nombres de tabla y de columna no pueden ser parametros en SQL; se
  validan contra la expresion de identificador y ademas contra las columnas
  que existen en information_schema, y solo entonces se citan entre comillas;
- las fechas se generan aqui a partir de objetos datetime, no del texto que
  llega en la URL.

Todo el SQL de este archivo se ejecuta en las pruebas contra DataFusion, el
motor de consulta que InfluxDB 3 usa por dentro.
"""
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Mapping, Optional

from zenit.esquema import COLUMNA_TIEMPO, COLUMNA_VALOR, ETIQUETAS_CANONICAS, es_identificador
from zenit.influx import ClienteInflux, ErrorInflux

from .contrato import ErrorConsulta, Resolucion, Respuesta, Serie

INTERVALO = timedelta(seconds=10)
# Seleccion de nivel (H-019, arquitectura seccion 4): hasta 24 h, crudo; hasta
# 30 dias, resumen por minuto; mas, resumen por hora. El maximo es lo que
# retiene zenit_1h.
UMBRAL_CRUDO = timedelta(hours=24)
UMBRAL_MINUTO = timedelta(days=30)
RANGO_MAXIMO = timedelta(days=180)
# En los resumenes la grafica no necesita un punto por minuto: se agrupa en el
# paso mas chico de esta lista que deje unos 1500 puntos o menos.
PASOS_S = (60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400)
PUNTOS_OBJETIVO = 1500
# Core falla las consultas que tocan mas de 432 archivos Parquet (--query-file-limit),
# y cada tabla produce un archivo por cada 10 minutos con datos. Las consultas
# largas se parten en tramos que no se acercan al limite y corren en paralelo.
# Los tramos son multiplos de los pasos de su nivel, asi ningun intervalo queda
# partido (24 h y 10 dias lo son de toda la lista).
# Con el agregador escribiendo cada minuto una vez (y la hora una vez), un dia
# de zenit_1m son entre 144 y 288 archivos por tabla y diez dias de zenit_1h,
# unos 240. Si "zenit.admin espacio" muestra mas archivos por dia, se achican
# con TRAMO_1M_H y TRAMO_1H_D.
TRAMO = {"1m": timedelta(hours=int(os.environ.get("TRAMO_1M_H", "24"))),
         "1h": timedelta(days=int(os.environ.get("TRAMO_1H_D", "10")))}
BASE_DE = {"1m": "zenit_1m", "1h": "zenit_1h"}
OBSERVACIONES = {"1m": 6, "1h": 360}     # por fila de resumen, a 10 s
CAMPOS_RESUMEN = ("value_avg", "value_min", "value_max", "value_count")
# Un hueco cuenta cuando entre dos puntos pasan mas de dos intervalos. El
# agente toma muestras cada 10 s pero no alineadas a la epoca, asi que a veces
# caen dos en un intervalo y ninguna en el siguiente; eso no es un hueco.
HUECO = 2 * INTERVALO
# Los intervalos mas recientes pueden estar todavia en camino (el agente
# agrupa y la ingesta escribe por lotes): RNF-REN-01 les da 30 s.
EN_CAMINO = timedelta(seconds=30)
# Ocho colores categoricos validados para daltonismo; una novena linea no
# recibe un color inventado, se reporta como omitida.
MAXIMO_SERIES = 8
_ESQUEMAS_DE_SISTEMA = "('information_schema', 'system')"


def _literal_tiempo(momento: datetime) -> str:
    """TIMESTAMP literal en UTC sin zona, que es como guarda el motor la
    columna time. Se construye desde un datetime, nunca desde texto externo."""
    if momento.tzinfo is not None:
        momento = momento.astimezone(timezone.utc).replace(tzinfo=None)
    return f"TIMESTAMP '{momento.strftime('%Y-%m-%d %H:%M:%S.%f')}'"


def _alinear(momento: datetime) -> datetime:
    """Al inicio del intervalo de 10 s que lo contiene, como hace date_bin."""
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=timezone.utc)
    segundos = int(momento.timestamp()) // 10 * 10
    return datetime.fromtimestamp(segundos, timezone.utc)


def contar_vacios(ms_con_datos: list, desde: datetime, limite: datetime) -> int:
    """Intervalos de 10 s sin datos dentro de huecos de mas de 20 s.

    desde y limite van alineados a 10 s. Se cuentan el hueco inicial, los
    interiores y el final hasta `limite`.
    """
    paso = int(INTERVALO.total_seconds() * 1000)
    umbral = int(HUECO.total_seconds() * 1000)
    inicio = int(desde.timestamp() * 1000)
    fin = int(limite.timestamp() * 1000)
    puntos = sorted(t for t in set(ms_con_datos) if inicio <= t < fin)
    if not puntos:
        return max(0, (fin - inicio) // paso)
    vacios = 0
    for previo, actual in zip([inicio - paso] + puntos, puntos + [fin]):
        if actual - previo > umbral:
            vacios += (actual - previo) // paso - 1
    return vacios


def _citar(identificador: str) -> str:
    if not es_identificador(identificador):
        raise ErrorConsulta(f"nombre no valido: {identificador!r}")
    return f'"{identificador}"'


class RepositorioInflux:
    def __init__(
        self,
        cliente: ClienteInflux,
        bd: str = "zenit_raw",
        ventana_metricas: timedelta = timedelta(hours=1),
        cache_s: float = 30.0,
        bd_interna: str = "zenit_interno",
    ):
        self._cliente = cliente
        self._bd = bd
        self._bd_interna = bd_interna
        self._ventana = ventana_metricas
        self._cache_s = cache_s
        self._candado = threading.Lock()
        self._esquemas: dict = {}          # bd -> (momento, {tabla: [columnas]})
        self._metricas_cache: dict = {}
        self._hilos = ThreadPoolExecutor(max_workers=8, thread_name_prefix="tramos")

    # ------------------------------------------------------------ esquema
    def _columnas(self, bd: Optional[str] = None) -> dict:
        """{tabla: [columnas]} de las tablas de usuario de una base, con cache corta."""
        bd = bd or self._bd
        with self._candado:
            guardado = self._esquemas.get(bd)
            if guardado is not None and time.monotonic() - guardado[0] < self._cache_s:
                return guardado[1]
        filas = self._cliente.consultar(
            bd,
            "SELECT table_name, column_name FROM information_schema.columns "
            f"WHERE table_schema NOT IN {_ESQUEMAS_DE_SISTEMA}",
        )
        esquema: dict = {}
        for f in filas:
            esquema.setdefault(f["table_name"], []).append(f["column_name"])
        with self._candado:
            self._esquemas[bd] = (time.monotonic(), esquema)
        return esquema

    def _dimensiones_de(self, metrica: str, bd: Optional[str] = None) -> list:
        if not es_identificador(metrica):
            raise ErrorConsulta(f"nombre de metrica no valido: {metrica!r}")
        columnas = self._columnas(bd).get(metrica)
        if columnas is None:
            if bd and bd != self._bd and metrica in self._columnas():
                raise ErrorConsulta(f"todavia no hay resumenes de {metrica!r} para este rango; "
                                    "el agregador los esta produciendo")
            raise ErrorConsulta(f"no existe la metrica {metrica!r}")
        fijas = {COLUMNA_TIEMPO, COLUMNA_VALOR, *ETIQUETAS_CANONICAS, *CAMPOS_RESUMEN}
        return sorted(c for c in columnas if c not in fijas and es_identificador(c))

    @staticmethod
    def _validar_rango(desde: datetime, hasta: datetime) -> None:
        if hasta <= desde:
            raise ErrorConsulta("el rango esta vacio: 'hasta' debe ser posterior a 'desde'")
        if hasta - desde > RANGO_MAXIMO:
            raise ErrorConsulta(
                f"el rango maximo es de {RANGO_MAXIMO.days} dias, lo que retiene el resumen por hora")

    @staticmethod
    def nivel_para(desde: datetime, hasta: datetime) -> Resolucion:
        """El arbol de decision de la arquitectura (seccion 4). Es lo unico que
        hay que cambiar para mover los umbrales."""
        rango = hasta - desde
        if rango <= UMBRAL_CRUDO:
            return Resolucion.CRUDO
        if rango <= UMBRAL_MINUTO:
            return Resolucion.MINUTO
        return Resolucion.HORA

    @staticmethod
    def paso_para(nivel: Resolucion, desde: datetime, hasta: datetime) -> int:
        minimo = 60 if nivel == Resolucion.MINUTO else 3600
        deseado = (hasta - desde).total_seconds() / PUNTOS_OBJETIVO
        return next((p for p in PASOS_S if p >= max(minimo, deseado)), PASOS_S[-1])

    # ------------------------------------------------------------ contrato
    def metricas_de(self, activo: str) -> list:
        ahora = time.monotonic()
        with self._candado:
            guardado = self._metricas_cache.get(activo)
            if guardado and ahora - guardado[0] < self._cache_s:
                return guardado[1]

        tablas = sorted(t for t, cols in self._columnas().items()
                        if "zenit_asset_id" in cols and es_identificador(t))
        desde = _literal_tiempo(datetime.now(timezone.utc) - self._ventana)
        encontradas = []
        # Una consulta por bloque de tablas: con cuarenta metricas son uno o
        # dos viajes al motor y no cuarenta.
        for i in range(0, len(tablas), 40):
            bloque = tablas[i:i + 40]
            sql = " UNION ALL ".join(
                f"(SELECT '{t}' AS metrica FROM {_citar(t)} "
                f"WHERE \"zenit_asset_id\" = $activo AND time >= {desde} LIMIT 1)"
                for t in bloque
            )
            encontradas += [f["metrica"] for f in self._cliente.consultar(self._bd, sql, {"activo": activo})]
        resultado = sorted(set(encontradas))
        with self._candado:
            self._metricas_cache[activo] = (ahora, resultado)
        return resultado

    def dimensiones(self, activo: str, metrica: str, desde: datetime, hasta: datetime) -> dict:
        self._validar_rango(desde, hasta)
        nivel = self.nivel_para(desde, hasta)
        bd = self._bd if nivel == Resolucion.CRUDO else BASE_DE[nivel.value]
        if nivel != Resolucion.CRUDO:
            # en rangos largos basta el ultimo tramo para conocer los valores
            desde = max(desde, hasta - TRAMO[nivel.value])
        dims = self._dimensiones_de(metrica, bd)
        if not dims:
            return {}
        tabla = _citar(metrica)
        rango = f"time >= {_literal_tiempo(desde)} AND time < {_literal_tiempo(hasta)}"
        sql = " UNION ALL ".join(
            f"SELECT DISTINCT '{d}' AS dimension, CAST({_citar(d)} AS VARCHAR) AS valor "
            f"FROM {tabla} WHERE \"zenit_asset_id\" = $activo AND {rango} AND {_citar(d)} IS NOT NULL"
            for d in dims
        )
        salida: dict = {d: [] for d in dims}
        for f in self._cliente.consultar(bd, sql, {"activo": activo}):
            salida[f["dimension"]].append(f["valor"])
        return {d: sorted(v)[:100] for d, v in salida.items()}

    def serie(
        self,
        activo: str,
        metrica: str,
        desde: datetime,
        hasta: datetime,
        *,
        agrupar: Optional[str] = None,
        filtros: Optional[Mapping[str, str]] = None,
    ) -> Respuesta:
        """La serie, al nivel que corresponde al rango (H-019). La respuesta
        tiene siempre la misma forma; `resolucion` dice que nivel se uso."""
        self._validar_rango(desde, hasta)
        nivel = self.nivel_para(desde, hasta)
        if nivel != Resolucion.CRUDO:
            return self._serie_resumen(nivel, activo, metrica, desde, hasta, agrupar, dict(filtros or {}))
        dims = self._dimensiones_de(metrica)
        filtros = dict(filtros or {})
        for clave in [agrupar, *filtros] if agrupar else list(filtros):
            if clave not in dims:
                raise ErrorConsulta(
                    f"{metrica} no tiene la dimension {clave!r}; tiene: {', '.join(dims) or 'ninguna'}"
                )

        # Se trabaja con intervalos completos: desde baja al inicio de su
        # intervalo y hasta tambien, asi la respuesta no tiene medio
        # intervalo en los bordes y la cuenta de intervalos es exacta.
        desde = _alinear(desde)
        hasta = _alinear(hasta)
        if hasta <= desde:
            hasta = desde + INTERVALO

        parametros = {"activo": activo}
        condiciones = [
            '"zenit_asset_id" = $activo',
            f"time >= {_literal_tiempo(desde)}",
            f"time < {_literal_tiempo(hasta)}",
        ]
        for n, (clave, valor) in enumerate(sorted(filtros.items())):
            parametros[f"f{n}"] = str(valor)
            condiciones.append(f"{_citar(clave)} = $f{n}")

        grupo = f", CAST({_citar(agrupar)} AS VARCHAR) AS grupo" if agrupar else ""
        sql = (
            "SELECT CAST(date_bin(INTERVAL '10 seconds', time, TIMESTAMP '1970-01-01 00:00:00') AS BIGINT) AS t"
            f"{grupo}, avg({_citar(COLUMNA_VALOR)}) AS v "
            f"FROM {_citar(metrica)} WHERE {' AND '.join(condiciones)} "
            f"GROUP BY 1{', 2' if agrupar else ''} ORDER BY 1"
        )
        filas = self._cliente.consultar(self._bd, sql, parametros)

        por_grupo: dict = {}
        con_datos = set()
        for f in filas:
            if f.get("v") is None or f.get("t") is None:
                continue
            ms = int(f["t"]) // 1_000_000
            con_datos.add(ms)
            por_grupo.setdefault(f.get("grupo"), []).append([ms, float(f["v"])])

        claves = sorted(por_grupo, key=lambda k: (k is None, str(k)))
        series = [
            Serie(etiquetas={agrupar: k} if agrupar else {}, puntos=por_grupo[k])
            for k in claves[:MAXIMO_SERIES]
        ]
        # Lo que esta dentro de la ventana de llegada no cuenta como vacio.
        limite = min(hasta, _alinear(datetime.now(timezone.utc) - EN_CAMINO))
        limite = max(limite, desde)
        return Respuesta(
            activo=activo,
            metrica=metrica,
            resolucion=Resolucion.CRUDO,
            desde=desde,
            hasta=hasta,
            agrupar=agrupar,
            series=series,
            intervalos_esperados=int((limite - desde) / INTERVALO),
            intervalos_vacios=contar_vacios(list(con_datos), desde, limite),
            series_omitidas=max(0, len(claves) - MAXIMO_SERIES),
            intervalo_s=int(INTERVALO.total_seconds()),
        )

    def _serie_resumen(self, nivel: Resolucion, activo: str, metrica: str, desde: datetime, hasta: datetime,
                       agrupar: Optional[str], filtros: dict) -> Respuesta:
        """La misma serie sobre zenit_1m o zenit_1h.

        El valor de cada intervalo es el promedio ponderado por value_count, que
        es el promedio exacto de las observaciones crudas que contiene. Un
        intervalo es incompleto si alguna de sus filas de resumen tiene menos
        observaciones de las esperadas (6 por minuto, 360 por hora).
        """
        bd = BASE_DE[nivel.value]
        dims = self._dimensiones_de(metrica, bd)
        for clave in [agrupar, *filtros] if agrupar else list(filtros):
            if clave not in dims:
                raise ErrorConsulta(
                    f"{metrica} no tiene la dimension {clave!r}; tiene: {', '.join(dims) or 'ninguna'}")
        paso = self.paso_para(nivel, desde, hasta)
        p = timedelta(seconds=paso)
        desde = datetime.fromtimestamp(int(desde.timestamp()) // paso * paso, timezone.utc)
        hasta = datetime.fromtimestamp(-(-int(hasta.timestamp()) // paso) * paso, timezone.utc)

        parametros = {"activo": activo}
        condiciones = ['"zenit_asset_id" = $activo']
        for n, (clave, valor) in enumerate(sorted(filtros.items())):
            parametros[f"f{n}"] = str(valor)
            condiciones.append(f"{_citar(clave)} = $f{n}")
        grupo = f", CAST({_citar(agrupar)} AS VARCHAR) AS grupo" if agrupar else ""

        def tramo(a: datetime, b: datetime) -> list:
            sql = (
                f"SELECT CAST(date_bin(INTERVAL '{paso} seconds', time, TIMESTAMP '1970-01-01 00:00:00') AS BIGINT) AS t"
                f'{grupo}, sum("value_avg" * "value_count") / sum("value_count") AS v, min("value_count") AS cmin '
                f"FROM {_citar(metrica)} WHERE {' AND '.join(condiciones)} "
                f"AND time >= {_literal_tiempo(a)} AND time < {_literal_tiempo(b)} "
                f"GROUP BY 1{', 2' if agrupar else ''}"
            )
            return self._cliente.consultar(bd, sql, parametros)

        cortes, t = [], desde
        while t < hasta:
            cortes.append((t, min(t + TRAMO[nivel.value], hasta)))
            t += TRAMO[nivel.value]
        filas = [f for parte in self._hilos.map(lambda c: tramo(*c), cortes) for f in parte]

        por_grupo: dict = {}
        con_datos, incompletos = set(), set()
        for f in filas:
            if f.get("v") is None or f.get("t") is None:
                continue
            ms = int(f["t"]) // 1_000_000
            con_datos.add(ms)
            if float(f.get("cmin") or 0) < OBSERVACIONES[nivel.value]:
                incompletos.add(ms)
            por_grupo.setdefault(f.get("grupo"), []).append([ms, float(f["v"])])
        for puntos in por_grupo.values():
            puntos.sort()
        claves = sorted(por_grupo, key=lambda k: (k is None, str(k)))
        series = [Serie(etiquetas={agrupar: k} if agrupar else {}, puntos=por_grupo[k])
                  for k in claves[:MAXIMO_SERIES]]
        # el ultimo intervalo puede estar a medio resumir: no cuenta como vacio
        limite = min(hasta, datetime.fromtimestamp(
            int((datetime.now(timezone.utc) - EN_CAMINO).timestamp()) // paso * paso, timezone.utc))
        limite = max(limite, desde)
        esperados = int((limite - desde) / p)
        inicio_ms, limite_ms = int(desde.timestamp() * 1000), int(limite.timestamp() * 1000)
        dentro = {ms for ms in con_datos if inicio_ms <= ms < limite_ms}
        return Respuesta(
            activo=activo, metrica=metrica, resolucion=nivel, desde=desde, hasta=hasta, agrupar=agrupar,
            series=series, intervalos_esperados=esperados, intervalos_vacios=max(0, esperados - len(dentro)),
            series_omitidas=max(0, len(claves) - MAXIMO_SERIES), intervalo_s=paso,
            intervalos_incompletos=len({ms for ms in incompletos if inicio_ms <= ms < limite_ms}),
        )

    # ------------------------------------------------------------ salud (H-021)
    def volumen_fuentes(self) -> dict:
        """{(activo, host_name, senal): {recibidas_5min, errores_1h}} desde zenit_interno."""
        ahora = datetime.now(timezone.utc)
        salida: dict = {}
        consultas = (
            ("recibidas_5min", "zenit_fuente_recibidas", ahora - timedelta(minutes=5)),
            ("errores_1h", "zenit_fuente_errores", ahora - timedelta(hours=1)),
        )
        for campo, tabla, desde in consultas:
            try:
                filas = self._cliente.consultar(
                    self._bd_interna,
                    f'SELECT "zenit_asset_id" AS a, "host_name" AS h, "senal" AS s, sum("value") AS n FROM "{tabla}" '
                    f"WHERE time >= {_literal_tiempo(desde)} GROUP BY 1, 2, 3",
                )
            except ErrorInflux as e:
                if e.estado not in (400, 404):   # la tabla aun no existe: nunca hubo errores
                    raise
                filas = []
            for f in filas:
                salida.setdefault((f["a"], f["h"], f["s"]), {})[campo] = int(f.get("n") or 0)
        return salida

    def cpu_reciente(self, minutos: int = 5) -> dict:
        """{activo: fraccion ocupada} en los ultimos minutos: 1 - promedio del estado idle."""
        if "system_cpu_utilization" not in self._columnas():
            return {}
        desde = _literal_tiempo(datetime.now(timezone.utc) - timedelta(minutes=minutos))
        filas = self._cliente.consultar(
            self._bd,
            'SELECT "zenit_asset_id" AS a, 1 - avg("value") AS v FROM "system_cpu_utilization" '
            f"WHERE \"state\" = 'idle' AND time >= {desde} GROUP BY 1",
        )
        return {f["a"]: round(float(f["v"]), 4) for f in filas if f.get("v") is not None}

    # (metrica, estado, invertir): CPU ocupada = 1 - idle; memoria ocupada = used
    _USO = {"cpu": ("system_cpu_utilization", "idle", True), "memoria": ("system_memory_utilization", "used", False)}

    def uso_por_hora(self, desde: datetime, hasta: datetime) -> dict:
        """{activo: {"cpu": [...], "memoria": [...]}}: la fraccion ocupada de
        cada hora con datos, del resumen por hora. Las CPU de un activo se
        promedian juntas (ponderadas por value_count)."""
        bd = BASE_DE["1h"]
        columnas = self._columnas(bd)
        salida: dict = {}
        for clave, (metrica, estado, invertir) in self._USO.items():
            if not {"zenit_asset_id", "state", "value_avg", "value_count"} <= set(columnas.get(metrica, [])):
                continue
            t = desde
            while t < hasta:
                fin = min(t + TRAMO["1h"], hasta)
                filas = self._cliente.consultar(
                    bd,
                    'SELECT "zenit_asset_id" AS a, '
                    "CAST(date_bin(INTERVAL '1 hour', time, TIMESTAMP '1970-01-01 00:00:00') AS BIGINT) AS h, "
                    'sum("value_avg" * "value_count") / sum("value_count") AS v '
                    f"FROM {_citar(metrica)} WHERE \"state\" = $estado "
                    f"AND time >= {_literal_tiempo(t)} AND time < {_literal_tiempo(fin)} GROUP BY 1, 2",
                    {"estado": estado},
                )
                for f in filas:
                    if f.get("v") is None or not f.get("a"):
                        continue
                    v = float(f["v"])
                    salida.setdefault(f["a"], {"cpu": [], "memoria": []})[clave].append(
                        round(1 - v if invertir else v, 4))
                t = fin
        return salida

    def disponible(self) -> bool:
        return self._cliente.disponible()

    def olvidar_esquema(self) -> None:
        with self._candado:
            self._esquemas.clear()
            self._metricas_cache.clear()


__all__ = ["RepositorioInflux", "ErrorConsulta", "ErrorInflux"]
