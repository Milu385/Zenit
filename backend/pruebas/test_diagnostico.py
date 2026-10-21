"""H-040: diagnostico como hipotesis con evidencia citada."""
import io
import json
import os
import urllib.error
from datetime import datetime, timedelta, timezone

import pytest
from psycopg.types.json import Jsonb

from conftest import preparar_pg
from sesion_prueba import con_sesion, opciones
from test_evidencia import ACTIVO, FIN, INICIO, FalsoAnalitico, FalsoRepositorio
from zenit import catalogo
from zenit.api.main import crear_app
from zenit.diagnostico import hipotesis, modelo
from zenit.diagnostico.__main__ import pendientes
from zenit.diagnostico.evidencia import recuperar
from zenit.diagnostico.modelo import ErrorModelo, Respuesta

PG = os.environ.get("ZENIT_PG_PRUEBAS")
con_pg = pytest.mark.skipif(not PG, reason="sin ZENIT_PG_PRUEBAS")
AHORA = datetime(2026, 10, 21, 15, 0, tzinfo=timezone.utc)


# ------------------------------------------------------------------ dobles
def falso_redactor():
    """Reemplaza los valores de `conocidos` por marcadores, como hara H-041."""
    def redactar(obj, conocidos):
        mapa, contadores = {}, {}

        def marcador(valor, tipo):
            for m, v in mapa.items():
                if v == valor:
                    return m
            contadores[tipo] = contadores.get(tipo, 0) + 1
            m = f"[{tipo}_{contadores[tipo]}]"
            mapa[m] = valor
            return m

        def texto(s):
            for valor in sorted(conocidos, key=len, reverse=True):
                if valor in s:
                    s = s.replace(valor, marcador(valor, conocidos[valor]))
            return s

        def recorrer(o):
            if isinstance(o, dict):
                return {k: recorrer(v) for k, v in o.items()}
            if isinstance(o, list):
                return [recorrer(v) for v in o]
            return texto(o) if isinstance(o, str) else o

        return recorrer(obj), mapa

    def restaurar(s, mapa):
        for m, v in mapa.items():
            s = s.replace(m, v)
        return s

    return redactar, restaurar


def respuesta_modelo(citas=None, envuelta=False):
    citas = citas if citas is not None else [
        {"metrica": "system_filesystem_usage", "desde": "2026-10-21T13:20:00+00:00",
         "hasta": "2026-10-21T13:31:00+00:00", "observacion": "el disco de [ACTIVO_1] subio de 7,6 a 10,8 GB"},
        {"metrica": "system_disk_io", "desde": "2026-10-21T13:18:00+00:00", "hasta": "2026-10-21T13:31:00+00:00",
         "observacion": "la escritura crecio en el mismo tramo"},
        {"metrica": "metrica_inventada", "desde": "2026-10-21T13:20:00+00:00", "hasta": "2026-10-21T13:31:00+00:00",
         "observacion": "no existe"},
        {"metrica": "system_disk_io", "desde": "2026-10-20T01:00:00+00:00", "hasta": "2026-10-20T02:00:00+00:00",
         "observacion": "fuera de la ventana"},
    ]
    datos = {"hipotesis": "Probablemente un proceso de [ACTIVO_1] escribio un archivo grande en /.",
             "evidencia_citada": citas,
             "acciones_sugeridas": ["Revisar que archivo crecio en / de [ACTIVO_1]"],
             "confianza": "media"}
    texto = json.dumps(datos, ensure_ascii=False)
    return f"```json\n{texto}\n```" if envuelta else texto


class FalsoModelo:
    def __init__(self, texto=None, falla=False):
        self.texto = texto if texto is not None else respuesta_modelo()
        self.falla = falla
        self.mensajes = []

    def generar(self, sistema, mensaje, max_tokens=1200):
        self.mensajes.append(mensaje)
        if self.falla:
            raise ErrorModelo("el proveedor no respondio: timeout")
        return Respuesta(texto=self.texto, tokens_entrada=1800, tokens_salida=240, modelo="modelo-fijo-2026")


ANOMALIA = {"id": 29, "activo": ACTIVO, "metrica": "system_filesystem_usage",
            "dimensiones": {"state": "used", "mountpoint": "/"}, "inicio": INICIO, "fin": FIN,
            "severidad": "alta", "puntaje": 658.7, "version_modelo": "mad-v1",
            "evidencia": {"valor_observado": 10.8e9, "valor_esperado": 7.6e9}}


# ------------------------------------------------------------------ interpretar
def _ev():
    return recuperar(ANOMALIA, FalsoRepositorio(), FalsoAnalitico(), AHORA)


def test_solo_quedan_las_citas_comprobables():
    redactar, restaurar = falso_redactor()
    r = hipotesis.interpretar(respuesta_modelo(), _ev(), {"[ACTIVO_1]": ACTIVO}, restaurar)
    assert [c["metrica"] for c in r["evidencia_citada"]] == ["system_filesystem_usage", "system_disk_io"]
    assert len(r["descartadas"]) == 2
    assert ACTIVO in r["evidencia_citada"][0]["observacion"]          # marcadores devueltos
    assert r["texto"].startswith("Probablemente") and ACTIVO in r["texto"]
    assert "Zenit no ejecuta ninguna" in r["texto"] and "Confianza de la hipotesis: media" in r["texto"]


def test_acepta_la_respuesta_en_un_bloque_de_codigo():
    _, restaurar = falso_redactor()
    r = hipotesis.interpretar(respuesta_modelo(envuelta=True), _ev(), {}, restaurar)
    assert r["evidencia_citada"]


def test_sin_citas_validas_no_hay_diagnostico():
    _, restaurar = falso_redactor()
    with pytest.raises(ErrorModelo, match="no cito"):
        hipotesis.interpretar(respuesta_modelo(citas=[]), _ev(), {}, restaurar)


def test_una_respuesta_sin_json_es_un_error():
    _, restaurar = falso_redactor()
    with pytest.raises(ErrorModelo):
        hipotesis.interpretar("No puedo ayudar con eso.", _ev(), {}, restaurar)


def test_lo_que_se_envia_esta_recortado():
    enviado = hipotesis.compactar(ANOMALIA, _ev())
    afectada = [m for m in enviado["metricas"] if m["papel"] == "afectada"][0]
    assert len(afectada["puntos"]) <= hipotesis.MAX_PUNTOS_PROMPT
    assert all("puntos" not in m for m in enviado["metricas"] if m["papel"] == "relacionada")
    assert enviado["incidente"]["valor_observado"] == pytest.approx(10.8e9)


def test_sin_redaccion_no_se_envia_nada(monkeypatch):
    import builtins

    original = builtins.__import__

    def sin_redaccion(nombre, *a, **kw):
        if nombre == "zenit.diagnostico.redaccion":
            raise ImportError(nombre)
        return original(nombre, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", sin_redaccion)
    with pytest.raises(hipotesis.SinRedaccion):
        hipotesis.cargar_redactor()


# ------------------------------------------------------------------ modelo
def test_la_version_del_modelo_es_fija(monkeypatch):
    monkeypatch.delenv("ZENIT_MODELO_DIAGNOSTICO", raising=False)
    with pytest.raises(modelo.SinConfiguracion):
        modelo.modelo_configurado()
    monkeypatch.setenv("ZENIT_MODELO_DIAGNOSTICO", "claude-algo-latest")
    with pytest.raises(modelo.SinConfiguracion, match="alias"):
        modelo.modelo_configurado()
    monkeypatch.setenv("ZENIT_MODELO_DIAGNOSTICO", "claude-version-fija-20260101")
    assert modelo.modelo_configurado() == "claude-version-fija-20260101"


def test_el_cliente_envia_la_version_sin_pensamiento_previo(monkeypatch):
    vistas = {}

    def urlopen(peticion, timeout):
        vistas["cuerpo"] = json.loads(peticion.data)
        vistas["cabeceras"] = dict(peticion.header_items())
        return io.BytesIO(json.dumps({"model": "claude-version-fija", "content": [{"type": "text", "text": "{}"}],
                                      "usage": {"input_tokens": 10, "output_tokens": 3}}).encode())

    monkeypatch.setattr(modelo.urllib.request, "urlopen", urlopen)
    r = modelo.ClienteAnthropic("clave", "claude-version-fija").generar("sistema", "mensaje")
    assert vistas["cuerpo"]["model"] == "claude-version-fija"
    assert "temperature" not in vistas["cuerpo"]                       # 400 en los modelos 5.5
    assert vistas["cuerpo"]["thinking"] == {"type": "between_tools"}
    assert vistas["cuerpo"]["output_config"] == {"effort": "low"}
    assert vistas["cabeceras"]["X-api-key"] == "clave"
    assert (r.tokens_entrada, r.tokens_salida, r.modelo) == (10, 3, "claude-version-fija")


def test_un_error_del_proveedor_es_error_del_modelo(monkeypatch):
    def urlopen(peticion, timeout):
        raise urllib.error.HTTPError(peticion.full_url, 529, "sobrecargado", {}, io.BytesIO(b"overloaded"))

    monkeypatch.setattr(modelo.urllib.request, "urlopen", urlopen)
    with pytest.raises(ErrorModelo, match="529"):
        modelo.ClienteAnthropic("clave", "m").generar("s", "m")


# ------------------------------------------------------------------ con PostgreSQL
@pytest.fixture
def pg():
    conexion = preparar_pg(PG)
    catalogo.agregar_manual(conexion, "zenit-nodo-azure", ACTIVO, "azure")
    conexion.commit()
    yield conexion
    conexion.close()


def _insertar(c, severidad="alta", estado="detectada", hace_min=1):
    with c.cursor() as cur:
        cur.execute(
            "INSERT INTO anomalias (activo, metrica, dimensiones, inicio, fin, estado, severidad, puntaje, evidencia, "
            "version_modelo, detectada_en) VALUES (%s, 'system_filesystem_usage', %s, %s, %s, %s, %s, 658.7, %s, "
            "'mad-v1', now() - make_interval(mins => %s)) RETURNING id",
            (ACTIVO, Jsonb({"state": "used", "mountpoint": "/"}), INICIO, FIN, estado, severidad,
             Jsonb({"ventana": 60, "valor_observado": 10.8e9, "valor_esperado": 7.6e9}), hace_min))
        (id_,) = cur.fetchone()
    c.commit()
    return id_


@con_pg
def test_diagnosticar_guarda_la_hipotesis_lo_enviado_y_el_tiempo(pg):
    id_ = _insertar(pg)
    cliente = FalsoModelo()
    d = hipotesis.diagnosticar(pg, id_, FalsoRepositorio(), FalsoAnalitico(), cliente,
                               redactor=falso_redactor(), ahora=AHORA)
    assert d["modelo"] == "modelo-fijo-2026" and d["tokens_entrada"] == 1800
    assert ACTIVO not in cliente.mensajes[0] and "zenit-nodo-azure" not in cliente.mensajes[0]   # redactado
    with pg.cursor() as cur:
        cur.execute("SELECT a.estado, d.evidencia_recuperada FROM anomalias a JOIN diagnosticos d "
                    "ON d.anomalia_id = a.id WHERE a.id = %s", (id_,))
        estado, guardada = cur.fetchone()
    assert estado == "diagnosticada"
    assert ACTIVO not in json.dumps(guardada["enviado"])
    assert len(guardada["citas_descartadas"]) == 2
    assert 0 <= guardada["segundos_desde_deteccion"] < 600


@con_pg
def test_si_el_modelo_falla_no_se_escribe_nada(pg):
    id_ = _insertar(pg)
    with pytest.raises(ErrorModelo):
        hipotesis.diagnosticar(pg, id_, FalsoRepositorio(), FalsoAnalitico(), FalsoModelo(falla=True),
                               redactor=falso_redactor(), ahora=AHORA)
    pg.rollback()
    with pg.cursor() as cur:
        cur.execute("SELECT count(*) FROM diagnosticos")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT estado FROM anomalias WHERE id = %s", (id_,))
        assert cur.fetchone()[0] == "detectada"


@con_pg
def test_el_servicio_solo_toma_recientes_de_severidad_alta_o_media_sin_diagnostico(pg):
    alta = _insertar(pg, "alta")
    _insertar(pg, "baja")
    _insertar(pg, "alta", hace_min=120)
    _insertar(pg, "alta", estado="falso_positivo")
    ya = _insertar(pg, "media")
    hipotesis.diagnosticar(pg, ya, FalsoRepositorio(), FalsoAnalitico(), FalsoModelo(),
                           redactor=falso_redactor(), ahora=AHORA)
    assert pendientes(pg, ["alta", "media"]) == [alta]


def _app(pg_url, cliente):
    return crear_app(FalsoRepositorio(), "/no/existe.json", pg_url=pg_url, analitico=FalsoAnalitico(),
                     cliente_modelo=cliente, **opciones())


@con_pg
def test_api_pedir_diagnostico(pg, monkeypatch):
    monkeypatch.setattr(hipotesis, "cargar_redactor", falso_redactor)
    id_ = _insertar(pg)
    api = con_sesion(_app(PG, FalsoModelo()), "operador")
    r = api.post(f"/api/incidentes/{id_}/diagnostico")
    assert r.status_code == 200, r.text
    assert set(r.json()) == {"texto", "evidencia_citada", "modelo", "tokens_entrada", "tokens_salida", "generado"}
    detalle = api.get(f"/api/incidentes/{id_}").json()
    assert detalle["estado"] == "diagnosticada" and detalle["diagnostico"]["texto"].startswith("Probablemente")
    assert con_sesion(_app(PG, FalsoModelo()), "seguridad").post(f"/api/incidentes/{id_}/diagnostico").status_code == 403
    assert api.post("/api/incidentes/999999/diagnostico").status_code == 404


@con_pg
def test_api_con_el_modelo_caido_el_incidente_se_ve_igual(pg, monkeypatch):
    monkeypatch.setattr(hipotesis, "cargar_redactor", falso_redactor)
    id_ = _insertar(pg)
    api = con_sesion(_app(PG, FalsoModelo(falla=True)), "operador")
    assert api.post(f"/api/incidentes/{id_}/diagnostico").status_code == 502
    detalle = api.get(f"/api/incidentes/{id_}").json()
    assert detalle["diagnostico"] is None and detalle["evidencia"]["valor_observado"] == pytest.approx(10.8e9)


def test_api_sin_modelo_configurado_es_503():
    api = con_sesion(crear_app(FalsoRepositorio(), "/no/existe.json", pg_url="postgresql://x@127.0.0.1:1/x",
                               **opciones()), "operador")
    r = api.post("/api/incidentes/1/diagnostico")
    assert r.status_code == 503 and "ZENIT_MODELO_DIAGNOSTICO" in r.json()["detail"]


def test_una_respuesta_cortada_es_un_error(monkeypatch):
    def urlopen(peticion, timeout):
        return io.BytesIO(json.dumps({"model": "m", "stop_reason": "max_tokens",
                                      "content": [{"type": "text", "text": "{\"hipotesis\": \"incompl"}]}).encode())

    monkeypatch.setattr(modelo.urllib.request, "urlopen", urlopen)
    with pytest.raises(ErrorModelo, match="cortada"):
        modelo.ClienteAnthropic("clave", "m").generar("s", "m")


def test_el_esfuerzo_se_valida(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("ZENIT_MODELO_DIAGNOSTICO", "claude-sonnet-5-5")
    monkeypatch.setenv("ZENIT_DIAGNOSTICO_ESFUERZO", "max")
    with pytest.raises(modelo.SinConfiguracion):
        modelo.ClienteAnthropic.desde_entorno()
    monkeypatch.setenv("ZENIT_DIAGNOSTICO_ESFUERZO", "medium")
    assert modelo.ClienteAnthropic.desde_entorno().esfuerzo == "medium"
