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
) -> Optional[datetime]:
    """El instante en que la anomalia deja de estar abierta, o None.

    `puntos` son `[(momento, valor), ...]` de la serie, ya preparados segun la
    clase de la senal (las acumuladas, convertidas a tasa). La linea base es
    la congelada al abrir.

    Devuelve el momento del ultimo punto normal cuando los ultimos
    `normalidad` puntos estan todos por debajo del corte critico. Con menos
    puntos que los exigidos devuelve None: la ausencia de datos no es
    normalidad.

    Los puntos tienen que ser seguidos, ademas de suficientes. Quince puntos
    con un hueco de media hora en medio no son quince minutos de normalidad:
    son quince minutos sueltos a los lados de un rato sin datos, y de ese
    rato no se sabe nada. `paso_s` es la resolucion esperada (60 s) y se
    admite un intervalo de holgura por si un minuto llega tarde.
    """
    if len(puntos) < normalidad:
        return None
    ultimos = list(puntos[-normalidad:])
    for _, valor in ultimos:
        if (valor - mediana) / escala >= z_critico:
            return None
    abarca = (ultimos[-1][0] - ultimos[0][0]).total_seconds()
    if abarca > (normalidad - 1 + holgura_puntos) * paso_s:
        return None
    return ultimos[-1][0]


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
