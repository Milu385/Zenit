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
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Mapping, Optional

from zenit.esquema import COLUMNA_TIEMPO, COLUMNA_VALOR, ETIQUETAS_CANONICAS, es_identificador
from zenit.influx import ClienteInflux, ErrorInflux

from .contrato import ErrorConsulta, Resolucion, Respuesta, Serie

INTERVALO = timedelta(seconds=10)
RANGO_MAXIMO = timedelta(hours=24)
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
    ):
        self._cliente = cliente
        self._bd = bd
        self._ventana = ventana_metricas
        self._cache_s = cache_s
        self._candado = threading.Lock()
        self._esquema: Optional[dict] = None
        self._esquema_t = 0.0
        self._metricas_cache: dict = {}

    # ------------------------------------------------------------ esquema
    def _columnas(self) -> dict:
        """{tabla: [columnas]} de las tablas de usuario, con cache corta."""
        with self._candado:
            if self._esquema is not None and time.monotonic() - self._esquema_t < self._cache_s:
                return self._esquema
        filas = self._cliente.consultar(
            self._bd,
            "SELECT table_name, column_name FROM information_schema.columns "
            f"WHERE table_schema NOT IN {_ESQUEMAS_DE_SISTEMA}",
        )
        esquema: dict = {}
        for f in filas:
            esquema.setdefault(f["table_name"], []).append(f["column_name"])
        with self._candado:
            self._esquema, self._esquema_t = esquema, time.monotonic()
        return esquema

    def _dimensiones_de(self, metrica: str) -> list:
        if not es_identificador(metrica):
            raise ErrorConsulta(f"nombre de metrica no valido: {metrica!r}")
        columnas = self._columnas().get(metrica)
        if columnas is None:
            raise ErrorConsulta(f"no existe la metrica {metrica!r}")
        fijas = {COLUMNA_TIEMPO, COLUMNA_VALOR, *ETIQUETAS_CANONICAS}
        return sorted(c for c in columnas if c not in fijas and es_identificador(c))

    @staticmethod
    def _validar_rango(desde: datetime, hasta: datetime) -> None:
        if hasta <= desde:
            raise ErrorConsulta("el rango esta vacio: 'hasta' debe ser posterior a 'desde'")
        if hasta - desde > RANGO_MAXIMO:
            raise ErrorConsulta(
                "el rango maximo a resolucion de 10 s es de 24 horas; los rangos largos "
                "se resuelven contra los resumenes (H-018 y H-019)"
            )

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
        dims = self._dimensiones_de(metrica)
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
        for f in self._cliente.consultar(self._bd, sql, {"activo": activo}):
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
        self._validar_rango(desde, hasta)
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
        )

    def disponible(self) -> bool:
        return self._cliente.disponible()

    def olvidar_esquema(self) -> None:
        with self._candado:
            self._esquema = None
            self._metricas_cache.clear()


__all__ = ["RepositorioInflux", "ErrorConsulta", "ErrorInflux"]
