"""H-029: evaluacion por evento (zenit.deteccion.evaluacion).

Pruebas puras, con inyecciones y anomalias construidas a mano: la regla de
emparejamiento, lo que cuenta y lo que no, y el informe.
"""
import csv
import json
from datetime import datetime, timedelta, timezone

from zenit.deteccion.evaluacion import (
    MARGEN_FIN,
    Anomalia,
    Inyeccion,
    evaluar,
    fecha,
    informe,
    leer_inyecciones,
    marcas_del_operador,
    metricas,
    principal,
)

T0 = datetime(2026, 10, 25, tzinfo=timezone.utc)
MIN = timedelta(minutes=1)
NODO = "activo-azure-nodo-01"
OTRO = "activo-azure-nodo-02"
DESDE, HASTA = T0 - timedelta(hours=1), T0 + timedelta(days=1)


def _iny(uid, minuto, duracion=10, tipo="cpu", activo=NODO, estado="completada"):
    inicio = T0 + minuto * MIN
    return Inyeccion(uid=uid, tipo=tipo, activo=activo, inicio=inicio,
                     fin=inicio + duracion * MIN, estado=estado)


def _ano(id_, minuto, activo=NODO, estado="detectada", version="mad-v2 W=60 z=3.5 m=3",
         metrica="system_cpu_utilization"):
    return Anomalia(id=id_, activo=activo, metrica=metrica, inicio=T0 + minuto * MIN,
                    estado=estado, version_modelo=version)


def test_emparejamiento_dentro_y_hasta_el_fin_mas_cinco_minutos():
    iny = _iny("a", 0, duracion=10)
    dentro = _ano("1", 2)
    limite = _ano("2", 10 + 5)                   # fin + 5 min justo: cuenta
    tarde = _ano("3", 10 + 5 + 1)                # un minuto despues: falso positivo
    antes = _ano("4", -1)                        # antes del inicio: falso positivo

    r = evaluar([iny], [dentro, limite, tarde, antes], DESDE, HASTA)

    assert set(r.emparejadas) == {"1", "2"}
    m = metricas(r)
    assert (m["anomalias_correctas"], m["falsos_positivos"]) == (2, 2)
    assert m["precision"] == 0.5 and m["exhaustividad"] == 1.0
    assert MARGEN_FIN == timedelta(minutes=5)


def test_otro_activo_no_empareja():
    r = evaluar([_iny("a", 0)], [_ano("1", 2, activo=OTRO)], DESDE, HASTA)
    m = metricas(r)
    assert m["detectadas"] == 0 and m["falsos_positivos"] == 1


def test_varias_anomalias_de_una_inyeccion_son_un_acierto():
    r = evaluar([_iny("a", 0)], [_ano("1", 2), _ano("2", 3, metrica="system_cpu_load_average_1m")],
                DESDE, HASTA)
    m = metricas(r)
    assert m["detectadas"] == 1 and m["anomalias_correctas"] == 2 and m["precision"] == 1.0
    assert m["retardo_medio_min"] == 2.0         # la primera de las dos


def test_las_fallidas_no_cuentan_ni_como_acierto_ni_como_falso_positivo():
    r = evaluar([_iny("a", 0, estado="fallida")], [_ano("1", 2)], DESDE, HASTA)
    m = metricas(r)
    assert m["inyecciones"] == 0 and m["falsos_positivos"] == 0
    assert [motivo for _, motivo in r.ignoradas] == ["inyeccion fallida"]


def test_la_latencia_queda_fuera_de_los_totales_salvo_que_se_pida():
    lat = _iny("l", 0, tipo="latencia")
    r = evaluar([lat], [_ano("1", 2)], DESDE, HASTA)
    assert metricas(r)["inyecciones"] == 0
    assert len(r.fuera_del_alcance) == 1 and metricas(r)["falsos_positivos"] == 0

    r = evaluar([lat], [], DESDE, HASTA, incluir_latencia=True)
    assert metricas(r)["inyecciones"] == 1 and metricas(r)["exhaustividad"] == 0.0


def test_ventana_y_version():
    vieja = _ano("1", 2, version="mad-v1 W=60 z=3.5 m=3")
    nueva = _ano("2", 3)
    fuera = Anomalia(id="3", activo=NODO, metrica="m", inicio=DESDE - MIN, version_modelo="mad-v2")
    r = evaluar([_iny("a", 0)], [vieja, nueva, fuera], DESDE, HASTA, version="mad-v2")
    assert [a.id for a in r.anomalias] == ["2"]
    assert r.versiones == {"mad-v2 W=60 z=3.5 m=3": 1}


def test_una_inyeccion_antes_de_la_ventana_no_se_evalua():
    """La evaluacion cuenta desde el despliegue de los arreglos: lo de antes
    no es ni acierto ni fallo de esta version."""
    r = evaluar([_iny("a", -200)], [], DESDE, HASTA)
    assert metricas(r)["inyecciones"] == 0


def test_metricas_por_tipo():
    inys = [_iny("c", 0, tipo="cpu"), _iny("g", 100, tipo="disco_gradual"),
            _iny("b", 200, tipo="disco_abrupto")]
    r = evaluar(inys, [_ano("1", 2), _ano("2", 203)], DESDE, HASTA)
    assert metricas(r, "cpu")["exhaustividad"] == 1.0
    assert metricas(r, "disco_gradual")["exhaustividad"] == 0.0
    assert metricas(r, "disco_abrupto")["retardo_medio_min"] == 3.0
    assert metricas(r, "cpu")["falsos_positivos"] is None      # un falso positivo no tiene tipo


def test_marcas_del_operador():
    anos = [_ano("1", 2, estado="falso_positivo"),       # marcada, pero era cierta
            _ano("2", 300, estado="falso_positivo"),     # marcada y era falsa
            _ano("3", 400)]                              # falsa, sin marcar
    mo = marcas_del_operador(evaluar([_iny("a", 0)], anos, DESDE, HASTA))
    assert mo == {"marcadas": 2, "eran_falso_positivo": 1, "estaban_emparejadas": 1,
                  "falsos_positivos_sin_marcar": 1}


def test_fecha_acepta_los_formatos_de_la_exportacion():
    esperado = datetime(2026, 10, 21, 13, 20, tzinfo=timezone.utc)
    for texto in ("2026-10-21T13:20:00+00:00", "2026-10-21 13:20:00+00", "2026-10-21T13:20Z",
                  "2026-10-21 08:20:00-05"):
        assert fecha(texto) == esperado, texto
    assert fecha("") is None


def test_disco_se_parte_por_modo(tmp_path):
    ruta = tmp_path / "inyecciones.csv"
    with open(ruta, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["uid", "tipo", "activo", "inicio", "fin", "parametros", "estado"])
        w.writerow(["g", "disco", NODO, "2026-10-25T00:00:00+00:00", "", json.dumps({"modo": "gradual"}), "completada"])
        w.writerow(["b", "disco", NODO, "2026-10-25T01:00:00+00:00", "", json.dumps({"modo": "abrupto"}), "completada"])
        w.writerow(["c", "cpu", "", "2026-10-25T02:00:00+00:00", "", "{}", "completada"])
    inys = leer_inyecciones(ruta)
    assert [i.tipo for i in inys] == ["disco_gradual", "disco_abrupto", "cpu"]
    assert inys[2].activo is None                # sin activo resuelto: no se evalua
    assert inys[0].fin is None


def test_informe_y_archivos(tmp_path):
    iny = tmp_path / "inyecciones.csv"
    with open(iny, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["uid", "tipo", "activo", "inicio", "fin", "parametros", "estado"])
        w.writerow(["a", "memoria", NODO, "2026-10-25T00:00:00+00:00", "2026-10-25T00:10:00+00:00", "{}", "completada"])
    ano = tmp_path / "anomalias.csv"
    with open(ano, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "activo", "metrica", "dimensiones", "inicio", "estado", "version_modelo"])
        w.writerow(["9", NODO, "system_memory_utilization", '{"state": "used"}',
                    "2026-10-25 00:03:00+00", "detectada", "mad-v2 W=60 z=3.5 m=3"])

    assert principal(["--inyecciones", str(iny), "--anomalias", str(ano),
                      "--desde", "2026-10-24T00:00Z", "--hasta", "2026-10-26T00:00Z",
                      "--salida", str(tmp_path / "salida")]) == 0

    texto = (tmp_path / "salida" / "informe.md").read_text()
    assert "`mad-v2 W=60 z=3.5 m=3` (1)" in texto
    assert "| memoria | 1 | 1 | 100,0% |" in texto
    filas = list(csv.DictReader(open(tmp_path / "salida" / "por_inyeccion.csv")))
    assert filas[0]["detectada"] == "True" and float(filas[0]["retardo_min"]) == 3.0


def test_informe_declara_lo_que_no_se_cuenta():
    r = evaluar([_iny("l", 0, tipo="latencia"), _iny("f", 100, estado="fallida")],
                [_ano("1", 2), _ano("2", 102)], DESDE, HASTA)
    texto = informe(r, corte=T0)
    assert "Inyecciones de latencia: 1" in texto
    assert "inyeccion fallida: 1" in texto
