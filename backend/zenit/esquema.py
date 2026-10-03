"""Esquema canonico de Zenit.

Una sola fuente de verdad para los nombres. El punto de entrada los aplica al
escribir y la API los usa al consultar; si cada uno los definiera por su lado,
tarde o temprano dejarian de coincidir.

Referencia: documento de esquema de medicion en InfluxDB, secciones 4 y 5.
"""
import re

# Las seis etiquetas obligatorias, ya con la conversion de puntos aplicada.
# Toda fila de toda tabla las lleva, y nunca vacias.
ETIQUETAS_CANONICAS = (
    "zenit_asset_id",
    "host_name",
    "service_name",
    "cloud_provider",
    "cloud_region",
    "deployment_environment",
)

# De que atributo de recurso de OpenTelemetry sale cada etiqueta canonica.
# zenit_asset_id no esta aqui: sale del catalogo, no del recurso.
ORIGEN_EN_RECURSO = {
    "host_name": "host.name",
    "service_name": "service.name",
    "cloud_provider": "cloud.provider",
    "cloud_region": "cloud.region",
    "deployment_environment": "deployment.environment",
}

# Valor explicito cuando un atributo no viene. El protocolo de linea no admite
# etiquetas vacias, y una etiqueta omitida produce nulos en la tabla.
DESCONOCIDO = "desconocido"

# Valor de zenit_asset_id para la telemetria que no resolvio contra el
# catalogo. Se almacena igual (ninguna senal se descarta) y se distingue por
# este valor; el origen queda en host_name.
SIN_RESOLVER = "sin_resolver"

# Columnas reservadas por el motor en cada tabla.
COLUMNA_TIEMPO = "time"
COLUMNA_VALOR = "value"

_NO_PERMITIDO = re.compile(r"[^a-z0-9_]")
_IDENTIFICADOR = re.compile(r"^[a-z][a-z0-9_]{0,62}$")

# Palabras que, usadas como nombre de tabla o columna, obligarian a comillas en
# cualquier consulta escrita a mano. Se evitan agregando un sufijo.
_RESERVADAS = frozenset(
    """
    all and as asc between by case cast create delete desc distinct drop else end
    exists false from full group having in inner insert interval is join left like
    limit not null offset on or order outer right select set table then time
    timestamp true union update using value values when where with
    """.split()
)


def canonizar(nombre: str) -> str:
    """Convierte un nombre de OpenTelemetry al de la tabla o columna.

    system.cpu.utilization -> system_cpu_utilization
    http.server.request.duration -> http_server_request_duration

    La conversion ocurre en el punto de entrada y una sola vez, nunca en cada
    consulta.
    """
    limpio = _NO_PERMITIDO.sub("_", nombre.strip().lower())
    limpio = re.sub(r"_+", "_", limpio).strip("_")
    if not limpio:
        return "sin_nombre"
    if not limpio[0].isalpha():
        limpio = "m_" + limpio
    limpio = limpio[:63]
    if limpio in _RESERVADAS:
        limpio += "_x"
    return limpio


def canonizar_dimension(nombre: str) -> str:
    """Como canonizar, pero sin dejar que una dimension pise una columna
    reservada (time, value) ni una etiqueta canonica."""
    limpio = canonizar(nombre)
    if limpio in (COLUMNA_TIEMPO, COLUMNA_VALOR) or limpio in ETIQUETAS_CANONICAS:
        limpio += "_dim"
    return limpio


def es_identificador(nombre: str) -> bool:
    """True si el nombre es seguro para ir dentro de SQL entre comillas.

    La API recibe nombres de metrica y de dimension desde la URL. Nunca se
    interpolan sin pasar por aqui primero.
    """
    return bool(_IDENTIFICADOR.match(nombre))
