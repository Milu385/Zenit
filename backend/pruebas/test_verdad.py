"""Verdad de referencia del laboratorio (H-024) y exportacion para la evaluacion.

Las pruebas con PostgreSQL necesitan una base de pruebas vacia:

    ZENIT_PG_PRUEBAS=postgresql://zenit@127.0.0.1:55432/zenit_pruebas python -m pytest pruebas/test_verdad.py

Sin esa variable, esas pruebas se saltan y el resto corre igual.
"""
import csv
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import falso_influx
from zenit import admin, verdad
from zenit.influx import ClienteInflux
from zenit.protocolo_linea import construir_linea

ESQUEMA = Path(__file__).resolve().parents[2] / "deploy" / "postgres" / "01-esquema.sql"
PG = os.environ.get("ZENIT_PG_PRUEBAS")
con_pg = pytest.mark.skipif(not PG, reason="sin ZENIT_PG_PRUEBAS")

CANON = {"zenit_asset_id": "activo-do-nodo-01", "host_name": "zenit-nodo-do", "service_name": "desconocido",
         "cloud_provider": "digitalocean", "cloud_region": "nyc3", "deployment_environment": "lab"}


def _inicio(uid, tipo="cpu", inicio="2026-10-06T20:00:00.000Z", **extra):
    return {"uid": uid, "evento": "inicio", "tipo": tipo, "nodo": "zenit-nodo-do", "inicio": inicio,
            "intensidad": "80%", "parametros": {"duracion_s": 600, "campana": "c-1"}, **extra}


def _fin(uid, estado="completada", fin="2026-10-06T20:10:00.000Z", **extra):
    return {"uid": uid, "evento": "fin", "fin": fin, "estado": estado, **extra}


def _archivo_de_registros(carpeta: Path, eventos, nombre="registros-20261006.jsonl", etiquetas=CANON):
    """Como lo escribe la ingesta: etiquetas canonicas y el cuerpo del log como texto."""
    carpeta.mkdir(parents=True, exist_ok=True)
    with (carpeta / nombre).open("a") as f:
        for e in eventos:
            cuerpo = e if isinstance(e, str) else json.dumps(e)
            f.write(json.dumps({**etiquetas, "severidad": "", "cuerpo": cuerpo, "momento_ns": 0}) + "\n")


# ------------------------------------------------------------------ lectura y combinacion
def test_una_fila_por_inyeccion_sin_importar_el_orden_ni_los_duplicados(tmp_path):
    _archivo_de_registros(tmp_path, [
        _fin("a"),                       # el fin llego antes que el inicio
        _inicio("a"), _inicio("a"),      # y el inicio llego dos veces
        _inicio("b", tipo="disco"),      # sigue en curso
        "linea de log que no es de verdad",
        '{"evento": "inicio"}',          # sin uid
        "{json roto",
    ])
    filas, descartados = verdad.combinar(verdad.leer_eventos(tmp_path))
    por_uid = {f.uid: f for f in filas}
    assert set(por_uid) == {"a", "b"} and descartados == 0
    assert por_uid["a"].estado == "completada" and por_uid["a"].fin == datetime(2026, 10, 6, 20, 10, tzinfo=timezone.utc)
    assert por_uid["b"].estado == "en_curso" and por_uid["b"].fin is None
    assert por_uid["a"].activo == "activo-do-nodo-01"


def test_motivo_y_cierre_quedan_en_parametros(tmp_path):
    _archivo_de_registros(tmp_path, [
        _inicio("c", tipo="caida"),
        _fin("c", estado="fallida", motivo="interrumpida", cierre={"restablecido": "2026-10-06T20:10:05Z"}),
    ])
    (fila,), _ = verdad.combinar(verdad.leer_eventos(tmp_path))
    assert fila.estado == "fallida"
    assert fila.parametros["motivo"] == "interrumpida"
    assert fila.parametros["cierre"]["restablecido"] == "2026-10-06T20:10:05Z"
    assert fila.parametros["campana"] == "c-1"


def test_nodo_fuera_del_catalogo_queda_sin_activo(tmp_path):
    _archivo_de_registros(tmp_path, [_inicio("d")], etiquetas={**CANON, "zenit_asset_id": "sin_resolver"})
    (fila,), _ = verdad.combinar(verdad.leer_eventos(tmp_path))
    assert fila.activo is None and fila.nodo == "zenit-nodo-do"


def test_eventos_invalidos_se_descartan_sin_detener_la_carga(tmp_path):
    _archivo_de_registros(tmp_path, [
        _inicio("malo", tipo="terremoto"),
        _inicio("sin-zona", inicio="2026-10-06T20:00:00"),
        _inicio("bueno"),
    ])
    filas, descartados = verdad.combinar(verdad.leer_eventos(tmp_path))
    assert [f.uid for f in filas] == ["bueno"] and descartados == 2


def test_lee_todos_los_dias(tmp_path):
    _archivo_de_registros(tmp_path, [_inicio("e")], nombre="registros-20261006.jsonl")
    _archivo_de_registros(tmp_path, [_fin("e")], nombre="registros-20261007.jsonl")
    _archivo_de_registros(tmp_path, [_inicio("x")], nombre="trazas-20261006.jsonl")  # otra senal: no se lee
    filas, _ = verdad.combinar(verdad.leer_eventos(tmp_path))
    assert [(f.uid, f.estado) for f in filas] == [("e", "completada")]


# ------------------------------------------------------------------ PostgreSQL
@pytest.fixture
def pg():
    import psycopg

    conexion = psycopg.connect(PG)
    with conexion.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        cur.execute(ESQUEMA.read_text())
    conexion.commit()
    yield conexion
    conexion.close()


@con_pg
def test_carga_idempotente_y_actualiza_el_cierre(tmp_path, pg):
    _archivo_de_registros(tmp_path, [_inicio("a"), _inicio("b")])
    assert verdad.pasada(pg, tmp_path) == (2, 2, 0)
    assert verdad.pasada(pg, tmp_path) == (2, 0, 0)      # releer no cambia nada
    _archivo_de_registros(tmp_path, [_fin("a")], nombre="registros-20261007.jsonl")
    assert verdad.pasada(pg, tmp_path) == (2, 1, 0)      # solo cambia la que se cerro
    with pg.cursor() as cur:
        cur.execute("SELECT uid, estado, fin IS NOT NULL, parametros->>'campana' FROM inyecciones ORDER BY uid")
        assert cur.fetchall() == [("a", "completada", True, "c-1"), ("b", "en_curso", False, "c-1")]


@con_pg
def test_un_estado_final_no_vuelve_a_en_curso(tmp_path, pg):
    _archivo_de_registros(tmp_path, [_inicio("a"), _fin("a")])
    verdad.pasada(pg, tmp_path)
    otra = tmp_path / "solo-inicio"
    _archivo_de_registros(otra, [_inicio("a")])  # p. ej. si se borraran los archivos viejos de la ingesta
    verdad.pasada(pg, otra)
    with pg.cursor() as cur:
        cur.execute("SELECT estado, fin IS NOT NULL FROM inyecciones WHERE uid = 'a'")
        assert cur.fetchone() == ("completada", True)


@con_pg
def test_el_esquema_rechaza_valores_fuera_del_contrato(pg):
    import psycopg

    with pytest.raises(psycopg.errors.CheckViolation):
        with pg.cursor() as cur:
            cur.execute("INSERT INTO inyecciones (uid, tipo, nodo, inicio, intensidad, estado) "
                        "VALUES ('z', 'cpu', 'n', now(), '1', 'terminada')")
    pg.rollback()


# ------------------------------------------------------------------ exportacion
@con_pg
def test_exportar_metricas_inyecciones_y_huecos(tmp_path, pg):
    import pyarrow.parquet as pq

    almacen, url, srv = falso_influx.levantar()
    try:
        cliente = ClienteInflux(url, "apiv3_prueba")
        admin.crear_bases(cliente)
        desde = datetime(2026, 10, 6, 20, 0, tzinfo=timezone.utc)
        t0 = int(desde.timestamp()) * 10**9
        lineas = []
        for i in range(30):  # 5 minutos a 10 s, con un hueco de 60 s entre la muestra 12 y la 18
            if 12 <= i < 18:
                continue
            for estado, v in (("user", 0.2 + i / 100), ("idle", 0.7)):
                lineas.append(construir_linea("system_cpu_utilization",
                                              {**CANON, "cpu": "cpu0", "state": estado}, v, t0 + i * 10 * 10**9))
            lineas.append(construir_linea("system_memory_usage", {**CANON}, 1e9, t0 + i * 10 * 10**9))  # sin dimension
        cliente.escribir("zenit_raw", "\n".join(lineas))
        _archivo_de_registros(tmp_path / "salida", [
            _inicio("a"), _fin("a"),
            _inicio("viejo", inicio="2026-10-01T00:00:00Z"), _fin("viejo", fin="2026-10-01T00:10:00Z"),
            _inicio("abierta", inicio="2026-10-06T19:00:00Z"),  # empezo antes del rango y sigue en curso
        ])
        verdad.pasada(pg, tmp_path / "salida")
        catalogo = tmp_path / "catalogo.json"
        catalogo.write_text(json.dumps({"zenit-nodo-do": "activo-do-nodo-01"}))

        salida = tmp_path / "exp"
        assert admin.exportar(cliente, desde, desde + timedelta(minutes=5), salida,
                              ruta_catalogo=str(catalogo), conexion=pg) == 0

        cpu = pq.read_table(salida / "metricas" / "system_cpu_utilization.parquet")
        assert cpu.num_rows == 48
        assert str(cpu.schema.field("time").type) == "timestamp[ns, tz=UTC]"
        assert cpu.column("time")[0].as_py() == desde
        assert {"zenit_asset_id", "cpu", "state", "value"} <= set(cpu.column_names)
        assert cpu.schema.field("value").type == "double"
        mem = pq.read_table(salida / "metricas" / "system_memory_usage.parquet")
        assert mem.num_rows == 24 and "state" not in mem.column_names

        with open(salida / "inyecciones.csv") as f:
            iny = list(csv.DictReader(f))
        assert [r["uid"] for r in iny] == ["abierta", "a"]          # la del 1 de octubre queda fuera
        assert json.loads(iny[0]["parametros"])["campana"] == "c-1"
        with open(salida / "huecos.csv") as f:
            huecos = list(csv.DictReader(f))
        assert len(huecos) == 1 and huecos[0]["activo"] == "activo-do-nodo-01" and int(huecos[0]["segundos"]) == 70
        manifiesto = json.loads((salida / "manifiesto.json").read_text())
        assert manifiesto["tablas"] == {"system_cpu_utilization": 48, "system_memory_usage": 24}
        assert manifiesto["inyecciones"] == 2
    finally:
        srv.shutdown()


def test_exportar_acepta_fechas_con_zona_y_las_pasa_a_utc():
    assert admin._fecha_arg("2026-10-06T00:00-05:00") == datetime(2026, 10, 6, 5, 0, tzinfo=timezone.utc)
    assert admin._fecha_arg("2026-10-06T00:00Z") == datetime(2026, 10, 6, 0, 0, tzinfo=timezone.utc)
    assert list(admin._dias(datetime(2026, 10, 6, tzinfo=timezone.utc), datetime(2026, 10, 8, 12, tzinfo=timezone.utc)))[-1] == (
        datetime(2026, 10, 8, tzinfo=timezone.utc), datetime(2026, 10, 8, 12, tzinfo=timezone.utc))


def test_leer_el_archivo_crudo_de_un_nodo(tmp_path):
    crudo = tmp_path / "verdad.jsonl"
    crudo.write_text("\n".join(json.dumps(e) for e in [_inicio("r"), _fin("r")]) + "\n{cortada")
    (fila,), _ = verdad.combinar(verdad.leer_crudo(crudo, "activo-do-nodo-01"))
    assert (fila.uid, fila.estado, fila.activo) == ("r", "completada", "activo-do-nodo-01")


@con_pg
def test_releer_sin_el_fin_no_borra_motivo_ni_cierre(tmp_path, pg):
    _archivo_de_registros(tmp_path, [_inicio("a"), _fin("a", estado="fallida", motivo="interrumpida")])
    verdad.pasada(pg, tmp_path)
    solo = tmp_path / "solo"
    _archivo_de_registros(solo, [_inicio("a")])
    verdad.pasada(pg, solo)
    with pg.cursor() as cur:
        cur.execute("SELECT estado, parametros->>'motivo' FROM inyecciones WHERE uid = 'a'")
        assert cur.fetchone() == ("fallida", "interrumpida")
