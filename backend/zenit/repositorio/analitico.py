"""Repositorio de registros y trazas sobre ClickHouse (H-016, H-017, RF-ALM-07).

Como el de series, es el unico lugar donde se escribe SQL contra este motor.
Los valores del usuario van siempre como parametros tipados ({nombre:Tipo});
las fechas se construyen aqui a partir de datetime.

La correlacion entre senales (RF-ALM-07, ADR-007) es una sola consulta: los
registros de un activo en una ventana, unidos con el span que los produjo por
traza_id y span_id.
"""
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from zenit.clickhouse import ClienteClickHouse
from zenit.esquema import SIN_RESOLVER

from .contrato import ErrorConsulta

RANGO_MAXIMO = timedelta(days=7)        # lo que retienen las tablas
LIMITE_MAXIMO = 2000
_CANONICAS = ("zenit_asset_id", "host_name", "service_name", "cloud_provider", "cloud_region",
              "deployment_environment")


def _ns(momento: datetime) -> int:
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=timezone.utc)
    return int(momento.timestamp() * 1_000_000_000)


def _validar(desde: datetime, hasta: datetime, limite: int) -> None:
    if hasta <= desde:
        raise ErrorConsulta("el rango esta vacio: 'hasta' debe ser posterior a 'desde'")
    if hasta - desde > RANGO_MAXIMO:
        raise ErrorConsulta("registros y trazas se retienen 7 dias: el rango maximo es de 7 dias")
    if not 1 <= limite <= LIMITE_MAXIMO:
        raise ErrorConsulta(f"limite entre 1 y {LIMITE_MAXIMO}")


# fromUnixTimestamp64Nano recibe Int64: las fechas viajan como enteros, sin texto
_VENTANA = ("{col} >= fromUnixTimestamp64Nano({desde:Int64}, 'UTC') "
            "AND {col} < fromUnixTimestamp64Nano({hasta:Int64}, 'UTC')")


def _ventana(columna: str, prefijo: str = "") -> str:
    return _VENTANA.replace("{col}", f"{prefijo}{columna}")


def _iso(texto: str) -> str:
    """'2026-10-09 14:00:00.123456789' (UTC) -> ISO 8601 con zona y microsegundos."""
    base, _, fraccion = texto.partition(".")
    return f"{base.replace(' ', 'T')}.{(fraccion + '000000')[:6]}+00:00"


class RepositorioAnalitico:
    def __init__(self, cliente: ClienteClickHouse):
        self._ch = cliente

    @classmethod
    def desde_entorno(cls) -> "RepositorioAnalitico":
        return cls(ClienteClickHouse(os.environ["CLICKHOUSE_URL"], os.environ.get("CLICKHOUSE_USUARIO", "zenit"),
                                     os.environ.get("CLICKHOUSE_CLAVE", "")))

    def disponible(self) -> bool:
        return self._ch.disponible()

    # ------------------------------------------------------------ registros
    def registros(self, activo: str, desde: datetime, hasta: datetime, *, severidad_minima: int = 0,
                  texto: Optional[str] = None, servicio: Optional[str] = None, limite: int = 200) -> list:
        """Los registros de un activo en una ventana, del mas reciente al mas viejo."""
        _validar(desde, hasta, limite)
        condiciones = ["zenit_asset_id = {activo:String}", _ventana("momento"),
                       "severidad_num >= {sev:UInt8}"]
        params = {"activo": activo, "desde": _ns(desde), "hasta": _ns(hasta), "sev": int(severidad_minima),
                  "limite": int(limite)}
        if texto:
            condiciones.append("positionCaseInsensitive(cuerpo, {texto:String}) > 0")
            params["texto"] = texto
        if servicio:
            condiciones.append("service_name = {servicio:String}")
            params["servicio"] = servicio
        filas = self._ch.consultar(
            "SELECT toString(momento) AS t_momento, service_name, host_name, severidad, severidad_num, cuerpo, "
            "traza_id, span_id, atributos FROM registros "
            f"WHERE {' AND '.join(condiciones)} ORDER BY momento DESC LIMIT {{limite:UInt32}}",
            params,
        )
        for f in filas:
            f["momento"] = _iso(f.pop("t_momento"))
        return filas

    # ------------------------------------------------------------ trazas
    def trazas(self, activo: str, desde: datetime, hasta: datetime, *, solo_errores: bool = False,
               limite: int = 100) -> list:
        """Resumen por traza de las que tocaron este activo en la ventana: el
        span raiz (si esta), la duracion total, cuantos spans y si hubo error."""
        _validar(desde, hasta, limite)
        filtro_error = "HAVING errores > 0" if solo_errores else ""
        filas = self._ch.consultar(
            "SELECT traza_id, toString(min(inicio)) AS t_inicio, toString(max(fin)) AS t_fin, "
            "toUInt64(dateDiff('microsecond', min(inicio), max(fin))) * 1000 AS duracion_ns, "
            "count() AS spans, countIf(estado = 'error') AS errores, "
            "argMinIf(nombre, inicio, padre_id = '') AS raiz, groupUniqArray(service_name) AS servicios "
            "FROM trazas WHERE zenit_asset_id = {activo:String} AND " + _ventana("inicio") + " "
            f"GROUP BY traza_id {filtro_error} ORDER BY max(fin) DESC LIMIT {{limite:UInt32}}",
            {"activo": activo, "desde": _ns(desde), "hasta": _ns(hasta), "limite": int(limite)},
        )
        for f in filas:
            f["inicio"], f["fin"] = _iso(f.pop("t_inicio")), _iso(f.pop("t_fin"))
            f["servicios"] = sorted(f["servicios"])
        return filas

    def traza(self, traza_id: str) -> list:
        """Todos los spans de una traza, en orden, con su activo: una traza puede
        cruzar activos y eso es justo lo que sirve para localizar el componente."""
        if not (len(traza_id) == 32 and all(c in "0123456789abcdef" for c in traza_id)):
            raise ErrorConsulta("traza_id son 32 caracteres hexadecimales en minuscula")
        filas = self._ch.consultar(
            "SELECT toString(inicio) AS t_inicio, toString(fin) AS t_fin, duracion_ns, span_id, padre_id, nombre, tipo, "
            "estado, estado_mensaje, zenit_asset_id, service_name, host_name, atributos FROM trazas "
            "WHERE traza_id = {t:String} ORDER BY inicio, padre_id != '' LIMIT 5000",
            {"t": traza_id},
        )
        for f in filas:
            f["inicio"], f["fin"] = _iso(f.pop("t_inicio")), _iso(f.pop("t_fin"))
        return filas

    # ------------------------------------------------------------ correlacion
    def correlacion(self, activo: str, desde: datetime, hasta: datetime, limite: int = 200) -> list:
        """RF-ALM-07: registros del activo en la ventana unidos con su span, en
        una sola consulta sobre el identificador canonico."""
        _validar(desde, hasta, limite)
        filas = self._ch.consultar(
            "SELECT toString(r.momento) AS t_momento, r.service_name AS servicio, r.severidad AS severidad, "
            "r.cuerpo AS cuerpo, r.traza_id AS traza_id, t.nombre AS operacion, t.duracion_ns AS duracion_ns, "
            "t.estado AS estado_span "
            "FROM registros AS r LEFT JOIN "
            "(SELECT traza_id, span_id, nombre, duracion_ns, estado FROM trazas "
            " WHERE zenit_asset_id = {activo:String} AND " + _ventana("inicio") + ") AS t "
            "ON r.traza_id = t.traza_id AND r.span_id = t.span_id "
            "WHERE r.zenit_asset_id = {activo:String} AND " + _ventana("momento", "r.") + " "
            "ORDER BY r.momento DESC LIMIT {limite:UInt32}",
            {"activo": activo, "desde": _ns(desde), "hasta": _ns(hasta), "limite": int(limite)},
        )
        for f in filas:
            f["momento"] = _iso(f.pop("t_momento"))
            if not f.get("operacion"):
                f["operacion"], f["duracion_ns"], f["estado_span"] = None, None, None
        return filas

    def resumen(self, activo: str, desde: datetime, hasta: datetime) -> dict:
        """Cuantos registros (y de ellos con severidad de error o mas) y cuantos
        spans (y con error) tiene el activo en la ventana."""
        _validar(desde, hasta, 1)
        params = {"activo": activo, "desde": _ns(desde), "hasta": _ns(hasta)}
        (r,) = self._ch.consultar(
            "SELECT (SELECT count() FROM registros WHERE zenit_asset_id = {activo:String} AND "
            + _ventana("momento") + ") AS registros, "
            "(SELECT count() FROM registros WHERE zenit_asset_id = {activo:String} AND severidad_num >= 17 AND "
            + _ventana("momento") + ") AS registros_error, "
            "(SELECT count() FROM trazas WHERE zenit_asset_id = {activo:String} AND " + _ventana("inicio")
            + ") AS spans, "
            "(SELECT count() FROM trazas WHERE zenit_asset_id = {activo:String} AND estado = 'error' AND "
            + _ventana("inicio") + ") AS spans_error",
            params,
        )
        return {k: int(v) for k, v in r.items()}

    def contar_registros(self, desde: datetime, hasta: datetime, servicio: Optional[str] = None,
                         activo: Optional[str] = None) -> int:
        """Para las mediciones de H-016: cuantos registros hay en la ventana."""
        condiciones = [_ventana("momento")]
        params = {"desde": _ns(desde), "hasta": _ns(hasta)}
        if servicio:
            condiciones.append("service_name = {servicio:String}")
            params["servicio"] = servicio
        if activo:
            condiciones.append("zenit_asset_id = {activo:String}")
            params["activo"] = activo
        (f,) = self._ch.consultar(f"SELECT count() AS n FROM registros WHERE {' AND '.join(condiciones)}", params)
        return int(f["n"])

    # ------------------------------------------------------------ mantenimiento (H-011)
    def reasignar_sin_resolver(self, host_name: str, activo: str) -> int:
        """Pasa las filas huerfanas de un origen al activo que ahora lo resuelve.

        zenit_asset_id es parte de la clave de orden, y ClickHouse no permite
        cambiarla con ALTER UPDATE. Se copia con el id nuevo y reasignado = 1, y
        despues se borran las originales. La copia salta las filas que ya tienen
        su copia (por una huella de sus columnas), asi que si el proceso se corta
        entre copiar y borrar, repetirlo no duplica nada.
        """
        total = 0
        p = {"h": host_name, "a": activo, "s": SIN_RESOLVER}
        for tabla, huella in (("registros", "cityHash64(momento, service_name, cuerpo, span_id, severidad_num)"),
                              ("trazas", "cityHash64(inicio, traza_id, span_id, nombre)")):
            (f,) = self._ch.consultar(
                f"SELECT count() AS n FROM {tabla} WHERE zenit_asset_id = {{s:String}} AND host_name = {{h:String}}", p)
            n = int(f["n"])
            if not n:
                continue
            self._ch.ejecutar(
                f"INSERT INTO {tabla} SELECT * REPLACE ({{a:String}} AS zenit_asset_id, toUInt8(1) AS reasignado) "
                f"FROM {tabla} WHERE zenit_asset_id = {{s:String}} AND host_name = {{h:String}} "
                f"AND {huella} NOT IN (SELECT {huella} FROM {tabla} WHERE zenit_asset_id = {{a:String}} "
                f"AND host_name = {{h:String}} AND reasignado = 1)", p)
            # solo lo que ya tiene su copia: lo que la ingesta siga escribiendo
            # como huerfano mientras tanto queda para la siguiente pasada
            self._ch.ejecutar(
                f"DELETE FROM {tabla} WHERE zenit_asset_id = {{s:String}} AND host_name = {{h:String}} "
                f"AND {huella} IN (SELECT {huella} FROM {tabla} WHERE zenit_asset_id = {{a:String}} "
                f"AND host_name = {{h:String}} AND reasignado = 1)", p)
            total += n
        return total
