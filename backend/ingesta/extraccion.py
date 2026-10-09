"""De OTLP al esquema canonico.

Funciones puras, sin red ni estado global, para poder probarlas con mensajes
OTLP construidos a mano. El servidor solo las llama.

Correccion respecto a la version anterior: antes se emitia un registro por
metrica con nombre, unidad y tipo, y se perdian el valor y la marca de tiempo.
Ahora se baja hasta los puntos de datos, que es donde estan.
"""
from dataclasses import dataclass, field
from typing import Iterator, Mapping, Optional

from zenit.esquema import (
    DESCONOCIDO,
    ETIQUETAS_CANONICAS,
    ORIGEN_EN_RECURSO,
    SIN_RESOLVER,
    canonizar,
    canonizar_dimension,
)

# Claves nativas por las que se resuelve el activo, en orden de preferencia.
CLAVES_NATIVAS = ("host.id", "host.name", "container.id", "service.instance.id")

# Bandera de OTLP: el punto existe pero no tiene valor registrado.
_SIN_VALOR_REGISTRADO = 1


def valor_de(any_value):
    """Convierte un AnyValue de OTLP a un tipo de Python."""
    campo = any_value.WhichOneof("value")
    if campo is None:
        return None
    if campo == "array_value":
        return [valor_de(v) for v in any_value.array_value.values]
    if campo == "kvlist_value":
        return {kv.key: valor_de(kv.value) for kv in any_value.kvlist_value.values}
    if campo == "bytes_value":
        return any_value.bytes_value.hex()
    return getattr(any_value, campo)


def atributos(lista) -> dict:
    return {kv.key: valor_de(kv.value) for kv in lista}


@dataclass(frozen=True)
class Identidad:
    """Las seis etiquetas canonicas de un recurso, mas como se resolvio."""

    etiquetas: Mapping[str, str]
    clave_nativa: Optional[str]
    huerfana: bool
    # el valor de esa clave (zenit-nodo-do): es el origen que lista la vista de huerfanas
    valor_clave: Optional[str] = None


def resolver_identidad(
    recurso_crudo: Mapping, catalogo: Mapping[str, str], servicio_por_defecto: str
) -> Identidad:
    """Aplica el esquema canonico a los atributos de un recurso.

    El activo se resuelve por la primera clave nativa que exista en el
    catalogo. Si ninguna resuelve, la senal queda huerfana: se almacena igual,
    con zenit_asset_id = "sin_resolver".
    """
    activo, clave_usada = None, None
    for clave in CLAVES_NATIVAS:
        if clave in recurso_crudo:
            clave_usada = clave_usada or clave
            encontrado = catalogo.get(str(recurso_crudo[clave]))
            if encontrado:
                activo, clave_usada = encontrado, clave
                break
    valor_clave = str(recurso_crudo[clave_usada]) if clave_usada else None

    etiquetas = {"zenit_asset_id": activo or SIN_RESOLVER}
    for canonica, origen in ORIGEN_EN_RECURSO.items():
        valor = recurso_crudo.get(origen)
        etiquetas[canonica] = str(valor) if valor not in (None, "") else DESCONOCIDO

    # Las metricas del anfitrion no pertenecen a ningun servicio, pero la
    # etiqueta es obligatoria en toda tabla. Valor explicito y configurable.
    if etiquetas["service_name"] == DESCONOCIDO:
        etiquetas["service_name"] = servicio_por_defecto

    assert set(etiquetas) == set(ETIQUETAS_CANONICAS)
    return Identidad(etiquetas=etiquetas, clave_nativa=clave_usada, huerfana=activo is None, valor_clave=valor_clave)


@dataclass
class PuntoMetrica:
    """Un punto de datos ya canonizado. Puede producir una o dos filas."""

    tabla: str
    dimensiones: dict
    tiempo_ns: int
    # Lista de (sufijo_de_tabla, valor). Gauge y sum producen una fila;
    # histogramas y resumenes producen _count y _sum.
    valores: list = field(default_factory=list)
    sin_valor: bool = False


def _dimensiones(attrs) -> dict:
    salida = {}
    for clave, valor in atributos(attrs).items():
        if valor is None or valor == "":
            continue
        salida[canonizar_dimension(clave)] = str(valor)
    return salida


def _valor_numerico(punto):
    caso = punto.WhichOneof("value")
    if caso == "as_double":
        return punto.as_double
    if caso == "as_int":
        return float(punto.as_int)
    return None


def _tiempo(punto, ahora_ns: int) -> int:
    return punto.time_unix_nano or ahora_ns


def extraer_puntos(metrica, ahora_ns: int) -> Iterator[PuntoMetrica]:
    """Recorre los puntos de datos de una metrica OTLP.

    Gauge y sum: un valor por punto. Histograma, histograma exponencial y
    resumen: se descomponen en conteo y suma, que es la descomposicion
    convencional. Los cubos del histograma no se almacenan en la epica 0.
    """
    tabla = canonizar(metrica.name)
    tipo = metrica.WhichOneof("data")

    if tipo in ("gauge", "sum"):
        for p in getattr(metrica, tipo).data_points:
            valor = _valor_numerico(p)
            sin_valor = bool(p.flags & _SIN_VALOR_REGISTRADO) or valor is None
            yield PuntoMetrica(
                tabla=tabla,
                dimensiones=_dimensiones(p.attributes),
                tiempo_ns=_tiempo(p, ahora_ns),
                valores=[] if sin_valor else [("", valor)],
                sin_valor=sin_valor,
            )
        return

    if tipo in ("histogram", "exponential_histogram", "summary"):
        for p in getattr(metrica, tipo).data_points:
            sin_valor = bool(p.flags & _SIN_VALOR_REGISTRADO)
            valores = [] if sin_valor else [("_count", float(p.count))]
            # sum es opcional en histogramas: si no viene, no se inventa un 0
            if not sin_valor and (tipo == "summary" or p.HasField("sum")):
                valores.append(("_sum", float(p.sum)))
            yield PuntoMetrica(
                tabla=tabla,
                dimensiones=_dimensiones(p.attributes),
                tiempo_ns=_tiempo(p, ahora_ns),
                valores=valores,
                sin_valor=sin_valor,
            )
        return

    # Tipo desconocido: se reporta con tabla y sin valores, y quien llama lo
    # cuenta aparte. No se ignora en silencio.
    yield PuntoMetrica(tabla=tabla, dimensiones={}, tiempo_ns=ahora_ns, valores=[], sin_valor=True)


# ------------------------------------------------------------------ registros y trazas
# El receptor filelog del agente pone este nombre en log.file.name. Los
# registros que lo traen son la verdad de referencia del laboratorio.
ARCHIVO_VERDAD = "verdad.jsonl"

_TIPOS_SPAN = {0: "sin_definir", 1: "interno", 2: "servidor", 3: "cliente", 4: "productor", 5: "consumidor"}
_ESTADOS_SPAN = {0: "sin_definir", 1: "ok", 2: "error"}


def momento_texto(ns: int) -> str:
    """'AAAA-MM-DD hh:mm:ss.nnnnnnnnn' en UTC: lo que ClickHouse acepta para
    DateTime64(9) sin perder los nanosegundos."""
    import time as _t

    if not ns or ns < 0:
        ns = _t.time_ns()
    segundos, fraccion = divmod(int(ns), 1_000_000_000)
    return _t.strftime("%Y-%m-%d %H:%M:%S", _t.gmtime(segundos)) + f".{fraccion:09d}"


def _texto(valor) -> str:
    if valor is None:
        return ""
    if isinstance(valor, str):
        return valor
    import json as _json

    return _json.dumps(valor, ensure_ascii=False, default=str)


def _mapa(lista) -> dict:
    return {kv.key: _texto(valor_de(kv.value)) for kv in lista}


def fila_registro(reg) -> dict:
    """Un LogRecord de OTLP como fila de zenit.registros (sin las etiquetas
    canonicas, que pone quien llama)."""
    return {
        "momento": momento_texto(reg.time_unix_nano or reg.observed_time_unix_nano),
        "severidad": reg.severity_text,
        "severidad_num": int(reg.severity_number),
        "cuerpo": _texto(valor_de(reg.body)),
        "traza_id": reg.trace_id.hex(),
        "span_id": reg.span_id.hex(),
        "atributos": _mapa(reg.attributes),
    }


def fila_traza(span) -> dict:
    """Un Span de OTLP como fila de zenit.trazas."""
    inicio, fin = span.start_time_unix_nano, span.end_time_unix_nano
    return {
        "inicio": momento_texto(inicio),
        "fin": momento_texto(fin or inicio),
        "duracion_ns": max(0, int(fin) - int(inicio)) if fin and inicio else 0,
        "traza_id": span.trace_id.hex(),
        "span_id": span.span_id.hex(),
        "padre_id": span.parent_span_id.hex(),
        "nombre": span.name,
        "tipo": _TIPOS_SPAN.get(int(span.kind), "sin_definir"),
        "estado": _ESTADOS_SPAN.get(int(span.status.code), "sin_definir"),
        "estado_mensaje": span.status.message,
        "atributos": _mapa(span.attributes),
    }
