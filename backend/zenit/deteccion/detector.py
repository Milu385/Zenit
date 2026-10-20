"""El metodo de deteccion y el trabajo de un ciclo (H-032).

El punto de entrada esta en `__main__.py`, como en zenit.descubrimiento:

    python -m zenit.deteccion --cada 60        lo corre el servicio detector
    python -m zenit.deteccion --senales        que senales del catalogo existen

Cada ciclo (mismo patron que zenit.agregacion.Agregador):

1. Lista los activos vigentes en el catalogo (zenit.catalogo.listar_activos).
2. Para cada activo y cada senal del catalogo (zenit.deteccion.senales),
   lee su serie por la capa de repositorio (RepositorioSeries.serie, nunca
   SQL directo a InfluxDB) a resolucion de 1 minuto.
3. Aplica la linea base por activo (mediana + MAD, cortes 1.5 / 3.5 de
   Iglewicz & Hoaglin) del cuaderno `Modelo criticidad`, serie por serie.
4. Abre una anomalia solo si la regla de persistencia se cumple: PERSISTENCIA
   puntos seguidos fuera de rango, nunca un pico aislado (criterio 3 de
   H-032). Si ya hay una anomalia abierta para ese (activo, metrica,
   dimensiones), no abre otra: seguir abierta o cerrarla es trabajo de H-033.

Que se vigila y con que dimensiones esta en `senales.py`, no aqui: este
archivo no sabe nada de metricas concretas. Lo de alla tambien explica por
que los contadores acumulados se convierten a tasa antes de la linea base.

Limitacion heredada del cuaderno, documentada y no escondida (ver
arquitectura-zenit-v0, seccion de estados de una anomalia): el metodo solo
mira desviaciones hacia arriba. Una caida de servicio que se manifiesta como
una *baja* de una senal no la detecta este metodo; lo cubre H-034
(deteccion no supervisada) cuando llegue a la misma salida.

version_modelo (H-036) describe el metodo y sus parametros, por ejemplo
"mad-v1 W=60 z=3.5 m=3": W es la ventana de la linea base, z el corte
critico y m la persistencia exigida.
"""
import logging
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Mapping, Optional, Sequence

from zenit import catalogo
from zenit.deteccion.senales import SENALES, Senal, preparar, puntos_extra
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
# Historia minima para confiar en la linea base: con menos, un activo recien
# llegado (o una metrica con poco historial) no genera falsos positivos por
# falta de datos.
HISTORIA_MINIMA = 5

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


def _severidad(z: float, z_critico: float) -> str:
    """Provisional (H-033 define el criterio final de severidad). Reutiliza el
    mismo paso de 1.5/3.5 del cuaderno, ahora por encima del corte critico."""
    if z >= z_critico + 3.5:
        return "alta"
    if z >= z_critico + 1.5:
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
    historia_minima: int = HISTORIA_MINIMA,
) -> list:
    """Recorre una serie ordenada por tiempo `[(momento, valor), ...]` y
    devuelve un Evento por cada racha de `persistencia` puntos seguidos en
    'critico' que arranca (criterio 3 de H-032).

    Por diseno da como maximo un evento por racha, sin importar cuanto dure
    la racha despues: la prueba de la guia (serie plana con un escalon
    construido a mano) debe dar exactamente una anomalia, ni cero ni diez.
    """
    valores = [v for _, v in puntos]
    n = len(valores)
    estados: list = [None] * n
    puntajes: list = [0.0] * n

    for i in range(n):
        historia = valores[max(0, i - ventana):i]
        if len(historia) < max(2, historia_minima):
            continue    # sin suficiente historial propio: no se clasifica
        baseline = calcular_baseline(historia)
        z = puntaje_z(valores[i], baseline)
        puntajes[i] = z
        estados[i] = clasificar(z, z_normal, z_critico)

    eventos = []
    racha = 0
    for i, estado in enumerate(estados):
        racha = racha + 1 if estado == "critico" else 0
        if racha != persistencia:
            continue    # solo se abre al llegar exactamente a la persistencia exigida
        historia = valores[max(0, i - ventana):i]
        baseline = calcular_baseline(historia)
        momento, valor = puntos[i]
        eventos.append(Evento(
            indice=i,
            momento=momento,
            puntaje=puntajes[i],
            severidad=_severidad(puntajes[i], z_critico),
            evidencia={
                "ventana": ventana,
                "persistencia": persistencia,
                "valor_observado": valor,
                "valor_esperado": baseline.mediana,
                "metodo": "mad-v1",
            },
        ))
    return eventos


def version_modelo(ventana: int = VENTANA_DEFECTO, z_critico: float = Z_CRITICO,
                   persistencia: int = PERSISTENCIA_DEFECTO) -> str:
    return f"mad-v1 W={ventana} z={z_critico} m={persistencia}"


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
        if len(puntos) < persistencia + max(2, HISTORIA_MINIMA):
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
    ):
        self.repositorio = repositorio
        self.pg_url = pg_url
        self.senales = tuple(senales)
        self.ventana = ventana
        self.persistencia = persistencia
        self.z_normal = z_normal
        self.z_critico = z_critico
        self.rango_consulta = rango_consulta
        self._version_modelo = version_modelo(ventana, z_critico, persistencia)

    # -------------------------------------------------------------- ciclo
    def ciclo(self, ahora: Optional[datetime] = None) -> dict:
        ahora = ahora or datetime.now(timezone.utc)
        desde = ahora - self.rango_consulta
        activos = senales_leidas = anomalias_abiertas = 0
        with catalogo.conectar(self.pg_url) as conexion:
            for fila in catalogo.listar_activos(conexion, incluir_retirados=False):
                activo = fila["id"]
                activos += 1
                disponibles = self._metricas_de(activo)
                for senal in self.senales:
                    if disponibles is not None and senal.metrica not in disponibles:
                        continue    # esa metrica no llega de este activo
                    senales_leidas += 1
                    anomalias_abiertas += self._revisar(conexion, activo, senal, desde, ahora)
        return {
            "activos": activos,
            "senales": senales_leidas,
            "anomalias_abiertas": anomalias_abiertas,
        }

    def _metricas_de(self, activo: str) -> Optional[set]:
        """Las metricas que reporta este activo, para no consultar senales que
        no existen. None si no se pudo averiguar: en ese caso se intentan
        todas y cada una falla por su cuenta, sin tumbar el ciclo."""
        try:
            return set(self.repositorio.metricas_de(activo))
        except Exception as e:  # noqa: BLE001 - el ciclo sigue con las demas
            log.warning("no se pudieron listar las metricas de %s: %s", activo, e)
            return None

    def _revisar(self, conexion, activo: str, senal: Senal, desde: datetime, hasta: datetime) -> int:
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
            if self._ya_abierta(conexion, activo, senal.metrica, hallazgo.dimensiones):
                continue    # ya hay una anomalia abierta: seguir o cerrar es H-033
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
    def _ya_abierta(self, conexion, activo: str, metrica: str, dimensiones: dict) -> bool:
        with conexion.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM anomalias WHERE activo = %s AND metrica = %s "
                "AND dimensiones = %s AND fin IS NULL LIMIT 1",
                (activo, metrica, _jsonb(dimensiones)),
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
