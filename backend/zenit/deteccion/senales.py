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
`system_disk_io`, `system_network_io`, `system_paging_operations` y
`traces_span_metrics_calls` son sumas acumuladas: solo crecen. Sobre una
rampa, el MAD crece junto con la rampa, asi que el puntaje z se queda
acotado y nunca llega al corte critico. Medido con una serie construida a
mano (90 min a 100 u/min, 30 min a 400 u/min, vuelta a 100; ventana de 60,
corte 3.5):

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

**Como se decide la clase.** No es Gauge contra Sum: es la columna
`Monotonic` de la documentacion del receptor. `system.memory.usage`,
`system.filesystem.usage`, `system.paging.usage` y `system.processes.count`
son Sum con `Monotonic: false`, es decir el nivel actual, y van como MEDIDA.
`system.disk.io`, `system.network.io` y `system.paging.operations` son Sum
con `Monotonic: true` y van como ACUMULADA.

Agregar una senal es agregar una linea a SENALES. No hay logica por metrica
en el detector.

Particiones: el mismo disco contado dos veces
---------------------------------------------

`system_disk_io` llega para el disco y para cada una de sus particiones
(`sda` y `sda1`, `vda` y `vda1`). La particion es parte del disco, asi que
cualquier escritura se ve en las dos series y cada evento de disco abria
dos anomalias (visto en Azure el 21 de octubre). Para la evaluacion por
evento de H-029 eso no cambia los aciertos, pero si duplica los falsos
positivos.

Por eso `Senal` tiene `excluir`: dimension -> expresion regular, comparada
contra el valor completo (`re.fullmatch`). Una serie cuya etiqueta cae en
la expresion no se evalua. Para los discos se usa `PARTICION`:

- letras seguidas de un numero al final (`sda1`, `vda1`, `xvda1`, `sdb15`);
- un numero, `p` y un numero al final (`nvme0n1p1`, `mmcblk0p1`).

`sda`, `vda`, `xvda` y `nvme0n1` no caen: son discos completos. Tambien caen
`loop0` y `md0`. Los `loop` son las imagenes montadas de snap y no son un
disco que se pueda saturar, asi que excluirlos es correcto. `md0` seria un
arreglo RAID completo; no hay ninguno en el laboratorio ni en Azure, y si
apareciera habria que afinar la expresion aqui.

Es un filtro despues de consultar, no antes: el repositorio devuelve como
maximo 8 series por consulta (H-019) y las particiones cuentan para ese
limite. Con los discos de las maquinas de Azure (`sda`, `sdb` y sus
particiones) no se llega a 8, y si se llegara, `evaluar_senal` lo deja en
los registros.

Piso de la escala
-----------------

El puntaje es `z = (valor - mediana) / escala`. Cuando una serie esta casi
plana, el MAD (y la desviacion estandar de respaldo) queda en casi nada y
cualquier movimiento, por pequeno que sea en terminos absolutos, da un
puntaje enorme. Visto en Kaderno: la CPU en espera paso de 0,001 a 0,01 (un
punto porcentual) y abrio una anomalia de puntaje 191. Un punto porcentual
de CPU en espera no es un fallo de nadie.

Por eso la escala tiene un piso:

    escala = max(1.4826 * MAD, piso_relativo * |mediana|, piso_escala)

- **Absoluto, por senal** (`piso_escala`). Para las `*_utilization`, que son
  fracciones de 0 a 1, es 0,01 (`PISO_FRACCION`): un punto porcentual. El
  corte critico queda al menos 3,5 puntos porcentuales por encima de lo
  normal, y lo de Kaderno da z = 0,9 en vez de 191. Las demas senales no
  llevan piso absoluto: sus unidades (bytes por segundo, paginas, llamadas,
  procesos) no tienen un "poco" comun.
- **Relativo** (`PISO_RELATIVO` en `detector.py`), una fraccion de la
  mediana para todas las senales. **Esta en 0**, por lo que dice la
  medicion de abajo.

Medido sobre el `data.csv` del laboratorio (DigitalOcean, 6 y 7 de octubre,
20 inyecciones), con el detector corriendo serie por serie a 1 minuto y la
regla de emparejamiento de H-029 (la anomalia empieza entre el inicio de la
inyeccion y su fin mas 5 minutos). El script esta en
`scripts/deteccion/medir_csv.py`:

| Configuracion | Eventos | Dentro | Ruido | Inyecciones detectadas | `alta` dentro |
|---|---|---|---|---|---|
| mad-v1 (historia 5, sin piso) | 44 | 31 | 13 | 18 de 20 | 10 |
| Historia completa, sin piso | 38 | 31 | 7 | 18 de 20 | 10 |
| Historia completa + piso absoluto (**mad-v2**) | 37 | 30 | 7 | 18 de 20 | 10 |
| mad-v2 + piso relativo 0,5 % o 1 % | 37 | 30 | 7 | 18 de 20 | 3 |
| mad-v2 + piso relativo 2 % | 37 | 30 | 7 | 18 de 20 | 0 |
| mad-v2 + piso relativo 5 % | 37 | 30 | 7 | 18 de 20 | 0 |

Lo que dice:

- **El ruido lo quita la historia completa, no el piso** (13 a 7). Los seis
  falsos positivos que se van son los del arranque del CSV (00:07 del 6 de
  octubre, uno por columna), el mismo fenomeno del borde de la ventana que
  se vio en Azure. Los siete que quedan son de severidad baja: cinco de
  `carga_1m` y dos de disco y paginacion a la misma hora.
- **El piso absoluto no cambia nada en este CSV**: la CPU en espera del
  nodo de DigitalOcean nunca estuvo casi plana en cero y luego subio, que
  es lo que paso en Kaderno. Quita un evento de memoria (z = 4,1) que caia
  dentro de una inyeccion de cpu, y ninguna inyeccion deja de detectarse.
  Su efecto se ve en Azure, que es donde aparecio el problema.
- **El piso relativo no quita ningun falso positivo y destruye la escala
  de severidad.** La severidad es por decadas del puntaje (`detector.py`,
  H-033), y el disco llega a `alta` justamente porque su linea base es muy
  estrecha. Un piso relativo ensancha esa linea base: con 0,5 % el puntaje
  maximo del disco baja de 17 096 a 924 y las inyecciones de disco gradual
  quedan en `media`; desde 2 % no queda ninguna anomalia `alta`. Por eso
  esta en 0.

**Efecto en la severidad de las fracciones.** Con el piso de 0,01, una
fraccion que sube toda su escala (de 0 a 1) da como maximo z = 100, que es
`media`. La CPU y la memoria ya no pueden llegar a `alta`; en este CSV no
llegaban (maximos de 76 y 232), pero en Azure la memoria de las 09:34 si
llego. Es coherente con lo que dice `detector.py` de la severidad (mide lo
extraordinaria que es la desviacion para esa serie, no el impacto), pero es
un cambio visible en la interfaz y hay que decirlo en el informe.

La version del modelo pasa a `mad-v2` con este cambio y con el de la
historia completa (ver `detector.py`): las anomalias abiertas antes llevan
`mad-v1` y se pueden separar en la evaluacion.

**Swap y sistema de archivos: medido, no desplegado (mejora propuesta,
mad-v3).** `system_paging_usage` y `system_filesystem_usage` estan en bytes
y no llevan piso. Un piso nuevo cambiaria el metodo y partiria otra vez la
ventana de H-029, asi que se midio fuera de linea con
`scripts/deteccion/medir_piso.py` sobre `zenit-export-2026-10-27` (nodo de
laboratorio de Azure, 16 al 27 de octubre, 164 inyecciones). El script
reproduce el servicio: misma apertura (`detectar_eventos`), bloqueo de la
serie mientras su anomalia sigue abierta y cierre con la linea base
congelada y el piso del 5 %. Sin piso reproduce minuto a minuto las
anomalias reales de esas dos senales en Azure (3 de 3 del swap y 10 de 10
del disco en la ventana de mad-v2, y tambien las 6, 95, 116 y 305 de
mad-v1).

Campana completa, cada racha critica cuenta como una anomalia (en once
dias, una sola anomalia que no cierra taparia todo lo demas):

| Senal | Piso | Anomalias | Ruido | Sobre otra inyeccion | Detectadas | `alta` dentro |
|---|---|---|---|---|---|---|
| swap | sin piso | 16 | 1 | 0 | memoria 15 de 30 | 0 |
| swap | 8, 16 o 64 MiB | 0 | 0 | 0 | memoria 0 de 30 | 0 |
| disco | sin piso | 69 | 3 | 2 | disco 64 de 74 (gradual 26, abrupto 38) | 64 |
| disco | 64 o 128 MiB | 67 | 1 | 1 | disco 65 de 74 (gradual 26, abrupto 39) | 0 |
| disco | 256 MiB | 66 | 0 | 1 | disco 65 de 74 (gradual 26, abrupto 39) | 0 |

Ventana de mad-v2 (26 de octubre 14:01 UTC al 27 a las 23:00), con
bloqueo y cierre como el servicio: el swap abre 3 anomalias sin piso y 0
con cualquier piso; el disco abre 10 con o sin piso y detecta las 10
inyecciones de disco de la ventana (4 graduales y 6 abruptas). Ruido: 0 en
las dos senales.

Lo que dice:

- **En Azure el swap no es ruido.** 15 de sus 16 anomalias caen dentro de
  una inyeccion de memoria: la 530 del 26 de octubre, por ejemplo, empezo a
  las 15:08, cinco minutos despues de la inyeccion de memoria de las 15:03,
  y en H-029 cuenta como correcta, no como falso positivo. Lo que se mueve
  son decenas de KB (el nodo casi no usa el swap), asi que cualquier piso
  probado, desde 8 MiB, las borra todas. **No cuesta exhaustividad:** las
  15 inyecciones que ve el swap tambien las ve `system_memory_utilization`
  con mad-v2 (28 de 30 por si sola; el swap no agrega ninguna).
- **El ruido del swap esta en Kaderno**, y la exportacion no lo trae (es
  solo del nodo de Azure). La 531, a la misma hora que la 530, es de
  Kaderno, y alli no hay inyecciones: todo lo que abre es falso positivo
  (10 anomalias de swap con mad-v2 hasta el 27). Con la evidencia de las que
  quedaron abiertas en mad-v1 (la 71: 262 KB sobre una mediana de 111 MiB),
  el piso de 8 MiB las quitaria, pero eso hay que medirlo con una
  exportacion de Kaderno antes de afirmarlo en el informe.
- **En el disco el piso casi no quita ruido y si quita la severidad.** Tres
  eventos de ruido en once dias (0,3 por dia) y ninguno en la ventana de
  mad-v2. Con 64 MiB quedan uno, con 256 MiB ninguno, y la deteccion no
  cambia (65 de 74). Pero el puntaje del disco depende de lo plana que es
  su linea base: con 64 MiB la mediana del puntaje dentro de una inyeccion
  baja de 4487 a 204 y las 64 `alta` pasan a `media`, lo mismo que se vio
  con el piso relativo. Ningun piso probado deja una inyeccion de disco en
  `alta`.

**Recomendacion para el informe (mad-v3, no desplegada):** piso absoluto de
**8 MiB para `system_paging_usage`**, condicionado a confirmar con una
exportacion de Kaderno que quita su ruido; no cuesta ninguna deteccion de
memoria en Azure. **Sin piso para `system_filesystem_usage`**: su ruido es
bajo y cualquier piso que lo quite borra la severidad `alta` de los llenados
de disco. Si se quisiera quitar ese ruido, el camino no es el piso de la
escala sino el criterio de severidad, y queda fuera de esta entrega.

De donde salen estos nombres
----------------------------

Verificados el 19 de octubre de 2026 contra la documentacion del receptor
`hostmetrics` y del conector `spanmetrics` de la version que corre el
laboratorio (`otel/opentelemetry-collector-contrib:0.116.1`, fijada en
`laboratorio/compose.yml`), mas la regla de conversion de nombres de
`docs/esquema-medicion.md`, seccion 3.

Lo que esa verificacion corrigio del primer intento de este catalogo:

| Se habia puesto | Problema | Quedo |
|---|---|---|
| `system_filesystem_utilization` | Metrica opcional, y `collector.yaml` solo activa las de cpu y memoria: no existe en esta plataforma. Ademas `utilization` no tiene dimension `state` | `system_filesystem_usage` (bytes, no fraccion) |
| `system_paging_utilization` | Opcional y tampoco activada | `system_paging_usage` |
| `calls` | El conector agrega su espacio de nombres por defecto, `traces.span.metrics` | `traces_span_metrics_calls` |
| `system_paging_operations` con solo `direction` | Tiene tambien `type` (`major`/`minor`): sin separarlas se promedian dos cosas distintas | agrupada por `type` |
| `calls` filtrando `status_code=STATUS_CODE_ERROR` | La documentacion del conector muestra dos convenciones distintas para ese valor (`Error` y `STATUS_CODE_*`) segun la version y una compuerta de funcionalidad | agrupada por `status_code`, sin fijar el valor |

**Dos metricas opcionales si estan activadas** en `collector.yaml`:
`system.cpu.utilization` y `system.memory.utilization`. Si alguien activara
ademas `system.filesystem.utilization`, la senal de disco podria volver a
la fraccion, que es la que usaba el cuaderno (`disco_used_frac`). No
conviene hacerlo durante la campana de Azure que arranco el 16 de octubre:
cambiaria a mitad de camino lo que contiene el conjunto con el que se evalua
H-029.

Aun asi, los nombres solo se confirman del todo contra la plataforma:

    docker compose exec api python -m zenit.deteccion --senales

Dice, activo por activo, que senales del catalogo existen y cuales no, y
ademas que metricas estan llegando que ninguna senal vigila. Lo que aparezca
se corrige aqui, en una linea, sin tocar el detector.

Lo que este catalogo no cubre todavia
-------------------------------------

**Latencia.** La duracion de las llamadas llega como histograma, que el
esquema parte en dos tablas (`traces_span_metrics_duration_count` y
`traces_span_metrics_duration_sum`). La latencia media es
`d(sum)/d(count)`: un cociente de dos tasas, es decir dos series
combinadas, y todo este archivo trabaja con una serie a la vez. Queda
declarado como hueco: la inyeccion de tipo `latencia` del laboratorio no la
detecta este metodo, y eso tiene que salir asi en la evaluacion de H-029.

**Caidas.** Una caida de servicio baja `calls` en vez de subirlo, y el
metodo solo mira desviaciones hacia arriba (ver la nota de `detector.py`).
Es la misma limitacion que el cuaderno midio contra la verdad de referencia
(`caida`: 0.398 de las filas en critico, contra 0.98 de las demas) y la
razon por la que el ERS pide H-034.
"""
import re
from dataclasses import dataclass, field
from typing import Mapping, Optional, Sequence

# Clases de senal. La clase decide si la serie se usa tal cual o si antes
# hay que derivarla.
MEDIDA = "medida"          # gauge: el valor ya es el nivel (utilizacion, carga)
ACUMULADA = "acumulada"    # contador que solo crece: se convierte a tasa/segundo

# Particiones de disco (ver "Particiones" arriba): letras y un numero al
# final (sda1, vda1, xvda1), o un numero, `p` y un numero (nvme0n1p1).
PARTICION = r"[a-z]+\d+|.*\dp\d+"

# Piso absoluto de la escala para las fracciones de 0 a 1 (`*_utilization`):
# un punto porcentual. Ver "Piso de la escala" arriba.
PISO_FRACCION = 0.01


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
    # Series que no se evaluan: dimension -> expresion regular contra el
    # valor completo. Las particiones de un disco, por ejemplo (`PARTICION`).
    excluir: Mapping[str, str] = field(default_factory=dict)
    # Piso absoluto de la escala de la linea base, en las unidades de la
    # serie. 0 = solo el piso relativo. Ver "Piso de la escala" arriba.
    piso_escala: float = 0.0

    def excluye(self, etiquetas: Mapping[str, str]) -> bool:
        """Si la serie con estas etiquetas queda fuera por `excluir`."""
        return any(
            re.fullmatch(patron, str(etiquetas.get(dimension, ""))) is not None
            for dimension, patron in self.excluir.items()
            if dimension in etiquetas
        )

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
        piso_escala=PISO_FRACCION,
        descripcion="CPU en espacio de usuario, promedio de los nucleos",
        fallas=("cpu",), equivale_a="cpu_user",
    ),
    Senal(
        metrica="system_cpu_utilization", clase=MEDIDA, filtros={"state": "system"},
        piso_escala=PISO_FRACCION,
        descripcion="CPU en espacio de sistema, promedio de los nucleos",
        fallas=("cpu",), equivale_a="cpu_system",
    ),
    Senal(
        metrica="system_cpu_utilization", clase=MEDIDA, filtros={"state": "wait"},
        piso_escala=PISO_FRACCION,
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
        piso_escala=PISO_FRACCION,
        descripcion="Memoria en uso",
        fallas=("memoria",), equivale_a="memoria_used",
    ),
    # `usage`, no `utilization`: la segunda es opcional y no esta activada.
    # Sum no monotonica (el nivel actual), de ahi MEDIDA. Por dispositivo,
    # que es el nombre del archivo de intercambio.
    Senal(
        metrica="system_paging_usage", clase=MEDIDA, filtros={"state": "used"},
        agrupar="device",
        descripcion="Espacio de intercambio en uso, por dispositivo",
        fallas=("memoria",),
    ),
    # --------------------------------------------------------------- disco
    # Por punto de montaje: un sistema de archivos que se llena es local a
    # su montaje, y promediar todos los montajes lo diluye.
    #
    # `usage` da bytes ocupados, no la fraccion que usaba el cuaderno: la
    # metrica de fraccion (`system.filesystem.utilization`) es opcional y
    # `collector.yaml` no la activa. Para el puntaje z da igual, porque mide
    # la desviacion contra la propia linea base de la serie; lo que cambia es
    # que el valor de la evidencia va en bytes. Filtrar `state=used` aqui no
    # es opcional: sin el se promedian used, free y reserved, cuya suma es
    # casi constante, y la senal desaparece.
    Senal(
        metrica="system_filesystem_usage", clase=MEDIDA, filtros={"state": "used"},
        agrupar="mountpoint",
        descripcion="Bytes ocupados del sistema de archivos, por punto de montaje",
        fallas=("disco",), equivale_a="disco_used_frac",
    ),
    # Sin las particiones: cada escritura se ve en el disco y en su
    # particion, y abria dos anomalias por evento ("Particiones", arriba).
    Senal(
        metrica="system_disk_io", clase=ACUMULADA, filtros={"direction": "write"},
        agrupar="device", excluir={"device": PARTICION},
        descripcion="Bytes escritos por segundo, por dispositivo",
        fallas=("disco",), equivale_a="disco_bytes_s",
    ),
    Senal(
        metrica="system_disk_io", clase=ACUMULADA, filtros={"direction": "read"},
        agrupar="device", excluir={"device": PARTICION},
        descripcion="Bytes leidos por segundo, por dispositivo",
        fallas=("disco",),
    ),
    # ----------------------------------------------------------- paginacion
    # Agrupada por `type` (major/minor): un fallo mayor va a disco y es el
    # que indica presion de memoria; uno menor se resuelve en RAM. Sin
    # separarlos se promedian dos cosas de magnitud muy distinta.
    Senal(
        metrica="system_paging_operations", clase=ACUMULADA, filtros={"direction": "page_in"},
        agrupar="type",
        descripcion="Paginas traidas por segundo, por tipo de fallo",
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
    # Llamadas por segundo, una serie por codigo de estado. El cuaderno usaba
    # la proporcion de errores contra el total (`tasa_error_relativa`); eso
    # es un cociente de dos series y queda para cuando el detector sepa
    # combinarlas. La tasa absoluta por codigo si sirve, y es lo mas cercano
    # a una caida que este metodo puede ver hacia arriba.
    #
    # Se agrupa por `status_code` en vez de filtrarlo porque el valor exacto
    # depende de la version del conector y de una compuerta de
    # funcionalidad: asi la serie de errores se evalua sola sea `Error` o
    # `STATUS_CODE_ERROR`, y el valor real queda escrito en la columna
    # `dimensiones` de la anomalia.
    #
    # El nombre lleva el espacio de nombres por defecto del conector
    # (`traces.span.metrics`), que `collector.yaml` no cambia.
    Senal(
        metrica="traces_span_metrics_calls", clase=ACUMULADA, agrupar="status_code",
        descripcion="Llamadas por segundo, por codigo de estado",
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
