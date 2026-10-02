import math

import pytest

from falso_influx import ErrorLinea, analizar_linea
from zenit.esquema import canonizar, canonizar_dimension, es_identificador
from zenit.protocolo_linea import construir_linea


@pytest.mark.parametrize("entrada,esperado", [
    ("system.cpu.utilization", "system_cpu_utilization"),
    ("http.server.request.duration", "http_server_request_duration"),
    ("service.name", "service_name"),
    ("System.Memory-Usage", "system_memory_usage"),
    ("process..cpu..time", "process_cpu_time"),
    ("9lives", "m_9lives"),
    ("___", "sin_nombre"),
    ("select", "select_x"),
])
def test_canonizar(entrada, esperado):
    assert canonizar(entrada) == esperado
    assert es_identificador(canonizar(entrada))


def test_una_dimension_no_pisa_columnas_reservadas_ni_canonicas():
    assert canonizar_dimension("time") != "time"
    assert canonizar_dimension("value") != "value"
    assert canonizar_dimension("host.name") == "host_name_dim"
    assert canonizar_dimension("cpu") == "cpu"


def test_identificadores_peligrosos_se_rechazan():
    for malo in ['x"; DROP TABLE y; --', "a b", "", "1abc", "Mayus", "a" * 64]:
        assert not es_identificador(malo)


ETQ = {
    "zenit_asset_id": "activo-aws-nodo-01", "host_name": "zenit-nodo-aws",
    "service_name": "host", "cloud_provider": "aws", "cloud_region": "us-east-1",
    "deployment_environment": "lab",
}


def test_linea_basica_ida_y_vuelta():
    linea = construir_linea("system_cpu_utilization", {**ETQ, "cpu": "cpu0", "state": "user"}, 0.42, 1789741930000000000)
    med, etq, campos, t = analizar_linea(linea)
    assert med == "system_cpu_utilization"
    assert etq["cpu"] == "cpu0" and etq["zenit_asset_id"] == "activo-aws-nodo-01"
    assert campos == {"value": ("flotante", 0.42)}
    assert t == 1789741930000000000


@pytest.mark.parametrize("valor_raro", [
    "con espacio", "con,coma", "con=igual", "barra\\invertida",
    "a\\,b", "ñandú", "dos  espacios", "=,= ,", "\\inicio",
])
def test_escapes_de_etiquetas_sobreviven_ida_y_vuelta(valor_raro):
    linea = construir_linea("m", {"device": valor_raro}, 1.0, 1)
    _, etq, _, _ = analizar_linea(linea)
    assert etq["device"] == valor_raro


def test_escape_de_medicion_y_clave():
    linea = construir_linea("tabla con,coma", {"clave con=igual": "v"}, 1.0, 1)
    med, etq, _, _ = analizar_linea(linea)
    assert med == "tabla con,coma"
    assert etq == {"clave con=igual": "v"}


def test_salto_de_linea_no_parte_la_linea():
    linea = construir_linea("m", {"device": "uno\ndos\r\ntres"}, 1.0, 1)
    assert "\n" not in linea
    assert analizar_linea(linea)[1]["device"] == "uno dos  tres"


def test_barra_invertida_final_se_quita_porque_el_motor_la_rechaza():
    linea = construir_linea("tabla\\", {"mountpoint": "C:\\", "solo": "\\", "k\\": "v"}, 1.0, 1)
    med, etq, _, _ = analizar_linea(linea)
    assert med == "tabla"
    assert etq == {"mountpoint": "C:", "solo": "desconocido", "k": "v"}


def test_el_doble_rechaza_barra_final_como_el_motor():
    with pytest.raises(ErrorLinea):
        analizar_linea("m,a=b\\\\ value=1 1")


def test_etiqueta_vacia_se_vuelve_desconocido():
    linea = construir_linea("m", {"device": "", "mountpoint": "   "}, 1.0, 1)
    etq = analizar_linea(linea)[1]
    assert etq == {"device": "desconocido", "mountpoint": "desconocido"}


@pytest.mark.parametrize("valor", [math.nan, math.inf, -math.inf])
def test_nan_e_infinito_no_se_escriben(valor):
    assert construir_linea("m", {}, valor, 1) is None


@pytest.mark.parametrize("valor", [0, 7, 0.0, 1e-9, 1e20, -3.5, 123456789012345])
def test_todo_valor_sale_como_flotante(valor):
    _, _, campos, _ = analizar_linea(construir_linea("m", {}, valor, 1))
    tipo, v = campos["value"]
    assert tipo == "flotante"
    assert v == pytest.approx(float(valor))


def test_el_analizador_del_doble_es_estricto():
    """Si el doble aceptara cualquier cosa, las pruebas de arriba no valdrian."""
    for mala in ["m,a= value=1 1", "m,=b value=1 1", "m value=1 x", "m", "m,a=b", "m value=abc 1"]:
        with pytest.raises(ErrorLinea):
            analizar_linea(mala)


def test_tabulador_y_otros_caracteres_de_control_no_rompen_la_linea():
    linea = construir_linea("m", {"route": "a\tb", "x": "nul\x00y"}, 1.0, 1)
    assert "\t" not in linea and "\x00" not in linea
    assert analizar_linea(linea)[1] == {"route": "a b", "x": "nul y"}


def test_valor_larguisimo_se_recorta():
    linea = construir_linea("m", {"k": "x" * 100_000}, 1.0, 1)
    assert len(analizar_linea(linea)[1]["k"]) == 1024


def test_marca_de_tiempo_fuera_de_rango_no_se_escribe():
    assert construir_linea("m", {}, 1.0, 2**63) is None
    assert construir_linea("m", {}, 1.0, 2**64 - 1) is None
    assert construir_linea("m", {}, 1.0, 2**63 - 1) is not None
