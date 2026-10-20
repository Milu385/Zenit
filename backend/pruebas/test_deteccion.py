"""H-032: metodo de deteccion (linea base, puntaje z, regla de persistencia).

Pruebas puras, sin base de datos ni InfluxDB: el metodo estadistico es
independiente de donde vengan los puntos. La integracion con el
repositorio y con la tabla `anomalias` (Detector.ciclo) se prueba por
separado cuando el servicio tenga su doble de Influx, igual que
test_agregacion.py.
"""
import random
from datetime import datetime, timedelta, timezone

from zenit.deteccion.detector import (
    Z_CRITICO,
    Z_NORMAL,
    Baseline,
    calcular_baseline,
    clasificar,
    detectar_eventos,
    puntaje_z,
    version_modelo,
)

INICIO = datetime(2026, 10, 19, tzinfo=timezone.utc)


def _serie(valores, paso=timedelta(minutes=1)):
    return [(INICIO + i * paso, v) for i, v in enumerate(valores)]


# ------------------------------------------------------------------- metodo
def test_calcular_baseline_mediana_y_mad():
    baseline = calcular_baseline([10, 10, 10, 10, 50])   # un atipico no mueve la mediana
    assert baseline.mediana == 10
    assert baseline.escala and baseline.escala > 0


def test_calcular_baseline_usa_desviacion_si_mad_es_cero():
    # la mitad de los valores son iguales a la mediana: el MAD da 0 y el
    # respaldo es la desviacion estandar, nunca una escala de 0 (division por
    # cero) ni None con señal real de dispersion.
    baseline = calcular_baseline([10, 10, 10, 10, 12, 8])
    assert baseline.escala and baseline.escala > 0


def test_puntaje_z_sin_escala_no_dispara():
    # metrica constante en su historial: sin escala, z = 0 (bueno), no error.
    baseline = Baseline(mediana=10.0, escala=None)
    assert puntaje_z(999.0, baseline) == 0.0


def test_clasificar_cortes():
    assert clasificar(0.0) == "bueno"
    assert clasificar(Z_NORMAL) == "normal"
    assert clasificar(Z_CRITICO) == "critico"


# -------------------------------------------------------------- persistencia
def test_escalon_sostenido_da_una_sola_anomalia():
    """La prueba de la guia de deteccion: una serie plana con un escalon
    construido a mano debe dar UNA anomalia, ni cero ni diez."""
    random.seed(0)
    plano = [10 + random.uniform(-0.05, 0.05) for _ in range(90)]
    escalon = [40 + random.uniform(-0.05, 0.05) for _ in range(30)]   # muy por encima de la linea base
    vuelta = [10 + random.uniform(-0.05, 0.05) for _ in range(20)]
    puntos = _serie(plano + escalon + vuelta)

    eventos = detectar_eventos(puntos, ventana=60, persistencia=3)

    assert len(eventos) == 1
    evento = eventos[0]
    assert evento.severidad in ("baja", "media", "alta")
    assert evento.puntaje >= Z_CRITICO
    assert evento.evidencia["metodo"] == "mad-v1"
    # el evento cae dentro del escalon, no antes de que empiece
    assert puntos[evento.indice][0] >= INICIO + timedelta(minutes=len(plano))


def test_pico_aislado_no_abre_anomalia():
    """Un solo punto fuera de rango no es una anomalia (criterio 3 de H-032):
    hace falta persistencia, no un pico aislado."""
    random.seed(1)
    plano = [10 + random.uniform(-0.05, 0.05) for _ in range(90)]
    plano[70] = 200  # un pico de un solo punto
    puntos = _serie(plano)

    eventos = detectar_eventos(puntos, ventana=60, persistencia=3)

    assert eventos == []


def test_serie_sin_desviacion_no_abre_nada():
    puntos = _serie([10 + random.uniform(-0.05, 0.05) for _ in range(90)])
    assert detectar_eventos(puntos, ventana=60, persistencia=3) == []


def test_degradacion_intermitente_abre_como_maximo_una_vez_por_racha():
    """Una racha que llega a la persistencia exigida abre una sola vez,
    aunque la racha siga despues: no una por cada punto adicional."""
    random.seed(2)
    plano = [10 + random.uniform(-0.05, 0.05) for _ in range(80)]
    racha_larga = [50 + random.uniform(-0.05, 0.05) for _ in range(50)]
    puntos = _serie(plano + racha_larga)

    eventos = detectar_eventos(puntos, ventana=60, persistencia=3)

    assert len(eventos) == 1


def test_version_modelo_describe_los_parametros():
    assert version_modelo(60, 3.5, 3) == "mad-v1 W=60 z=3.5 m=3"
