"""El metodo de deteccion y el trabajo de un ciclo (H-032).

El punto de entrada esta en `__main__.py`, como en zenit.descubrimiento:

    python -m zenit.deteccion --cada 60        lo corre el servicio detector
    python -m zenit.deteccion --senales        que senales del catalogo existen

Cada ciclo (mismo patron que zenit.agregacion.Agregador):

1. Mira que fuentes estan reportando (zenit.salud, la misma regla que
   responde `GET /api/fuentes`). Un activo cuya fuente se callo no genera
   anomalias por eso (H-033, cuarto criterio).
2. Cierra las anomalias cuya serie volvio a lo normal el tiempo exigido
   (zenit.deteccion.cierre, H-033).
3. Lista los activos vigentes en el catalogo (zenit.catalogo.listar_activos).
4. Para cada activo y cada senal del catalogo (zenit.deteccion.senales),
   lee su serie por la capa de repositorio (RepositorioSeries.serie, nunca
   SQL directo a InfluxDB) a resolucion de 1 minuto.
5. Aplica la linea base por activo (mediana + MAD, cortes 1.5 / 3.5 de
   Iglewicz & Hoaglin) del cuaderno `Modelo criticidad`, serie por serie.
6. Abre una anomalia solo si la regla de persistencia se cumple: PERSISTENCIA
   puntos seguidos fuera de rango, nunca un pico aislado (criterio 3 de
   H-032). Si ya hay una anomalia en curso para ese (activo, metrica,
   dimensiones), no abre otra.

La severidad con la que se abre (H-033, primer criterio) esta mas abajo, en
`severidad`; el cierre automatico y su justificacion, en `cierre.py`.

Que se vigila y con que dimensiones esta en `senales.py`, no aqui: este
archivo no sabe nada de metricas concretas. Lo de alla tambien explica por
que los contadores acumulados se convierten a tasa antes de la linea base.

Limitacion heredada del cuaderno, documentada y no escondida (ver
arquitectura-zenit-v0, seccion de estados de una anomalia): el metodo solo
mira desviaciones hacia arriba. Una caida de servicio que se manifiesta como
una *baja* de una senal no la detecta este metodo; lo cubre H-034
(deteccion no supervisada) cuando llegue a la misma salida.

version_modelo (H-036) describe el metodo y sus parametros, por ejemplo
"mad-v2 W=60 z=3.5 m=3": W es la ventana de la linea base, z el corte
critico y m la persistencia exigida.

Que cambio de mad-v1 a mad-v2 (arreglos vistos en Azure el 21 de octubre):

- **Historia completa.** Un punto solo se evalua si tiene `ventana` puntos
  de historia antes (60 minutos). Con mad-v1 bastaban 5, y los puntos del
  principio de la ventana de consulta, con una linea base de 5 a 59
  minutos, abrian anomalias de severidad baja con inicio de casi 24 horas
  antes (ids 26, 27, 28, 30 y 32 en Azure).

Lo que no cambia la version, porque no cambia el metodo sino el servicio:
`MAX_ANTIGUEDAD` (abajo) y el `fin` del cierre (`cierre.py`).
"""
import logging
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Mapping, Optional, Sequence

from zenit import catalogo, salud
from zenit.deteccion import cierre
from zenit.deteccion.senales import ACUMULADA, SENALES, Senal, a_tasa, preparar, puntos_extra
from zenit.repositorio.contrato import ErrorConsulta, RepositorioSeries

log = logging.getLogger("zenit.deteccion")

# Cortes de Iglewicz & Hoaglin para el z-score robusto (seccion 3 del
# cuaderno `Modelo criticidad`): no son umbrales de infraestructura, son
# parametros estadisticos del metodo. Un administrador podria ajustarlos por
# cliente (HU-52) sin tocar el resto del codigo.
Z_NORMAL = 1.5
Z_CRITICO = 3.5
# Cuantos puntos (a 1 minuto) hacen falta para calcular la linea base de un
# activo. RF-DET-05 pide reentrenarla periodicamente: aqui se recalcula cada
# ciclo, sobre la ventana mas reciente, en vez de guardarse en disco.
VENTANA_DEFECTO = 60
# Regla de persistencia (H-032, criterio 3): cuantos puntos seguidos fuera de
# rango hacen falta para abrir una anomalia. Uno solo (m=1) abriria con un
# pico aislado; esto es justo lo que la guia de deteccion pide evitar.
PERSISTENCIA_DEFECTO = 3
# Historia exigida: antes de evaluar un punto hace falta la ventana completa (mad-v2).
# Con mad-v1 era 5 puntos, y el principio de cada consulta de 24 h se evaluaba
# contra una linea base de pocos minutos: en Azure eso abria anomalias de
# severidad baja con inicio de casi un dia antes, cada pocos minutos, porque
# el principio de la ventana de consulta se mueve con el reloj. Con la
# ventana completa, todos los puntos se miden contra 60 minutos de historia,
# igual que el ultimo. El parametro `historia_minima` de `detectar_eventos`
# es la ventana si no se dice otra cosa.

# Antiguedad maxima del inicio de una anomalia para abrirla en el ciclo normal
# (minutos). El ciclo lee 24 h de cada serie y se queda con el evento mas
# reciente de cada una; sin este limite, el primer ciclo despues de cada
# arranque abria de golpe todo lo del dia anterior (25 anomalias el 21 de
# octubre en Azure, con inicios repartidos en el dia) y el cierre del minuto
# siguiente les ponia de `fin` la hora del arranque. 30 minutos cubre de sobra
# la persistencia (3 min) y un ciclo atrasado, y es menos que el hueco de 45
# minutos entre inyecciones de la campana. Se cambia con la variable
# MAX_ANTIGUEDAD o con --max-antiguedad. La deteccion hacia atras, si se
# necesita, es `--rellenar`, que corre un ciclo sin este limite.
MAX_ANTIGUEDAD_DEFECTO = 30

# La deteccion corre sobre el resumen por minuto, no sobre el crudo de 10 s
# (arquitectura, seccion 4). RepositorioSeries no recibe la resolucion como
# parametro: la deduce del tamano del rango (H-019), asi que el rango de
# consulta es lo que la elige, y queda encajonado por dos lados:
#
#   - mas de 24 h (UMBRAL_CRUDO), o responde al nivel crudo de 10 s;
#   - 25 h como maximo, porque el paso se calcula para dejar unos 1500
#     puntos: a 25 h justas da 60 s, y un rango mayor salta al paso
#     siguiente de la lista (120 s) y los puntos dejarian de ser de un
#     minuto.
#
# 24 h 30 min queda dentro de los dos limites con margen. Cambiar este valor
# sin mirar PASOS_S y UMBRAL_CRUDO en repositorio/influx.py rompe la
# resolucion en silencio, y por eso cada respuesta se verifica abajo contra
# RESOLUCION_ESPERADA_S.
RANGO_CONSULTA = timedelta(hours=24, minutes=30)
RESOLUCION_ESPERADA_S = 60


# --------------------------------------------------------------------- metodo
@dataclass(frozen=True)
class Baseline:
    """Mediana y escala (dispersion robusta) de una ventana de una serie."""

    mediana: float
    escala: Optional[float]      # None si la ventana es demasiado plana


def calcular_baseline(valores: Sequence[float]) -> Baseline:
    """Linea base de una ventana: mediana y MAD reescalado, con respaldo en
    la desviacion estandar cuando el MAD es cero (senal casi constante).

    Es la misma formula de `calcular_baseline` en el cuaderno `Modelo
    criticidad`, adaptada a una sola serie a la vez (aqui la deteccion ya
    esta separada por activo, metrica y dimensiones; agrupar por activo, como
    hace el cuaderno, es responsabilidad de quien llama).
    """
    mediana = statistics.median(valores)
    mad = statistics.median(abs(v - mediana) for v in valores) * 1.4826
    desviacion = statistics.pstdev(valores) if len(valores) > 1 else 0.0
    escala = mad if mad > 0 else desviacion
    return Baseline(mediana=mediana, escala=escala if escala and escala > 0 else None)


def puntaje_z(valor: float, baseline: Baseline) -> float:
    """z = (valor - mediana) / escala. Una ventana sin escala (metrica
    constante en su historial) no aporta senal de desviacion: z = 0, no un
    error, siguiendo el mismo criterio del cuaderno."""
    if baseline.escala is None:
        return 0.0
    return (valor - baseline.mediana) / baseline.escala


def clasificar(z: float, z_normal: float = Z_NORMAL, z_critico: float = Z_CRITICO) -> str:
    """'bueno' / 'normal' / 'critico', igual que `clasificar_estado` en el
    cuaderno. Solo mira desviaciones hacia arriba (z positivo); ver la nota
    de limitacion al inicio del archivo."""
    if z >= z_critico:
        return "critico"
    if z >= z_normal:
        return "normal"
    return "bueno"


# Severidad (H-033, primer criterio).
#
# Lo que se mide no es el puntaje suelto, sino **cuantas veces supera el corte
# critico**: exceso = z / z_critico. Asi el corte del cuaderno queda como
# unidad y la escala no introduce un umbral nuevo. Los tramos son decadas:
#
#   baja      1 <= exceso < 10     (z de 3.5 a 35)
#   media    10 <= exceso < 100    (z de 35 a 350)
#   alta          exceso >= 100    (z >= 350)
#
# Por que decadas y no incrementos. El primer intento sumaba al corte los
# mismos pasos que separan bueno, normal y critico (+1.5 y +3.5, o sea cortes
# en 5 y 7). Medido sobre las 42 anomalias que el detector abre en el
# `data.csv` del laboratorio, con la verdad de referencia del propio archivo
# para saber cuales cayeron dentro de una inyeccion:
#
#   z observado: de 3.6 a 17 096; mediana 39.6 dentro de una inyeccion y
#   9.7 fuera. Son cuatro ordenes de magnitud, y unos cortes lineales en 5 y 7
#   no dividen ese rango: casi todo queda por encima.
#
#   | Criterio            | Dentro (29)            | Ruido (13)            | Acierto de 'alta' |
#   |---------------------|------------------------|-----------------------|-------------------|
#   | lineal (+1.5 / +3.5)| 4 baja, 1 media, 24 alta | 4 baja, 1 media, 8 alta | 24 de 32 (75 %) |
#   | decadas (x10/x100)  | 14 baja, 5 media, 10 alta | 12 baja, 1 media, 0 alta | 10 de 10 (100 %) |
#
# Con decadas el ruido se va entero a `baja` y ninguna anomalia fuera de una
# inyeccion llega a `alta`. La eleccion no esta ajustada al dato: x20/x200
# tambien da 100 % y x5/x50 da 92 %; una decada es una decada, no una
# constante calibrada. El puntaje crece de forma multiplicativa (depende de lo
# estrecha que sea la linea base del activo), y agrupar por orden de magnitud
# es lo que corresponde a una cantidad multiplicativa.
#
# Lo que hay que saber al leerla. La severidad depende de lo estrecha que sea
# la linea base de la serie, no solo de lo grave que sea el fallo: en la
# medicion anterior las 10 anomalias `alta` salieron todas de
# `disco_used_frac`, porque un sistema de archivos que normalmente no se mueve
# produce puntajes enormes en cuanto se mueve. Es correcto en terminos
# estadisticos (es una desviacion extraordinaria para esa serie) y es
# inherente a cualquier severidad construida sobre el puntaje z, no algo que
# agreguen las decadas. Conviene decirlo asi en el informe en vez de leer la
# severidad como una medida de impacto operativo.
#
# La severidad se fija al abrir y no se recalcula: describe con que fuerza
# irrumpio la desviacion. Como el puntaje queda guardado en la fila, cambiar
# este criterio despues no obliga a volver a detectar nada: la severidad de lo
# ya detectado se recalcula con una consulta.
EXCESO_MEDIA = 10
EXCESO_ALTA = 100


def severidad(z: float, z_critico: float = Z_CRITICO) -> str:
    """'baja' | 'media' | 'alta' para una desviacion ya clasificada critica."""
    exceso = z / z_critico
    if exceso >= EXCESO_ALTA:
        return "alta"
    if exceso >= EXCESO_MEDIA:
        return "media"
    return "baja"


@dataclass(frozen=True)
class Evento:
    """Un punto donde la regla de persistencia se cumplio: aqui abre una
    anomalia. `indice` es la posicion dentro de la lista de puntos que se le
    paso a `detectar_eventos`."""

    indice: int
    momento: datetime
    puntaje: float
    severidad: str
    evidencia: dict = field(default_factory=dict)


def detectar_eventos(
    puntos: Sequence[tuple],
    *,
    ventana: int = VENTANA_DEFECTO,
    persistencia: int = PERSISTENCIA_DEFECTO,
    z_normal: float = Z_NORMAL,
    z_critico: float = Z_CRITICO,
    historia_minima: Optional[int] = None,
) -> list:
    """Recorre una serie ordenada por tiempo `[(momento, valor), ...]` y
    devuelve un Evento por cada racha de `persistencia` puntos seguidos en
    'critico' que arranca (criterio 3 de H-032).

    Por diseno da como maximo un evento por racha, sin importar cuanto dure
    la racha despues: la prueba de la guia (serie plana con un escalon
    construido a mano) debe dar exactamente una anomalia, ni cero ni diez.

    Un punto solo se clasifica si tiene `historia_minima` puntos antes, que
    por defecto es la ventana completa (mad-v2, ver "Historia exigida" arriba). Los
    puntos del principio de la serie no se evaluan y no pueden abrir nada.
    """
    if historia_minima is None:
        historia_minima = ventana
    valores = [v for _, v in puntos]
    n = len(valores)
    estados: list = [None] * n
    puntajes: list = [0.0] * n
    bases: list = [None] * n

    for i in range(n):
        historia = valores[max(0, i - ventana):i]
        if len(historia) < max(2, historia_minima):
            continue    # sin la linea base completa: no se clasifica
        baseline = calcular_baseline(historia)
        bases[i] = baseline
        z = puntaje_z(valores[i], baseline)
        puntajes[i] = z
        estados[i] = clasificar(z, z_normal, z_critico)

    eventos = []
    racha = 0
    for i, estado in enumerate(estados):
        racha = racha + 1 if estado == "critico" else 0
        if racha != persistencia:
            continue    # solo se abre al llegar exactamente a la persistencia exigida
        baseline = bases[i]
        momento, valor = puntos[i]
        eventos.append(Evento(
            indice=i,
            momento=momento,
            puntaje=puntajes[i],
            severidad=severidad(puntajes[i], z_critico),
            evidencia={
                "ventana": ventana,
                "persistencia": persistencia,
                "valor_observado": valor,
                "valor_esperado": baseline.mediana,
                # La linea base del momento de abrir, congelada. Es lo que el
                # cierre automatico vuelve a usar para decidir si la
                # desviacion ceso, en vez de recalcular una ventana que ya se
                # habria adaptado al fallo (zenit.deteccion.cierre).
                "linea_base": {"mediana": baseline.mediana, "escala": baseline.escala},
                "metodo": "mad-v2",
            },
        ))
    return eventos


def version_modelo(ventana: int = VENTANA_DEFECTO, z_critico: float = Z_CRITICO,
                   persistencia: int = PERSISTENCIA_DEFECTO) -> str:
    return f"mad-v2 W={ventana} z={z_critico} m={persistencia}"


# ------------------------------------------------------------------ evaluacion
@dataclass(frozen=True)
class Hallazgo:
    """Un evento ya atribuido a una serie concreta: lo que se convierte en
    una fila de `anomalias`."""

    senal: Senal
    dimensiones: dict
    evento: Evento


def _a_momento(valor) -> datetime:
    """Los puntos de Serie vienen como [ms_epoca, valor] (contrato.Serie).
    Si ya es datetime (por ejemplo, en una prueba), se deja igual."""
    if isinstance(valor, datetime):
        return valor if valor.tzinfo else valor.replace(tzinfo=timezone.utc)
    return datetime.fromtimestamp(valor / 1000, timezone.utc)


def evaluar_senal(
    senal: Senal,
    respuesta,
    *,
    ventana: int = VENTANA_DEFECTO,
    persistencia: int = PERSISTENCIA_DEFECTO,
    z_normal: float = Z_NORMAL,
    z_critico: float = Z_CRITICO,
    resolucion_esperada_s: int = RESOLUCION_ESPERADA_S,
) -> list:
    """Evalua una Respuesta del repositorio y devuelve los hallazgos.

    Es la parte del detector que no toca ni la red ni la base de datos: recibe
    lo que respondio el repositorio y devuelve lo que habria que escribir.

    Dos guardas antes del metodo:

    - **Resolucion.** Si la respuesta no vino a un punto por minuto, no se
      evalua. La linea base y la ventana estan expresadas en minutos (W=60 son
      60 minutos), asi que puntos de 10 s o de 2 minutos cambiarian
      silenciosamente lo que significa la ventana y el modelo dejaria de ser
      comparable (arquitectura: "un modelo entrenado sobre otra resolucion no
      es comparable").
    - **Series omitidas.** El repositorio devuelve como maximo 8 series por
      consulta, un limite pensado para los colores de una grafica (H-019). Lo
      que quede fuera no se vigila, y eso tiene que verse en los registros en
      vez de pasar callado.
    """
    if respuesta.intervalo_s != resolucion_esperada_s:
        log.warning("%s: la respuesta vino a %s s por punto, no a %s s; no se evalua "
                    "(revisar RANGO_CONSULTA contra PASOS_S y UMBRAL_CRUDO)",
                    senal.clave, respuesta.intervalo_s, resolucion_esperada_s)
        return []
    if getattr(respuesta, "series_omitidas", 0):
        log.warning("%s: %s series quedaron fuera del limite del repositorio y no se vigilan",
                    senal.clave, respuesta.series_omitidas)

    hallazgos = []
    for serie in respuesta.series:
        puntos = preparar(senal, [(_a_momento(t), float(v)) for t, v in serie.puntos])
        if len(puntos) < ventana + persistencia:
            continue
        eventos = detectar_eventos(
            puntos, ventana=ventana, persistencia=persistencia,
            z_normal=z_normal, z_critico=z_critico,
        )
        if not eventos:
            continue
        # Las dimensiones de la anomalia son las que fijo la senal mas las
        # que abrio la agrupacion: asi la fila de `anomalias` dice exactamente
        # de que serie se trata (state=used y mountpoint=/, por ejemplo).
        dimensiones = {**dict(senal.filtros), **dict(serie.etiquetas)}
        evidencia_senal = {
            "senal": senal.clave,
            "clase": senal.clase,
            "resolucion_s": respuesta.intervalo_s,
        }
        evento = eventos[-1]    # el mas reciente de la ventana
        hallazgos.append(Hallazgo(
            senal=senal,
            dimensiones=dimensiones,
            evento=Evento(
                indice=evento.indice,
                momento=evento.momento,
                puntaje=evento.puntaje,
                severidad=evento.severidad,
                evidencia={**evento.evidencia, **evidencia_senal},
            ),
        ))
    return hallazgos


# -------------------------------------------------------------------- servicio
class Detector:
    def __init__(
        self,
        repositorio: RepositorioSeries,
        pg_url: str,
        *,
        senales: Sequence[Senal] = SENALES,
        ventana: int = VENTANA_DEFECTO,
        persistencia: int = PERSISTENCIA_DEFECTO,
        z_normal: float = Z_NORMAL,
        z_critico: float = Z_CRITICO,
        rango_consulta: timedelta = RANGO_CONSULTA,
        normalidad_para_cerrar: int = cierre.NORMALIDAD_PARA_CERRAR,
        max_antiguedad: int = MAX_ANTIGUEDAD_DEFECTO,
    ):
        self.repositorio = repositorio
        self.pg_url = pg_url
        self.senales = tuple(senales)
        self.ventana = ventana
        self.persistencia = persistencia
        self.z_normal = z_normal
        self.z_critico = z_critico
        self.rango_consulta = rango_consulta
        self.normalidad_para_cerrar = normalidad_para_cerrar
        self.max_antiguedad = timedelta(minutes=max_antiguedad)
        self._version_modelo = version_modelo(ventana, z_critico, persistencia)

    # -------------------------------------------------------------- ciclo
    def ciclo(self, ahora: Optional[datetime] = None, *, rellenar: bool = False) -> dict:
        """Un ciclo: primero cierra lo que volvio a lo normal, despues abre lo
        nuevo. En ese orden, para que una serie que se recupero y volvio a
        caer en el mismo ciclo cierre su anomalia vieja antes de abrir la
        nueva, en vez de quedar bloqueada por la deduplicacion.

        Solo abre anomalias cuyo inicio esta dentro de los ultimos
        `max_antiguedad` minutos (ver MAX_ANTIGUEDAD_DEFECTO). Con
        `rellenar=True` no hay limite: abre lo que encuentre en las 24 h, una
        anomalia por serie como maximo (la mas reciente). Es para un uso
        manual (`--rellenar`), no para el servicio.
        """
        ahora = ahora or datetime.now(timezone.utc)
        desde = ahora - self.rango_consulta
        no_antes_de = None if rellenar else ahora - self.max_antiguedad
        activos = senales_leidas = anomalias_abiertas = 0
        with catalogo.conectar(self.pg_url) as conexion:
            degradados = self._degradados(conexion, ahora)
            anomalias_cerradas = self._cerrar(conexion, degradados, desde, ahora)
            for fila in catalogo.listar_activos(conexion, incluir_retirados=False):
                activo = fila["id"]
                activos += 1
                if activo in degradados:
                    # Cuarto criterio de H-033: una fuente callada no es una
                    # anomalia del activo. La deteccion de eso es H-021 y ya
                    # sale por /api/fuentes y en el estado del inventario.
                    continue
                disponibles = self._metricas_de(activo)
                for senal in self.senales:
                    if disponibles is not None and senal.metrica not in disponibles:
                        continue    # esa metrica no llega de este activo
                    senales_leidas += 1
                    anomalias_abiertas += self._revisar(conexion, activo, senal, desde, ahora,
                                                        no_antes_de)
        return {
            "activos": activos,
            "degradados": len(degradados),
            "senales": senales_leidas,
            "anomalias_abiertas": anomalias_abiertas,
            "anomalias_cerradas": anomalias_cerradas,
        }

    # ------------------------------------------------------ fuentes (H-021)
    def _degradados(self, conexion, ahora: datetime) -> set:
        """Los activos cuya fuente no esta reportando.

        La regla de degradado vive en zenit.salud y en ningun otro lado (mas
        de 5 minutos sin recibir), y es la misma que responde
        `GET /api/fuentes`. Aqui se lee de PostgreSQL con esas funciones en
        vez de llamar a la API: el detector ya tiene la conexion, y una copia
        de la regla seria una segunda version de la verdad.
        """
        try:
            filas = salud.fuentes_pg(conexion)
        except Exception as e:  # noqa: BLE001 - sin fuentes se sigue detectando
            log.warning("no se pudo leer el estado de las fuentes: %s", e)
            return set()
        ultimas = salud.ultima_recepcion_por_activo(filas)
        degradados = {activo for activo, ultima in ultimas.items()
                      if salud.estado_de(ultima, ahora) != "ok"}
        # Un activo que nunca reporto no tiene fila en `fuentes` y no aparece
        # aqui, pero tampoco tiene puntos: la pasada de apertura no encuentra
        # serie y no abre nada igual.
        if degradados:
            log.info("fuentes no disponibles, no se abren anomalias: %s", ", ".join(sorted(degradados)))
        return degradados

    # -------------------------------------------------------------- cierre
    def _cerrar(self, conexion, degradados: set, desde: datetime, hasta: datetime) -> int:
        """Cierra las anomalias cuya serie volvio a lo normal el tiempo
        exigido, medido contra la linea base congelada al abrirlas. El motivo
        de usar la congelada esta en el encabezado de `cierre.py`."""
        try:
            pendientes = cierre.abiertas(conexion)
        except Exception as e:  # noqa: BLE001 - la apertura sigue aunque el cierre falle
            log.warning("no se pudieron listar las anomalias abiertas: %s", e)
            return 0

        cerradas = 0
        for a in pendientes:
            if a.activo in degradados:
                continue    # sin datos no se puede afirmar que la desviacion ceso
            if not a.evaluable:
                log.warning("anomalia %s sin linea base en su evidencia: no se cierra sola", a.id)
                continue
            # Filtrando por TODAS sus dimensiones se recupera exactamente la
            # serie de la anomalia, sin agrupar y sin mirar el catalogo.
            try:
                respuesta = self.repositorio.serie(
                    a.activo, a.metrica, desde, hasta, filtros=a.dimensiones or None,
                )
            except ErrorConsulta as e:
                log.warning("anomalia %s: no se pudo releer %s %s: %s", a.id, a.metrica, a.dimensiones, e)
                continue
            except Exception as e:  # noqa: BLE001 - una anomalia no tumba el ciclo
                log.warning("anomalia %s: no se pudo releer la serie: %s", a.id, e)
                continue
            if respuesta.intervalo_s != RESOLUCION_ESPERADA_S:
                log.warning("anomalia %s: la serie vino a %s s por punto; no se evalua el cierre",
                            a.id, respuesta.intervalo_s)
                continue
            puntos: list = []
            for serie in respuesta.series:
                puntos += [(_a_momento(t), float(v)) for t, v in serie.puntos]
            puntos.sort()
            if a.clase == ACUMULADA:
                puntos = a_tasa(puntos)
            fin = cierre.momento_de_cierre(
                puntos, a.mediana, a.escala,
                normalidad=self.normalidad_para_cerrar, z_critico=self.z_critico,
                despues_de=a.inicio,
            )
            if fin is None:
                continue
            if cierre.cerrar(conexion, a.id, fin):
                cerradas += 1
                log.info("anomalia cerrada: %s %s %s abierta %s, normal desde %s",
                         a.id, a.activo, a.metrica, a.inicio, fin)
        return cerradas

    def _metricas_de(self, activo: str) -> Optional[set]:
        """Las metricas que reporta este activo, para no consultar senales que
        no existen. None si no se pudo averiguar: en ese caso se intentan
        todas y cada una falla por su cuenta, sin tumbar el ciclo."""
        try:
            return set(self.repositorio.metricas_de(activo))
        except Exception as e:  # noqa: BLE001 - el ciclo sigue con las demas
            log.warning("no se pudieron listar las metricas de %s: %s", activo, e)
            return None

    def _revisar(self, conexion, activo: str, senal: Senal, desde: datetime, hasta: datetime,
                 no_antes_de: Optional[datetime] = None) -> int:
        try:
            respuesta = self.repositorio.serie(
                activo, senal.metrica, desde, hasta,
                agrupar=senal.agrupar, filtros=dict(senal.filtros) or None,
            )
        except ErrorConsulta as e:
            # Dimension o valor que no existe en esta plataforma: es un error
            # del catalogo de senales, no del activo. Se registra con la clave
            # de la senal para poder corregir senales.py.
            log.warning("%s en %s: %s", senal.clave, activo, e)
            return 0
        except Exception as e:  # noqa: BLE001 - un activo no tumba el ciclo
            log.warning("no se pudo leer %s de %s: %s", senal.clave, activo, e)
            return 0

        abiertas = 0
        for hallazgo in evaluar_senal(
            senal, respuesta, ventana=self.ventana, persistencia=self.persistencia,
            z_normal=self.z_normal, z_critico=self.z_critico,
        ):
            if no_antes_de is not None and hallazgo.evento.momento < no_antes_de:
                # Ya pasaron mas de `max_antiguedad` minutos desde que empezo:
                # si el servicio estaba arriba, ya la abrio en su momento, y si
                # no, abrirla ahora es lo que llenaba la lista al arrancar.
                log.debug("%s en %s: evento de %s, mas viejo que %s; no se abre",
                          senal.clave, activo, hallazgo.evento.momento, no_antes_de)
                continue
            if self._ya_registrada(conexion, activo, senal.metrica, hallazgo.dimensiones,
                                   hallazgo.evento.momento):
                continue    # ya esta representada: en curso, o ya marcada con este mismo inicio
            self._abrir(conexion, activo, senal.metrica, hallazgo)
            abiertas += 1
        return abiertas

    def puntos_necesarios(self, senal: Senal) -> int:
        """Cuantos puntos de un minuto hacen falta para evaluar esta senal."""
        return self.ventana + self.persistencia + puntos_extra(senal)

    # ---------------------------------------------------------- diagnostico
    def revisar_catalogo(self) -> dict:
        """Para `--senales`: que senales del catalogo existen de verdad en la
        plataforma, activo por activo. Es la forma barata de verificar los
        nombres de `senales.py` sin desplegar nada.

        La revision va en los dos sentidos. `filas` dice que senales del
        catalogo no encuentran su metrica; `sin_vigilar` dice que metricas
        estan llegando y ninguna senal menciona. Un nombre equivocado en el
        catalogo aparece en las dos listas a la vez, y la segunda suele traer
        el nombre correcto al lado.
        """
        informe = []
        sin_vigilar: dict = {}
        vigiladas = {s.metrica for s in self.senales}
        with catalogo.conectar(self.pg_url) as conexion:
            activos = catalogo.listar_activos(conexion, incluir_retirados=False)
        ahora = datetime.now(timezone.utc)
        desde = ahora - self.rango_consulta
        for fila in activos:
            activo = fila["id"]
            disponibles = self._metricas_de(activo) or set()
            sobrantes = sorted(disponibles - vigiladas)
            if sobrantes:
                sin_vigilar[activo] = sobrantes
            for senal in self.senales:
                if senal.metrica not in disponibles:
                    informe.append({"activo": activo, "senal": senal.clave,
                                    "estado": "falta la metrica", "puntos": 0})
                    continue
                try:
                    r = self.repositorio.serie(
                        activo, senal.metrica, desde, ahora,
                        agrupar=senal.agrupar, filtros=dict(senal.filtros) or None,
                    )
                except ErrorConsulta as e:
                    informe.append({"activo": activo, "senal": senal.clave,
                                    "estado": f"dimension no valida: {e}", "puntos": 0})
                    continue
                puntos = sum(len(s.puntos) for s in r.series)
                if not puntos:
                    estado = "sin datos (revisar el valor de la dimension)"
                elif r.intervalo_s != RESOLUCION_ESPERADA_S:
                    estado = f"resolucion de {r.intervalo_s} s, no de 60 s"
                elif puntos < self.puntos_necesarios(senal):
                    estado = f"historial corto: {puntos} de {self.puntos_necesarios(senal)} puntos"
                else:
                    estado = "ok"
                informe.append({"activo": activo, "senal": senal.clave, "estado": estado,
                                "puntos": puntos, "series": len(r.series)})
        return {"filas": informe, "sin_vigilar": sin_vigilar}

    # -------------------------------------------------------------- postgres
    def _ya_registrada(self, conexion, activo: str, metrica: str, dimensiones: dict,
                       inicio: datetime) -> bool:
        """Si esta serie ya tiene esta anomalia, no se abre otra. Dos motivos
        distintos, y hacen falta los dos:

        **Hay una en curso.** Mientras siga abierta y sin marcar, la
        degradacion ya esta representada; seguir abierta o cerrarla es trabajo
        de H-033. Se excluyen los estados terminales para que una anomalia
        marcada por el operador sin `fin` no bloquee su serie para siempre.

        **Ya existe una con este mismo inicio, en cualquier estado.** Sin esto,
        la exclusion anterior se vuelve contra si misma: en cuanto el operador
        marca un falso positivo, el ciclo siguiente vuelve a ver la misma racha
        de puntos, calcula el mismo instante de apertura y la inserta de nuevo,
        deshaciendo la marca. Una anomalia esta identificada por su serie y su
        inicio, asi que repetir ese par es repetir la misma anomalia, y la
        marca del operador tiene que sobrevivir. Una degradacion realmente
        nueva en la misma serie arranca en otro instante y si entra.

        Limitacion conocida: si la desviacion se sostiene tanto que la racha
        original sale de la ventana, el detector puede calcular un inicio
        distinto para la misma causa y abrir otra anomalia pese a la marca.
        Con la ventana de 60 minutos eso tarda al menos una hora.
        """
        with conexion.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM anomalias WHERE activo = %s AND metrica = %s AND dimensiones = %s "
                f"AND ((fin IS NULL AND estado NOT IN {cierre.EN_TERMINALES}) OR inicio = %s) LIMIT 1",
                (activo, metrica, _jsonb(dimensiones), inicio),
            )
            return cur.fetchone() is not None

    def _abrir(self, conexion, activo: str, metrica: str, hallazgo: Hallazgo) -> None:
        evento = hallazgo.evento
        with conexion.cursor() as cur:
            cur.execute(
                "INSERT INTO anomalias "
                "(activo, metrica, dimensiones, inicio, estado, severidad, puntaje, evidencia, version_modelo) "
                "VALUES (%s, %s, %s, %s, 'detectada', %s, %s, %s, %s)",
                (activo, metrica, _jsonb(hallazgo.dimensiones), evento.momento, evento.severidad,
                 evento.puntaje, _jsonb(evento.evidencia), self._version_modelo),
            )
        conexion.commit()
        log.info("anomalia abierta: %s %s severidad=%s puntaje=%.2f en %s",
                 activo, hallazgo.senal.clave, evento.severidad, evento.puntaje, evento.momento)


def _jsonb(data: dict):
    from psycopg.types.json import Jsonb
    return Jsonb(data)
