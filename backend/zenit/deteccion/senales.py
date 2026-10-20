"""Catalogo de senales que vigila el detector (H-032).

Por que existe este archivo
---------------------------

El cuaderno `Modelo criticidad` trabaja sobre `data.csv`, cuyas columnas ya
venian preparadas: `cpu_total` sumaba usuario y sistema, `disco_bytes_s` y
`red_bytes_s` ya eran tasas por segundo, y cada fila era un activo. En la
plataforma no existe nada de eso: hay 26 tablas en `zenit_raw`, una por
metrica de OpenTelemetry, y cada fila trae las dimensiones propias de la
metrica (`docs/esquema-medicion.md`, seccion 5). Llevar el metodo a la
plataforma es, en buena medida, reconstruir esas columnas. Dos trampas
concretas:

**1. Promediar dimensiones distintas no significa nada.** `system_cpu_utilization`
trae `cpu` (el nucleo) y `state` (`user`, `system`, `idle`, `wait`...).
Consultar la serie sin filtrar promedia el tiempo ocioso (cercano a 1.0)
con el de usuario: el resultado no sube cuando el nodo se satura, porque lo
que sube en `user` baja en `idle`. Por eso cada senal declara sus `filtros`.
`zenit.salud.cpu_reciente` ya hacia esto a mano (filtra `state='idle'` y
resta de 1); aqui queda declarado para todas.

**2. Un contador acumulado diluye la senal hasta hacerla invisible.**
`system_disk_io`, `system_network_io`, `system_paging_operations` y `calls`
son sumas acumuladas: solo crecen. Sobre una rampa, el MAD crece junto con
la rampa, asi que el puntaje z se queda acotado y nunca llega al corte
critico. Medido con una serie construida a mano (90 min a 100 u/min, 30 min
a 400 u/min, vuelta a 100; ventana de 60, corte 3.5):

| Serie                       | Puntos en critico | z dentro del fallo |
|-----------------------------|-------------------|--------------------|
| Contador crudo (como MEDIDA)| 1, aislado        | 2.85               |
| Convertida a tasa (`a_tasa`)| 30                | 183.5              |

El punto aislado del contador crudo lo descarta despues la regla de
persistencia (m=3), asi que el resultado final son **cero anomalias**: una
saturacion de entrada/salida de media hora, que es uno de los tipos de
inyeccion del laboratorio (`disco`), pasaria entera sin detectar. No es un
exceso de falsos positivos, es un falso negativo completo.

Las senales marcadas como ACUMULADA se convierten a tasa por segundo
(`a_tasa`) antes de calcular la linea base, que es justamente lo que las
columnas `*_s` del CSV del cuaderno ya traian hechas. La prueba
`test_contador_acumulado_*` en `backend/pruebas/test_deteccion.py` fija
este comportamiento.

Agregar una senal es agregar una linea a SENALES. No hay logica por metrica
en el detector.

Los nombres: hay que verificarlos contra la plataforma
------------------------------------------------------

Los nombres de tabla y los valores de dimension de abajo salen de las
convenciones del receptor `hostmetrics` de OpenTelemetry (el que configura
`laboratorio/agente/collector.yaml`) mas la regla de conversion de nombres
de `docs/esquema-medicion.md`, seccion 3. NO estan verificados contra la
plataforma, porque los nombres reales solo se ven consultando el motor.

Para verificarlos, con la plataforma arriba:

    docker compose exec api python -m zenit.deteccion --senales

Lista, activo por activo, que senales del catalogo existen y cuales no, con
las dimensiones que el repositorio reporta para cada metrica. Lo que
aparezca como faltante se corrige aqui, en una linea, sin tocar el detector.

Lo que este catalogo no cubre todavia
-------------------------------------

**Latencia.** La duracion de las llamadas llega como histograma, que el
esquema parte en dos tablas (`duration_count` y `duration_sum`). La latencia
media es `d(sum)/d(count)`: un cociente de dos tasas, es decir dos series
combinadas, y todo este archivo trabaja con una serie a la vez. Queda
declarado como hueco: la inyeccion de tipo `latencia` del laboratorio no la
detecta este metodo, y eso tiene que salir asi en la evaluacion de H-029.

**Caidas.** Una caida de servicio baja `calls` en vez de subirlo, y el
metodo solo mira desviaciones hacia arriba (ver la nota de `detector.py`).
Es la misma limitacion que el cuaderno midio contra la verdad de referencia
(`caida`: 0.398 de las filas en critico, contra 0.98 de las demas) y la
razon por la que el ERS pide H-034.
"""
from dataclasses import dataclass, field
from typing import Mapping, Optional, Sequence

# Clases de senal. La clase decide si la serie se usa tal cual o si antes
# hay que derivarla.
MEDIDA = "medida"          # gauge: el valor ya es el nivel (utilizacion, carga)
ACUMULADA = "acumulada"    # contador que solo crece: se convierte a tasa/segundo


@dataclass(frozen=True)
class Senal:
    """Una serie concreta que el detector vigila.

    `filtros` fija dimensiones a un valor exacto (son las que no se deben
    promediar). `agrupar` abre una serie por cada valor de esa dimension, y
    cada una se evalua por separado: por eso la tabla `anomalias` tiene
    columna `dimensiones`, una anomalia es de una serie, no de una metrica.
    """

    metrica: str
    clase: str = MEDIDA
    filtros: Mapping[str, str] = field(default_factory=dict)
    agrupar: Optional[str] = None
    # Para que sirve, en una linea. Sale en los registros y en --senales.
    descripcion: str = ""
    # Que tipos de inyeccion del laboratorio deberia captar esta senal
    # (los de la restriccion CHECK de `inyecciones`: cpu, memoria, disco,
    # caida, latencia). Es lo que conecta el catalogo con la evaluacion por
    # tipo de fallo de H-029.
    fallas: tuple = ()
    # Columna del CSV del cuaderno a la que corresponde, para poder seguir
    # el rastro entre el cuaderno y la plataforma.
    equivale_a: str = ""

    @property
    def clave(self) -> str:
        """Identificador estable de la senal, para registros y --senales."""
        partes = [f"{k}={v}" for k, v in sorted(self.filtros.items())]
        if self.agrupar:
            partes.append(f"por {self.agrupar}")
        return f"{self.metrica}[{', '.join(partes)}]" if partes else self.metrica


SENALES = (
    # ---------------------------------------------------------------- CPU
    Senal(
        metrica="system_cpu_utilization", clase=MEDIDA, filtros={"state": "user"},
        descripcion="CPU en espacio de usuario, promedio de los nucleos",
        fallas=("cpu",), equivale_a="cpu_user",
    ),
    Senal(
        metrica="system_cpu_utilization", clase=MEDIDA, filtros={"state": "system"},
        descripcion="CPU en espacio de sistema, promedio de los nucleos",
        fallas=("cpu",), equivale_a="cpu_system",
    ),
    Senal(
        metrica="system_cpu_utilization", clase=MEDIDA, filtros={"state": "wait"},
        descripcion="CPU esperando entrada/salida",
        fallas=("disco",), equivale_a="cpu_wait",
    ),
    Senal(
        metrica="system_cpu_load_average_1m", clase=MEDIDA,
        descripcion="Carga del ultimo minuto",
        fallas=("cpu",), equivale_a="carga_1m",
    ),
    # ------------------------------------------------------------- memoria
    Senal(
        metrica="system_memory_utilization", clase=MEDIDA, filtros={"state": "used"},
        descripcion="Memoria en uso",
        fallas=("memoria",), equivale_a="memoria_used",
    ),
    Senal(
        metrica="system_paging_utilization", clase=MEDIDA, filtros={"state": "used"},
        descripcion="Espacio de intercambio en uso",
        fallas=("memoria",),
    ),
    # --------------------------------------------------------------- disco
    # Por punto de montaje: un sistema de archivos que se llena es local a
    # su montaje, y promediar todos los montajes lo diluye.
    Senal(
        metrica="system_filesystem_utilization", clase=MEDIDA, filtros={"state": "used"},
        agrupar="mountpoint",
        descripcion="Sistema de archivos ocupado, por punto de montaje",
        fallas=("disco",), equivale_a="disco_used_frac",
    ),
    Senal(
        metrica="system_disk_io", clase=ACUMULADA, filtros={"direction": "write"},
        agrupar="device",
        descripcion="Bytes escritos por segundo, por dispositivo",
        fallas=("disco",), equivale_a="disco_bytes_s",
    ),
    Senal(
        metrica="system_disk_io", clase=ACUMULADA, filtros={"direction": "read"},
        agrupar="device",
        descripcion="Bytes leidos por segundo, por dispositivo",
        fallas=("disco",),
    ),
    # ----------------------------------------------------------- paginacion
    Senal(
        metrica="system_paging_operations", clase=ACUMULADA, filtros={"direction": "page_in"},
        descripcion="Paginas traidas de disco por segundo",
        fallas=("memoria",), equivale_a="paginacion_s",
    ),
    # ----------------------------------------------------------------- red
    Senal(
        metrica="system_network_io", clase=ACUMULADA, filtros={"direction": "transmit"},
        agrupar="device",
        descripcion="Bytes transmitidos por segundo, por interfaz",
        fallas=(), equivale_a="red_bytes_s",
    ),
    Senal(
        metrica="system_network_io", clase=ACUMULADA, filtros={"direction": "receive"},
        agrupar="device",
        descripcion="Bytes recibidos por segundo, por interfaz",
        fallas=(),
    ),
    # ------------------------------------------------------------ procesos
    Senal(
        metrica="system_processes_count", clase=MEDIDA, filtros={"status": "running"},
        descripcion="Procesos en ejecucion",
        fallas=("cpu",), equivale_a="procesos_running",
    ),
    # ------------------------------------------- aplicacion (spanmetrics)
    # Llamadas con error por segundo. El cuaderno usaba la proporcion contra
    # el total (`tasa_error_relativa`); eso es un cociente de dos series y
    # queda para cuando el detector sepa combinarlas. La tasa absoluta de
    # errores si sirve y es la senal mas cercana a una caida que este metodo
    # puede ver hacia arriba.
    Senal(
        metrica="calls", clase=ACUMULADA, filtros={"status_code": "STATUS_CODE_ERROR"},
        descripcion="Llamadas con error por segundo",
        fallas=("caida", "latencia"), equivale_a="tasa_errores_pedidos",
    ),
)


def a_tasa(puntos: Sequence[tuple]) -> list:
    """Convierte una serie acumulada en tasa por segundo.

    `puntos` son `[(momento, valor), ...]` ordenados. Devuelve un punto menos
    que entra: el primero no tiene con que compararse.

    Un salto negativo significa que el contador se reinicio (el proceso que
    lo publica arranco de nuevo). Ese punto no dice cuanto paso, asi que se
    descarta en vez de inventar una tasa negativa o un cero.

    Sobre los resumenes: el valor de cada minuto es el promedio del contador
    dentro del minuto (`value_avg`), no su ultimo valor, asi que la diferencia
    entre dos minutos es la tasa promedio entre los centros de esos minutos.
    Es una aproximacion, y es la misma que traia el CSV del cuaderno.
    """
    salida = []
    for (t0, v0), (t1, v1) in zip(puntos, puntos[1:]):
        segundos = (t1 - t0).total_seconds()
        if segundos <= 0:
            continue
        delta = v1 - v0
        if delta < 0:
            continue
        salida.append((t1, delta / segundos))
    return salida


def preparar(senal: Senal, puntos: Sequence[tuple]) -> list:
    """Deja la serie lista para la linea base, segun la clase de la senal."""
    return a_tasa(puntos) if senal.clase == ACUMULADA else list(puntos)


def puntos_extra(senal: Senal) -> int:
    """Cuantos puntos de mas hay que pedir para esta senal: convertir a tasa
    consume el primero."""
    return 1 if senal.clase == ACUMULADA else 0
