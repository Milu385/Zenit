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

from zenit.deteccion.cierre import NORMALIDAD_PARA_CERRAR, momento_de_cierre
from zenit.deteccion.detector import (
    PERSISTENCIA_DEFECTO,
    Z_CRITICO,
    Z_NORMAL,
    Baseline,
    calcular_baseline,
    clasificar,
    detectar_eventos,
    evaluar_senal,
    puntaje_z,
    severidad,
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


# ------------------------------------------------------------------- ciclo
# Con dobles de PostgreSQL y del repositorio: cubre el cableado del ciclo
# (filtrado por metricas disponibles, deduplicacion, parametros del INSERT)
# sin levantar nada. La prueba contra PostgreSQL y un doble de Influx, como
# en test_agregacion.py, sigue pendiente.
ACTIVO = "activo-azure-nodo-01"


class _FalsoCursor:
    def __init__(self, conexion):
        self.conexion = conexion
        self.rowcount = 0
        self._filas: list = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        limpio = sql.lstrip()
        if limpio.startswith("INSERT"):
            self.conexion.inserts.append(params)
        elif limpio.startswith("UPDATE"):
            self.conexion.cerradas.append(params)
            self.rowcount = 1
        elif limpio.startswith("SELECT id,"):      # cierre.abiertas
            self._filas = list(self.conexion.filas_abiertas)

    def fetchall(self):
        return self._filas

    def fetchone(self):
        return (1,) if self.conexion.hay_abierta else None


class _FalsaConexion:
    def __init__(self, hay_abierta=False, filas_abiertas=()):
        self.inserts: list = []
        self.cerradas: list = []
        self.filas_abiertas = list(filas_abiertas)
        self.hay_abierta = hay_abierta
        self.commits = 0

    def cursor(self):
        return _FalsoCursor(self)

    def commit(self):
        self.commits += 1


class _FalsoRepositorio:
    """Solo reporta las metricas de `disponibles`; la de `saturada` trae un
    escalon y las demas una serie plana."""

    def __init__(self, disponibles, saturada=None):
        self.disponibles = list(disponibles)
        self.saturada = saturada
        self.consultas: list = []

    def metricas_de(self, activo):
        return list(self.disponibles)

    def serie(self, activo, metrica, desde, hasta, *, agrupar=None, filtros=None):
        self.consultas.append((metrica, agrupar, filtros))
        valores = (_plano(90) + _plano(30, nivel=40.0, semilla=1)
                   if metrica == self.saturada else _plano(120))
        return _respuesta([_serie(valores)],
                          etiquetas=[{agrupar: "x"} if agrupar else {}])


def _conectado(monkeypatch, conexion, activos=(ACTIVO,)):
    import contextlib

    from zenit import catalogo

    monkeypatch.setattr(catalogo, "conectar",
                        lambda url, autocommit=False: contextlib.nullcontext(conexion))
    monkeypatch.setattr(catalogo, "listar_activos",
                        lambda c, incluir_retirados=True: [{"id": a} for a in activos])


def _desenvolver(valor):
    """Los jsonb van envueltos en psycopg.types.json.Jsonb."""
    return getattr(valor, "obj", valor)


def test_ciclo_solo_consulta_las_metricas_que_existen(monkeypatch):
    from zenit.deteccion.detector import Detector

    conexion = _FalsaConexion()
    _conectado(monkeypatch, conexion)
    repo = _FalsoRepositorio(["system_cpu_utilization"])

    resumen = Detector(repo, "postgresql://doble").ciclo()

    assert resumen["activos"] == 1
    # las tres senales de system_cpu_utilization, y ninguna de las demas
    assert {m for m, _, _ in repo.consultas} == {"system_cpu_utilization"}
    assert resumen["senales"] == 3


def test_ciclo_pasa_los_filtros_de_la_senal_al_repositorio(monkeypatch):
    from zenit.deteccion.detector import Detector

    conexion = _FalsaConexion()
    _conectado(monkeypatch, conexion)
    repo = _FalsoRepositorio(["system_filesystem_usage"])

    Detector(repo, "postgresql://doble").ciclo()

    assert repo.consultas == [("system_filesystem_usage", "mountpoint", {"state": "used"})]


def test_ciclo_abre_la_anomalia_con_la_forma_de_la_tabla(monkeypatch):
    from zenit.deteccion.detector import Detector, version_modelo

    conexion = _FalsaConexion()
    _conectado(monkeypatch, conexion)
    repo = _FalsoRepositorio(["system_memory_utilization"], saturada="system_memory_utilization")

    resumen = Detector(repo, "postgresql://doble").ciclo()

    assert resumen["anomalias_abiertas"] == 1
    assert conexion.commits == 1
    activo, metrica, dimensiones, inicio, severidad, puntaje, evidencia, version = conexion.inserts[0]
    assert (activo, metrica) == (ACTIVO, "system_memory_utilization")
    assert _desenvolver(dimensiones) == {"state": "used"}
    assert inicio.tzinfo is not None                  # timestamptz, nunca ingenuo
    assert severidad in ("baja", "media", "alta")
    assert puntaje >= Z_CRITICO
    assert version == version_modelo()
    evid = _desenvolver(evidencia)
    assert {"ventana", "persistencia", "valor_observado", "valor_esperado",
            "metodo", "senal", "clase", "resolucion_s"} <= set(evid)


def test_ciclo_no_duplica_una_anomalia_ya_abierta(monkeypatch):
    """Mientras siga abierta no se abre otra: cerrarla es trabajo de H-033."""
    from zenit.deteccion.detector import Detector

    conexion = _FalsaConexion(hay_abierta=True)
    _conectado(monkeypatch, conexion)
    repo = _FalsoRepositorio(["system_memory_utilization"], saturada="system_memory_utilization")

    resumen = Detector(repo, "postgresql://doble").ciclo()

    assert resumen["anomalias_abiertas"] == 0
    assert conexion.inserts == []


def test_ciclo_no_abre_si_la_fuente_esta_degradada(monkeypatch):
    """Cuarto criterio de H-033: una fuente callada no es una anomalia del
    activo. La regla de degradado es la de zenit.salud, no una copia."""
    from zenit import salud
    from zenit.deteccion.detector import Detector

    conexion = _FalsaConexion()
    _conectado(monkeypatch, conexion)
    callada = INICIO - salud.DEGRADADA_TRAS - timedelta(minutes=1)
    monkeypatch.setattr(salud, "fuentes_pg", lambda c: [
        {"activo": ACTIVO, "host_name": "h", "senal": salud.SENAL_LATIDO, "ultima": callada},
    ])
    repo = _FalsoRepositorio(["system_memory_utilization"], saturada="system_memory_utilization")

    resumen = Detector(repo, "postgresql://doble").ciclo(ahora=INICIO)

    assert resumen["degradados"] == 1
    assert resumen["anomalias_abiertas"] == 0
    assert conexion.inserts == []
    assert repo.consultas == []        # ni se consulta la serie


def test_ciclo_si_abre_cuando_la_fuente_reporta(monkeypatch):
    from zenit import salud
    from zenit.deteccion.detector import Detector

    conexion = _FalsaConexion()
    _conectado(monkeypatch, conexion)
    monkeypatch.setattr(salud, "fuentes_pg", lambda c: [
        {"activo": ACTIVO, "host_name": "h", "senal": salud.SENAL_LATIDO,
         "ultima": INICIO - timedelta(seconds=20)},
    ])
    repo = _FalsoRepositorio(["system_memory_utilization"], saturada="system_memory_utilization")

    resumen = Detector(repo, "postgresql://doble").ciclo(ahora=INICIO)

    assert resumen["degradados"] == 0
    assert resumen["anomalias_abiertas"] == 1


def test_revisar_catalogo_reporta_los_dos_sentidos(monkeypatch):
    """Un nombre equivocado sale como senal que falta y, al lado, como
    metrica que llega sin vigilancia."""
    from zenit.deteccion.detector import Detector

    conexion = _FalsaConexion()
    _conectado(monkeypatch, conexion)
    repo = _FalsoRepositorio(["system_memory_utilization", "una_metrica_nueva"])

    informe = Detector(repo, "postgresql://doble").revisar_catalogo()

    estados = {f["senal"]: f["estado"] for f in informe["filas"]}
    assert estados["system_memory_utilization[state=used]"] == "ok"
    assert estados["system_cpu_utilization[state=user]"] == "falta la metrica"
    assert informe["sin_vigilar"] == {ACTIVO: ["una_metrica_nueva"]}


# ------------------------------------------------------------ H-033: severidad
def test_severidad_usa_los_cortes_del_cuaderno():
    """Los dos incrementos son los mismos que separan bueno/normal/critico
    (1.5 y 3.5), aplicados por encima del corte critico."""
    assert severidad(Z_CRITICO) == "baja"
    assert severidad(4.9) == "baja"
    assert severidad(5.0) == "media"            # 3.5 + 1.5
    assert severidad(6.9) == "media"
    assert severidad(7.0) == "alta"             # 3.5 + 3.5
    assert severidad(900.0) == "alta"


def test_severidad_sigue_al_corte_critico_si_se_ajusta():
    """Un administrador que mueva el corte critico (HU-52) mueve con el toda
    la escala de severidad, sin numeros nuevos."""
    assert severidad(5.0, z_critico=5.0) == "baja"
    assert severidad(6.5, z_critico=5.0) == "media"
    assert severidad(8.5, z_critico=5.0) == "alta"


# -------------------------------------------------------------- H-033: cierre
LINEA_BASE = {"mediana": 10.0, "escala": 0.5}    # z = 3.5 en 11.75


def _normales(n):
    return _serie([10.0] * n)


def test_no_cierra_mientras_la_desviacion_sigue():
    puntos = _serie([10.0] * 5 + [40.0] * 20)    # sigue muy por encima
    assert momento_de_cierre(puntos, 10.0, 0.5, normalidad=15) is None


def test_cierra_cuando_lleva_el_tiempo_exigido_en_normal():
    puntos = _serie([40.0] * 10 + [10.0] * 15)   # 15 minutos seguidos normales
    fin = momento_de_cierre(puntos, 10.0, 0.5, normalidad=15)
    assert fin == puntos[-1][0]


def test_no_cierra_con_normalidad_mas_corta_que_la_exigida():
    puntos = _serie([40.0] * 10 + [10.0] * 14)   # uno menos
    assert momento_de_cierre(puntos, 10.0, 0.5, normalidad=15) is None


def test_la_falta_de_datos_no_es_normalidad():
    """Con menos puntos que los exigidos no se afirma que la desviacion ceso."""
    assert momento_de_cierre(_normales(5), 10.0, 0.5, normalidad=15) is None
    assert momento_de_cierre([], 10.0, 0.5, normalidad=15) is None


def test_cierra_contra_la_linea_base_congelada_no_contra_una_nueva():
    """El hallazgo que motiva todo `cierre.py`: un escalon sostenido hace que
    una ventana movil lo declare normal a la media hora. Contra la linea base
    congelada al abrir, el mismo escalon sigue siendo desviacion."""
    sostenido = _serie([40.0] * 40)              # 40 minutos al nivel del fallo

    # congelada (lo que hace el cierre): no cierra
    assert momento_de_cierre(sostenido, 10.0, 0.5, normalidad=15) is None

    # recalculada sobre los propios puntos del fallo: la declararia normal
    movil = calcular_baseline([v for _, v in sostenido[-16:-1]])
    assert momento_de_cierre(sostenido, movil.mediana, movil.escala or 1.0, normalidad=15) is not None


def test_degradacion_intermitente_no_cierra_entre_rebotes():
    """Tercer criterio de H-033: si rebota antes de completar el tiempo de
    normalidad, la anomalia sigue abierta y no se abre otra nueva."""
    rebotes = []
    for _ in range(4):
        rebotes += [40.0] * 3 + [10.0] * 8       # 8 minutos normales, menos de 15
    assert momento_de_cierre(_serie(rebotes), 10.0, 0.5, normalidad=15) is None


def test_el_tiempo_de_cierre_cabe_en_el_hueco_de_la_campana():
    """La campana garantiza 45 minutos de normalidad entre inyecciones
    (`normal_min` en campana.py). El tiempo exigido tiene que ser bastante
    menor, o dos inyecciones seguidas se funden en una sola anomalia."""
    assert NORMALIDAD_PARA_CERRAR < 45 / 2
    assert NORMALIDAD_PARA_CERRAR > PERSISTENCIA_DEFECTO * 2


# ------------------------------------------------- H-033: cierre en el ciclo
def _fila_abierta(**cambios):
    """Una fila de `anomalias` sin cerrar, en el orden de columnas que lee
    cierre.abiertas: asi la prueba ejercita ese parseo de verdad."""
    datos = {"id": 7, "activo": ACTIVO, "metrica": "system_memory_utilization",
             "dimensiones": {"state": "used"}, "inicio": INICIO, "estado": "detectada",
             "evidencia": {"clase": "medida", "linea_base": dict(LINEA_BASE)}}
    datos.update(cambios)
    return tuple(datos[c] for c in ("id", "activo", "metrica", "dimensiones",
                                    "inicio", "estado", "evidencia"))


def test_ciclo_cierra_la_anomalia_que_volvio_a_normal(monkeypatch):
    from zenit.deteccion.detector import Detector

    conexion = _FalsaConexion(filas_abiertas=[_fila_abierta()])
    _conectado(monkeypatch, conexion)
    repo = _FalsoRepositorio(["system_memory_utilization"])     # serie plana: normal

    resumen = Detector(repo, "postgresql://doble").ciclo(ahora=INICIO)

    assert resumen["anomalias_cerradas"] == 1
    assert conexion.cerradas and conexion.cerradas[0][1] == 7


def test_ciclo_no_cierra_si_la_fuente_esta_degradada(monkeypatch):
    """Sin datos no se puede afirmar que la desviacion ceso."""
    from zenit import salud
    from zenit.deteccion.detector import Detector

    conexion = _FalsaConexion(filas_abiertas=[_fila_abierta()])
    _conectado(monkeypatch, conexion)
    callada = INICIO - salud.DEGRADADA_TRAS - timedelta(minutes=1)
    monkeypatch.setattr(salud, "fuentes_pg", lambda c: [
        {"activo": ACTIVO, "host_name": "h", "senal": salud.SENAL_LATIDO, "ultima": callada},
    ])
    repo = _FalsoRepositorio(["system_memory_utilization"])

    resumen = Detector(repo, "postgresql://doble").ciclo(ahora=INICIO)

    assert resumen["anomalias_cerradas"] == 0
    assert conexion.cerradas == []


def test_ciclo_no_cierra_una_anomalia_sin_linea_base(monkeypatch):
    """Una fila vieja sin `linea_base` en su evidencia no se cierra sola: se
    avisa, en vez de recalcular una ventana que mentiria."""
    from zenit.deteccion.detector import Detector

    conexion = _FalsaConexion(filas_abiertas=[_fila_abierta(evidencia={"clase": "medida"})])
    _conectado(monkeypatch, conexion)
    repo = _FalsoRepositorio(["system_memory_utilization"])

    assert Detector(repo, "postgresql://doble").ciclo(ahora=INICIO)["anomalias_cerradas"] == 0
    assert conexion.cerradas == []


def test_un_hueco_de_datos_no_cuenta_como_normalidad():
    """Quince puntos normales con un corte de datos en medio no son quince
    minutos seguidos de normalidad."""
    seguidos = _serie([40.0] * 5 + [10.0] * 15)
    assert momento_de_cierre(seguidos, 10.0, 0.5, normalidad=15) is not None

    # los mismos 15 puntos normales, pero los ultimos 5 llegan media hora despues
    con_hueco = seguidos[:15] + [(t + timedelta(minutes=30), v) for t, v in seguidos[15:]]
    assert momento_de_cierre(con_hueco, 10.0, 0.5, normalidad=15) is None
