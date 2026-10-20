"""El detector como servicio (H-032).

    python -m zenit.deteccion --cada 60        lo corre el servicio detector

Cada ciclo (mismo patron que zenit.agregacion.Agregador):

1. Lista los activos vigentes en el catalogo (zenit.catalogo.listar_activos).
2. Para cada activo, pregunta a la capa de repositorio
   (RepositorioSeries.serie, nunca SQL directo a InfluxDB) que metricas tiene
   y lee su serie reciente a resolucion de 1 minuto.
3. Aplica la linea base por activo (mediana + MAD, cortes 1.5 / 3.5 de
   Iglewicz & Hoaglin) del cuaderno `Modelo criticidad`, metrica por metrica.
4. Abre una anomalia solo si la regla de persistencia se cumple: PERSISTENCIA
   puntos seguidos fuera de rango, nunca un pico aislado (criterio 3 de
   H-032). Si ya hay una anomalia abierta para ese (activo, metrica), no abre
   otra: seguir abierta o cerrarla es trabajo de H-033.

Limitacion heredada del cuaderno, documentada y no escondida (ver
arquitectura-zenit-v0, seccion de estados de una anomalia): el metodo solo
mira desviaciones hacia arriba. Una caida de servicio que se manifiesta como
una *baja* de una señal no la detecta este metodo; lo cubre H-034
(deteccion no supervisada) cuando llegue a la misma salida.

version_modelo (H-036) describe el metodo y sus parametros, por ejemplo
"mad-v1 W=60 z=3.5 m=3": W es la ventana de la linea base, z el corte
critico y m la persistencia exigida.
"""
import argparse
import logging
import os
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Mapping, Optional, Sequence

from zenit import catalogo
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
# Rango de consulta: mas de 24 h (UMBRAL_CRUDO en RepositorioInflux) para que
# RepositorioSeries.serie responda al nivel de 1 minuto (H-019), nunca al
# crudo de 10 s. La deteccion corre sobre el resumen por minuto, no sobre el
# dato crudo (arquitectura, seccion de la detección).
RANGO_CONSULTA = timedelta(hours=25)


# --------------------------------------------------------------------- metodo
@dataclass(frozen=True)
class Baseline:
    """Mediana y escala (dispersion robusta) de una ventana de una serie."""

    mediana: float
    escala: Optional[float]      # None si la ventana es demasiado plana


def calcular_baseline(valores: Sequence[float]) -> Baseline:
    """Linea base de una ventana: mediana y MAD reescalado, con respaldo en
    la desviacion estandar cuando el MAD es cero (señal casi constante).

    Es la misma formula de `calcular_baseline` en el cuaderno `Modelo
    criticidad`, adaptada a una sola metrica de un solo activo a la vez (aqui
    la deteccion ya esta separada por activo y metrica; agrupar por activo,
    como hace el cuaderno, es responsabilidad de quien llama).
    """
    mediana = statistics.median(valores)
    mad = statistics.median(abs(v - mediana) for v in valores) * 1.4826
    desviacion = statistics.pstdev(valores) if len(valores) > 1 else 0.0
    escala = mad if mad > 0 else desviacion
    return Baseline(mediana=mediana, escala=escala if escala and escala > 0 else None)


def puntaje_z(valor: float, baseline: Baseline) -> float:
    """z = (valor - mediana) / escala. Una ventana sin escala (metrica
    constante en su historial) no aporta señal de desviacion: z = 0, no un
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
    """Provisional (H-033 define el criterio final de severidad; el mensaje
    de Maria Lucia dice literalmente "tus cortes sirven"). Reutiliza el mismo
    paso de 1.5/3.5 del cuaderno, ahora por encima del corte critico."""
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
    `momento` puede ser un datetime o un entero (milisegundos desde la
    epoca, como devuelve RepositorioSeries); aqui no se interpreta, solo se
    guarda en el evento.
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


# -------------------------------------------------------------------- servicio
def _a_momento(valor) -> datetime:
    """Los puntos de Serie vienen como [ms_epoca, valor] (contrato.Serie).
    Si ya es datetime (por ejemplo, en una prueba), se deja igual."""
    if isinstance(valor, datetime):
        return valor if valor.tzinfo else valor.replace(tzinfo=timezone.utc)
    return datetime.fromtimestamp(valor / 1000, timezone.utc)


class Detector:
    def __init__(
        self,
        repositorio: RepositorioSeries,
        pg_url: str,
        *,
        ventana: int = VENTANA_DEFECTO,
        persistencia: int = PERSISTENCIA_DEFECTO,
        z_normal: float = Z_NORMAL,
        z_critico: float = Z_CRITICO,
        rango_consulta: timedelta = RANGO_CONSULTA,
        metricas: Optional[Sequence[str]] = None,
    ):
        self.repositorio = repositorio
        self.pg_url = pg_url
        self.ventana = ventana
        self.persistencia = persistencia
        self.z_normal = z_normal
        self.z_critico = z_critico
        self.rango_consulta = rango_consulta
        # None: se detecta sobre todas las metricas que el repositorio
        # reporte para cada activo (igual de generico que el cuaderno, que
        # "sirve para cualquier usuario de Zenit... lo que cambia es el
        # historial, no el codigo"). Fijar una lista es una optimizacion, no
        # un requisito del metodo.
        self.metricas = list(metricas) if metricas is not None else None
        self._version_modelo = version_modelo(ventana, z_critico, persistencia)

    # -------------------------------------------------------------- ciclo
    def ciclo(self, ahora: Optional[datetime] = None) -> dict:
        ahora = ahora or datetime.now(timezone.utc)
        desde = ahora - self.rango_consulta
        activos_revisados = metricas_revisadas = anomalias_abiertas = 0
        with catalogo.conectar(self.pg_url) as conexion:
            for fila in catalogo.listar_activos(conexion, incluir_retirados=False):
                activo = fila["id"]
                activos_revisados += 1
                try:
                    metricas = self.metricas if self.metricas is not None else self.repositorio.metricas_de(activo)
                except ErrorConsulta as e:
                    log.warning("no se pudieron listar las metricas de %s: %s", activo, e)
                    continue
                for metrica in metricas:
                    metricas_revisadas += 1
                    anomalias_abiertas += self._revisar(conexion, activo, metrica, desde, ahora)
        return {
            "activos": activos_revisados,
            "metricas": metricas_revisadas,
            "anomalias_abiertas": anomalias_abiertas,
        }

    def _revisar(self, conexion, activo: str, metrica: str, desde: datetime, hasta: datetime) -> int:
        try:
            respuesta = self.repositorio.serie(activo, metrica, desde, hasta)
        except ErrorConsulta as e:
            log.warning("no se pudo leer %s de %s: %s", metrica, activo, e)
            return 0
        abiertas = 0
        for serie in respuesta.series:
            necesarios = self.ventana + self.persistencia
            puntos = [(_a_momento(t), v) for t, v in serie.puntos[-necesarios:]]
            if len(puntos) < self.persistencia + max(2, HISTORIA_MINIMA):
                continue
            eventos = detectar_eventos(
                puntos, ventana=self.ventana, persistencia=self.persistencia,
                z_normal=self.z_normal, z_critico=self.z_critico,
            )
            if not eventos:
                continue
            dimensiones = dict(serie.etiquetas)
            if self._ya_abierta(conexion, activo, metrica, dimensiones):
                continue    # ya hay una anomalia abierta: seguir o cerrar es H-033
            self._abrir(conexion, activo, metrica, dimensiones, eventos[-1])
            abiertas += 1
        return abiertas

    # -------------------------------------------------------------- postgres
    def _ya_abierta(self, conexion, activo: str, metrica: str, dimensiones: dict) -> bool:
        with conexion.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM anomalias WHERE activo = %s AND metrica = %s "
                "AND dimensiones = %s AND fin IS NULL LIMIT 1",
                (activo, metrica, _jsonb(dimensiones)),
            )
            return cur.fetchone() is not None

    def _abrir(self, conexion, activo: str, metrica: str, dimensiones: dict, evento: Evento) -> None:
        with conexion.cursor() as cur:
            cur.execute(
                "INSERT INTO anomalias "
                "(activo, metrica, dimensiones, inicio, estado, severidad, puntaje, evidencia, version_modelo) "
                "VALUES (%s, %s, %s, %s, 'detectada', %s, %s, %s, %s)",
                (activo, metrica, _jsonb(dimensiones), evento.momento, evento.severidad,
                 evento.puntaje, _jsonb(evento.evidencia), self._version_modelo),
            )
        conexion.commit()
        log.info("anomalia abierta: %s/%s severidad=%s puntaje=%.2f en %s",
                 activo, metrica, evento.severidad, evento.puntaje, evento.momento)


def _jsonb(data: dict):
    from psycopg.types.json import Jsonb
    return Jsonb(data)


def principal(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    p = argparse.ArgumentParser(prog="python -m zenit.deteccion")
    p.add_argument("--cada", type=int, default=0, help="segundos entre ciclos; 0 = uno solo")
    args = p.parse_args(argv)

    from zenit.influx import ClienteInflux
    from zenit.repositorio.influx import RepositorioInflux

    url = os.environ.get("PG_URL")
    if not url or not os.environ.get("INFLUX_TOKEN"):
        print("faltan PG_URL o INFLUX_TOKEN", file=sys.stderr)
        return 2
    catalogo.esperar_pg(url)
    cliente = ClienteInflux(os.environ.get("INFLUX_URL", "http://influxdb:8181"), os.environ["INFLUX_TOKEN"])
    repositorio = RepositorioInflux(cliente, os.environ.get("INFLUX_BD", "zenit_raw"))
    detector = Detector(repositorio, url)

    while True:
        t0 = time.monotonic()
        try:
            r = detector.ciclo()
            print(f"{datetime.now(timezone.utc):%H:%M:%S} {r['activos']} activos, {r['metricas']} metricas, "
                  f"{r['anomalias_abiertas']} anomalias abiertas", flush=True)
        except Exception:  # noqa: BLE001 - un ciclo fallido se repite en el siguiente
            log.exception("ciclo fallido")
        if not args.cada:
            return 0
        time.sleep(max(1.0, args.cada - (time.monotonic() - t0)))


if __name__ == "__main__":
    sys.exit(principal())
