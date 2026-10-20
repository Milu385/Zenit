"""H-032: metodo de deteccion y catalogo de senales.

Pruebas puras, sin base de datos ni InfluxDB: el metodo estadistico y la
preparacion de cada senal son independientes de donde vengan los puntos.
`evaluar_senal` recibe una Respuesta del repositorio (la misma clase del
contrato) y devuelve lo que habria que escribir, asi que todo el camino
menos el INSERT queda cubierto aqui.

Lo que falta: `Detector.ciclo` contra PostgreSQL y un doble de Influx, como
hace test_agregacion.py.
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
    evaluar_senal,
    puntaje_z,
    version_modelo,
)
from zenit.deteccion.senales import ACUMULADA, MEDIDA, SENALES, Senal, a_tasa, preparar
from zenit.esquema import es_identificador
from zenit.repositorio.contrato import Resolucion, Respuesta, Serie

INICIO = datetime(2026, 10, 19, tzinfo=timezone.utc)
MINUTO = timedelta(minutes=1)


def _serie(valores, paso=MINUTO):
    return [(INICIO + i * paso, v) for i, v in enumerate(valores)]


def _plano(n, nivel=10.0, ruido=0.05, semilla=0):
    random.seed(semilla)
    return [nivel + random.uniform(-ruido, ruido) for _ in range(n)]


def _respuesta(puntos_por_serie, *, intervalo_s=60, etiquetas=None, series_omitidas=0):
    """Una Respuesta como la que devuelve RepositorioSeries.serie, con los
    puntos en el formato real: [ms desde la epoca, valor]."""
    series = []
    for i, puntos in enumerate(puntos_por_serie):
        series.append(Serie(
            etiquetas=(etiquetas or [{}] * len(puntos_por_serie))[i],
            puntos=[[int(t.timestamp() * 1000), v] for t, v in puntos],
        ))
    return Respuesta(
        activo="activo-azure-nodo-01", metrica="prueba", resolucion=Resolucion.MINUTO,
        desde=INICIO, hasta=INICIO + 1500 * MINUTO, agrupar=None, series=series,
        intervalo_s=intervalo_s, series_omitidas=series_omitidas,
    )


# ------------------------------------------------------------------- metodo
def test_calcular_baseline_mediana_y_mad():
    baseline = calcular_baseline([10, 10, 10, 10, 50])   # un atipico no mueve la mediana
    assert baseline.mediana == 10
    assert baseline.escala and baseline.escala > 0


def test_calcular_baseline_usa_desviacion_si_mad_es_cero():
    # la mitad de los valores son iguales a la mediana: el MAD da 0 y el
    # respaldo es la desviacion estandar, nunca una escala de 0 (division por
    # cero) ni None habiendo dispersion real.
    baseline = calcular_baseline([10, 10, 10, 10, 12, 8])
    assert baseline.escala and baseline.escala > 0


def test_puntaje_z_sin_escala_no_dispara():
    # metrica constante en su historial: sin escala, z = 0 (bueno), no error.
    assert puntaje_z(999.0, Baseline(mediana=10.0, escala=None)) == 0.0


def test_clasificar_cortes():
    assert clasificar(0.0) == "bueno"
    assert clasificar(Z_NORMAL) == "normal"
    assert clasificar(Z_CRITICO) == "critico"


def test_version_modelo_describe_los_parametros():
    assert version_modelo(60, 3.5, 3) == "mad-v1 W=60 z=3.5 m=3"


# -------------------------------------------------------------- persistencia
def test_escalon_sostenido_da_una_sola_anomalia():
    """La prueba de la guia de deteccion: una serie plana con un escalon
    construido a mano debe dar UNA anomalia, ni cero ni diez."""
    puntos = _serie(_plano(90) + _plano(30, nivel=40.0, semilla=1) + _plano(20, semilla=2))

    eventos = detectar_eventos(puntos, ventana=60, persistencia=3)

    assert len(eventos) == 1
    evento = eventos[0]
    assert evento.severidad in ("baja", "media", "alta")
    assert evento.puntaje >= Z_CRITICO
    assert evento.evidencia["metodo"] == "mad-v1"
    assert puntos[evento.indice][0] >= INICIO + 90 * MINUTO     # cae dentro del escalon


def test_pico_aislado_no_abre_anomalia():
    """Un solo punto fuera de rango no es una anomalia (criterio 3 de H-032):
    hace falta persistencia, no un pico aislado."""
    valores = _plano(90)
    valores[70] = 200
    assert detectar_eventos(_serie(valores), ventana=60, persistencia=3) == []


def test_serie_sin_desviacion_no_abre_nada():
    assert detectar_eventos(_serie(_plano(90)), ventana=60, persistencia=3) == []


def test_racha_larga_abre_una_sola_vez():
    puntos = _serie(_plano(80) + _plano(50, nivel=50.0, semilla=3))
    assert len(detectar_eventos(puntos, ventana=60, persistencia=3)) == 1


# ------------------------------------------------------- contadores acumulados
def _contador(rates):
    """Contador acumulado a partir de una lista de incrementos por minuto."""
    random.seed(4)
    acumulado, total = [], 0.0
    for r in rates:
        total += r + random.uniform(-2, 2)
        acumulado.append(total)
    return _serie(acumulado)


# 90 min a 100 u/min, 30 min a 400 (saturacion), vuelta a 100.
CONTADOR = _contador([100] * 90 + [400] * 30 + [100] * 40)
SENAL_MEDIDA = Senal(metrica="system_disk_io", clase=MEDIDA)
SENAL_ACUMULADA = Senal(metrica="system_disk_io", clase=ACUMULADA)


def test_contador_acumulado_crudo_no_detecta_la_saturacion():
    """Por que las senales acumuladas no se evaluan tal cual: sobre una rampa
    el MAD crece con la rampa, el z se queda acotado y la saturacion de media
    hora no llega al corte critico. Queda un punto aislado que la regla de
    persistencia descarta: cero anomalias."""
    hallazgos = evaluar_senal(SENAL_MEDIDA, _respuesta([CONTADOR]), ventana=60, persistencia=3)
    assert hallazgos == []


def test_contador_acumulado_como_tasa_si_detecta_la_saturacion():
    """La misma serie, declarada ACUMULADA: se convierte a tasa por segundo
    antes de la linea base y entonces el escalon de 4x se ve."""
    hallazgos = evaluar_senal(SENAL_ACUMULADA, _respuesta([CONTADOR]), ventana=60, persistencia=3)

    assert len(hallazgos) == 1
    assert hallazgos[0].evento.puntaje >= Z_CRITICO
    assert hallazgos[0].evento.evidencia["clase"] == ACUMULADA


def test_a_tasa_descarta_el_reinicio_del_contador():
    """Un salto negativo es un contador que arranco de nuevo: ese punto no
    dice cuanto paso, asi que no entra como tasa negativa."""
    puntos = _serie([0.0, 60.0, 120.0, 5.0, 65.0])    # reinicio en el cuarto punto
    tasas = a_tasa(puntos)
    assert all(v >= 0 for _, v in tasas)
    assert len(tasas) == 3                             # 4 intervalos menos el del reinicio
    assert tasas[0][1] == 1.0                          # 60 unidades en 60 s


def test_a_tasa_pierde_el_primer_punto():
    assert len(a_tasa(_serie([1.0, 2.0, 3.0]))) == 2


def test_preparar_respeta_la_clase():
    puntos = _serie([1.0, 2.0, 3.0])
    assert preparar(SENAL_MEDIDA, puntos) == puntos
    assert len(preparar(SENAL_ACUMULADA, puntos)) == 2


# ----------------------------------------------------------- evaluar_senal
def test_no_evalua_si_la_resolucion_no_es_de_un_minuto():
    """La ventana esta expresada en minutos (W=60 son 60 minutos): puntos de
    10 s cambiarian en silencio lo que significa el modelo."""
    puntos = _serie(_plano(90) + _plano(30, nivel=40.0, semilla=1))
    assert evaluar_senal(SENAL_MEDIDA, _respuesta([puntos], intervalo_s=10)) == []
    assert evaluar_senal(SENAL_MEDIDA, _respuesta([puntos], intervalo_s=120)) == []
    assert evaluar_senal(SENAL_MEDIDA, _respuesta([puntos], intervalo_s=60)) != []


def test_dimensiones_juntan_filtros_y_agrupacion():
    """La fila de `anomalias` tiene que decir de que serie se trata: lo que
    fijo la senal y lo que abrio la agrupacion."""
    senal = Senal(metrica="system_filesystem_utilization", clase=MEDIDA,
                  filtros={"state": "used"}, agrupar="mountpoint")
    puntos = _serie(_plano(90) + _plano(30, nivel=40.0, semilla=1))

    hallazgos = evaluar_senal(senal, _respuesta([puntos], etiquetas=[{"mountpoint": "/"}]),
                              ventana=60, persistencia=3)

    assert len(hallazgos) == 1
    assert hallazgos[0].dimensiones == {"state": "used", "mountpoint": "/"}


def test_cada_serie_agrupada_se_evalua_por_separado():
    tranquila = _serie(_plano(120))
    saturada = _serie(_plano(90) + _plano(30, nivel=40.0, semilla=1))
    respuesta = _respuesta([tranquila, saturada],
                           etiquetas=[{"device": "sda"}, {"device": "sdb"}])

    hallazgos = evaluar_senal(SENAL_MEDIDA, respuesta, ventana=60, persistencia=3)

    assert [h.dimensiones for h in hallazgos] == [{"device": "sdb"}]


def test_serie_corta_no_abre_nada():
    assert evaluar_senal(SENAL_MEDIDA, _respuesta([_serie(_plano(4))])) == []


def test_series_omitidas_no_rompen_la_evaluacion(caplog):
    """El repositorio devuelve 8 series como maximo; lo que queda fuera no se
    vigila y eso tiene que verse en los registros."""
    puntos = _serie(_plano(90) + _plano(30, nivel=40.0, semilla=1))
    hallazgos = evaluar_senal(SENAL_MEDIDA, _respuesta([puntos], series_omitidas=3),
                              ventana=60, persistencia=3)
    assert len(hallazgos) == 1
    assert "no se vigilan" in caplog.text


# --------------------------------------------------------------- catalogo
def test_catalogo_usa_nombres_validos():
    """Metricas, dimensiones y agrupaciones tienen que pasar el identificador
    del esquema: si no, el repositorio las rechaza en tiempo de consulta."""
    for senal in SENALES:
        assert es_identificador(senal.metrica), senal.metrica
        for dimension in senal.filtros:
            assert es_identificador(dimension), (senal.metrica, dimension)
        if senal.agrupar:
            assert es_identificador(senal.agrupar), (senal.metrica, senal.agrupar)


def test_catalogo_sin_senales_repetidas():
    claves = [s.clave for s in SENALES]
    assert len(claves) == len(set(claves))


def test_catalogo_declara_clase_y_descripcion():
    for senal in SENALES:
        assert senal.clase in (MEDIDA, ACUMULADA), senal.clave
        assert senal.descripcion, senal.clave


def test_catalogo_no_agrupa_por_una_dimension_que_tambien_filtra():
    """Filtrar y agrupar por la misma dimension dejaria una sola serie con el
    valor ya fijado: senal mal declarada."""
    for senal in SENALES:
        assert senal.agrupar not in senal.filtros, senal.clave


def test_catalogo_cubre_los_tipos_de_falla_del_laboratorio():
    """Los tipos de la restriccion CHECK de `inyecciones`. `latencia` sale de
    un cociente de dos series y todavia no esta cubierta: queda declarado
    aqui para que el hueco sea explicito en la evaluacion de H-029."""
    cubiertos = {f for s in SENALES for f in s.fallas}
    assert {"cpu", "memoria", "disco", "caida"} <= cubiertos
