"""Cierre automatico de anomalias (H-033).

Una anomalia pasa a `cerrada` cuando su serie vuelve a lo normal durante un
tiempo definido. Tres decisiones, con su motivo:

**1. Se mide contra la linea base congelada al abrir, no contra una nueva.**
Es la decision que sostiene todo lo demas. La linea base del detector es una
ventana movil de 60 minutos, asi que se adapta a lo que ve: medido sobre un
escalon sostenido (disco al 80 % que no se resuelve), la serie se declara
`bueno` a los 30 minutos del inicio del fallo, porque a esa altura la mayoria
de la ventana ya son puntos del fallo y la mediana se mudo al nivel nuevo.

| Minutos tras el fallo | z con ventana movil | Estado |
|---|---|---|
| 0 | 839 | critico |
| 10 | 734 | critico |
| 20 | 511 | critico |
| 30 | 0.68 | **bueno** |
| 60 | 0.40 | bueno |

Con una linea base recalculada, "la serie volvio a lo normal" se cumpliria
por construccion a la media hora pasara lo que pasara con el fallo: se
cerrarian anomalias que nadie resolvio y la evaluacion de H-029 las contaria
como detecciones cortas y exitosas de fallos que seguian activos. Por eso el
cierre usa la mediana y la escala que quedaron guardadas en
`evidencia.linea_base` cuando la anomalia se abrio.

**Consecuencia asumida:** si un activo cambia de nivel para siempre (un
servicio nuevo que sube el trafico base), su anomalia no cierra sola, porque
contra la linea base vieja la desviacion no cesa nunca. Es el precio de no
cerrar en falso, y la salida es la marca del operador (`falso_positivo`) o el
reentrenamiento periodico que pide RF-DET-05. Conviene mirar las anomalias
muy viejas antes de la entrega.

**2. "Vuelve a lo normal" es dejar de estar en critico, no llegar a bueno.**
La anomalia se abrio al cruzar el corte critico (z >= 3.5), asi que cierra
cuando deja de cruzarlo. Exigir el retorno a la banda `bueno` (z < 1.5) seria
mas estricto, pero con una linea base muy estrecha una serie puede quedarse
estable en z = 2 y la anomalia no cerraria nunca. El tiempo sostenido es lo
que protege del parpadeo, no un segundo umbral.

**3. El tiempo exigido: 15 minutos.** Sale del calendario de la campana
(`laboratorio/servicios/inyector/inyector/campana.py`), que garantiza
`normal_min = 45` minutos de normalidad entre el fin de una inyeccion y el
inicio de la siguiente, con inyecciones de 3 a 20 minutos:

- tiene que ser **bastante menor que 45 minutos**, o dos inyecciones
  seguidas se fundirian en una sola anomalia y la evaluacion por evento de
  H-029 contaria la segunda como no detectada. 15 deja 30 de margen;
- tiene que ser **bastante mayor que la persistencia** (3 puntos), para que
  una degradacion intermitente no cierre y vuelva a abrir en cada rebote:
  con 15 minutos, la serie tiene que estar quieta un cuarto de hora seguido
  para que la anomalia se cierre. Ese es el mecanismo que cumple el tercer
  criterio de H-033, y no hace falta nada mas.

**4. El `fin` es el primer punto normal, no el ultimo.** La anomalia termino
cuando la serie volvio a lo normal; los 15 minutos siguientes son la
confirmacion, no parte de la anomalia. Con el ultimo punto de la racha, cada
anomalia parecia durar 15 minutos mas de lo real (la 29 de Azure quedo con
fin 13:46, cuando el disco volvio a lo normal a las 13:31), y eso se veia en
la duracion de la interfaz y en la de H-029. Si la racha normal es mas larga
que los 15 puntos (el servicio estuvo parado un rato, por ejemplo), el `fin`
es el primero de toda la racha seguida, sin pasar del inicio de la anomalia.

**5. Al cerrar, la escala nunca baja del 5 % de la mediana.** Visto en
Azure el 26 de octubre: una anomalia de `system_paging_usage` (bytes de swap)
se abrio con una escala congelada de pocos KB. Cuando la inyeccion termino,
el swap quedo 92 KB por encima de antes (0,3 %), y contra esa escala eso
seguia siendo "critico": la anomalia no se cerraba nunca y, mientras
siguiera abierta, la serie no podia abrir otra (deduplicacion del
detector). Lo mismo dejo abiertas durante dias la 110 de memoria y la 42 de
sistema de archivos con mad-v1. El piso de mad-v2 (`senales.py`) solo cubre
las fracciones y se aplica al abrir; este se aplica al cerrar y a todas las
senales: la serie cuenta como normal si queda a menos de 3,5 veces el 5 %
de la mediana de antes. No cambia cuando se abre una anomalia ni su
severidad, solo cuando se da por terminada, y no cierra un fallo
sostenido: un disco que sigue lleno, o una memoria que sigue alta, quedan
muy por encima de ese margen. Por eso no cambia `version_modelo`.

**La falta de datos no es normalidad.** Si una fuente esta degradada o no
llegaron suficientes puntos, no se cierra: no hay con que afirmar que la
desviacion ceso. Lo mismo del lado de abrir, en el detector.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Sequence

log = logging.getLogger("zenit.deteccion")

# Minutos seguidos de normalidad que se exigen para cerrar. A resolucion de
# 1 minuto, son tambien puntos.
NORMALIDAD_PARA_CERRAR = 15

# Piso de la escala al cerrar, como fraccion de la mediana congelada
# (decision 5 del encabezado).
PISO_RELATIVO_CIERRE = 0.05

# Estados que no se tocan automaticamente: ya terminaron su ciclo. El paso a
# `falso_positivo` es del operador (arquitectura, estados de una anomalia) y
# alimenta la evaluacion; cerrarlo por encima borraria esa marca.
ESTADOS_TERMINALES = ("cerrada", "falso_positivo")
# Son constantes del codigo, no datos de nadie: se arman aqui una vez en vez
# de repetir la lista en cada consulta.
EN_TERMINALES = "(" + ", ".join(f"'{e}'" for e in ESTADOS_TERMINALES) + ")"

_COLUMNAS = ("id", "activo", "metrica", "dimensiones", "inicio", "estado", "evidencia")


@dataclass(frozen=True)
class Abierta:
    """Una anomalia sin cerrar, con lo que hace falta para evaluar su cierre.

    Todo sale de su propia fila: no se consulta el catalogo de senales. Una
    anomalia que se abrio con una version anterior del catalogo cierra igual.
    """

    id: int
    activo: str
    metrica: str
    dimensiones: dict
    inicio: datetime
    estado: str
    clase: str                      # medida | acumulada, de evidencia
    mediana: Optional[float]        # linea base congelada al abrir
    escala: Optional[float]

    @property
    def evaluable(self) -> bool:
        """Sin la escala de su linea base no se puede recalcular el puntaje
        contra lo que era normal cuando se abrio."""
        return self.escala is not None and self.escala > 0 and self.mediana is not None


def abiertas(conexion) -> list:
    """Las anomalias sin cerrar, en orden de apertura."""
    with conexion.cursor() as cur:
        cur.execute(
            f"SELECT {', '.join(_COLUMNAS)} FROM anomalias "
            f"WHERE fin IS NULL AND estado NOT IN {EN_TERMINALES} ORDER BY inicio",
        )
        filas = [dict(zip(_COLUMNAS, f)) for f in cur.fetchall()]
    salida = []
    for f in filas:
        evidencia = f.get("evidencia") or {}
        linea = evidencia.get("linea_base") or {}
        salida.append(Abierta(
            id=f["id"], activo=f["activo"], metrica=f["metrica"],
            dimensiones=f.get("dimensiones") or {}, inicio=f["inicio"], estado=f["estado"],
            clase=evidencia.get("clase", "medida"),
            mediana=linea.get("mediana"), escala=linea.get("escala"),
        ))
    return salida


def momento_de_cierre(
    puntos: Sequence[tuple],
    mediana: float,
    escala: float,
    *,
    normalidad: int = NORMALIDAD_PARA_CERRAR,
    z_critico: float = 3.5,
    paso_s: int = 60,
    holgura_puntos: int = 1,
    despues_de: Optional[datetime] = None,
    piso_relativo: float = PISO_RELATIVO_CIERRE,
) -> Optional[datetime]:
    """El instante en que la anomalia deja de estar abierta, o None.

    `puntos` son `[(momento, valor), ...]` de la serie, ya preparados segun la
    clase de la senal (las acumuladas, convertidas a tasa). La linea base es
    la congelada al abrir.

    Cuando los ultimos `normalidad` puntos estan todos por debajo del corte
    critico, devuelve el momento del **primer** punto de la racha normal
    seguida que termina en el ultimo: el instante en que la serie volvio a lo
    normal (decision 4 del encabezado). Con menos puntos que los exigidos
    devuelve None: la ausencia de datos no es normalidad.

    La escala nunca baja de `piso_relativo` veces la mediana (decision 5).

    `despues_de` es el inicio de la anomalia: solo cuentan los puntos
    posteriores, porque los de antes son la normalidad de antes del fallo.

    Los puntos tienen que ser seguidos, ademas de suficientes. Quince puntos
    con un hueco de media hora en medio no son quince minutos de normalidad:
    son quince minutos sueltos a los lados de un rato sin datos, y de ese
    rato no se sabe nada. `paso_s` es la resolucion esperada (60 s) y se
    admite un intervalo de holgura por si un minuto llega tarde.
    """
    escala = max(escala, piso_relativo * abs(mediana))
    if despues_de is not None:
        puntos = [p for p in puntos if p[0] > despues_de]
    if len(puntos) < normalidad:
        return None

    def normal(valor):
        return (valor - mediana) / escala < z_critico

    ultimos = list(puntos[-normalidad:])
    if not all(normal(valor) for _, valor in ultimos):
        return None
    abarca = (ultimos[-1][0] - ultimos[0][0]).total_seconds()
    if abarca > (normalidad - 1 + holgura_puntos) * paso_s:
        return None

    # Hacia atras, mientras siga normal y sin huecos: el primer punto de la
    # racha es cuando la serie volvio a lo normal.
    i = len(puntos) - normalidad
    while i > 0:
        t_ant, v_ant = puntos[i - 1]
        if not normal(v_ant) or (puntos[i][0] - t_ant).total_seconds() > holgura_puntos * paso_s + paso_s:
            break
        i -= 1
    return puntos[i][0]


def cerrar(conexion, anomalia_id: int, fin: datetime) -> bool:
    """Marca la anomalia como cerrada. Devuelve False si alguien la cerro o la
    marco mientras tanto: la condicion se repite en el UPDATE para no pisar
    una decision del operador."""
    if fin.tzinfo is None:
        fin = fin.replace(tzinfo=timezone.utc)
    with conexion.cursor() as cur:
        cur.execute(
            "UPDATE anomalias SET fin = %s, estado = 'cerrada' "
            f"WHERE id = %s AND fin IS NULL AND estado NOT IN {EN_TERMINALES}",
            (fin, anomalia_id),
        )
        cambio = cur.rowcount
    conexion.commit()
    return bool(cambio)
