"""H-042: version del modelo y consumo de tokens."""
import os
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from conftest import preparar_pg
from sesion_prueba import con_sesion, opciones
from zenit.api.main import crear_app
from zenit.diagnostico import SinConfiguracion, consumo, modelo_configurado
from zenit.gobernanza.permisos import permitido

PG = os.environ.get("ZENIT_PG_PRUEBAS")
con_pg = pytest.mark.skipif(not PG, reason="sin ZENIT_PG_PRUEBAS")


class _SinSeries:
    def disponible(self):
        return True


def _app(pg_url=None):
    return crear_app(_SinSeries(), "/no/existe.json", pg_url=pg_url, **opciones())


# ------------------------------------------------------------------ version del modelo
def test_modelo_configurado_exige_una_version_fija(monkeypatch):
    monkeypatch.setenv("ZENIT_MODELO_DIAGNOSTICO", "claude-sonnet-5-5")
    assert modelo_configurado() == "claude-sonnet-5-5"
    monkeypatch.setenv("ZENIT_MODELO_DIAGNOSTICO", "claude-sonnet-latest")
    with pytest.raises(SinConfiguracion, match="version fija"):
        modelo_configurado()
    monkeypatch.delenv("ZENIT_MODELO_DIAGNOSTICO")
    with pytest.raises(SinConfiguracion):
        modelo_configurado()


def test_la_configuracion_trae_una_version_fija():
    raiz = os.path.join(os.path.dirname(__file__), "..", "..", "deploy")
    env = open(os.path.join(raiz, ".env.example")).read()
    linea = [l for l in env.splitlines() if l.startswith("ZENIT_MODELO_DIAGNOSTICO=")][0]
    valor = linea.split("=", 1)[1].strip()
    assert valor and "latest" not in valor
    assert "ZENIT_MODELO_DIAGNOSTICO" in open(os.path.join(raiz, "compose.yml")).read()


# ------------------------------------------------------------------ resumen
def test_periodo_por_defecto_son_los_ultimos_30_dias():
    assert consumo.periodo(None, None, hoy=date(2026, 10, 22)) == (date(2026, 9, 23), date(2026, 10, 22))
    assert consumo.periodo(date(2026, 10, 1), None, hoy=date(2026, 10, 22)) == (date(2026, 10, 1), date(2026, 10, 22))
    with pytest.raises(ValueError):
        consumo.periodo(date(2026, 10, 5), date(2026, 10, 1))
    with pytest.raises(ValueError):
        consumo.periodo(date(2024, 1, 1), date(2026, 1, 1))


def test_resumen_por_dia_y_por_modelo():
    filas = [(date(2026, 10, 20), "claude-sonnet-5-5", 3, 5400, 700),
             (date(2026, 10, 21), "claude-haiku-5-5", 1, 1500, 180),
             (date(2026, 10, 21), "claude-sonnet-5-5", 2, 3600, 500)]
    r = consumo.resumir(filas, date(2026, 10, 1), date(2026, 10, 21), "claude-sonnet-5-5")
    assert r["modelo_configurado"] == "claude-sonnet-5-5"
    assert r["por_dia"][0] == {"dia": "2026-10-20", "modelo": "claude-sonnet-5-5", "diagnosticos": 3,
                               "tokens_entrada": 5400, "tokens_salida": 700}
    assert r["por_modelo"][0] == {"modelo": "claude-sonnet-5-5", "diagnosticos": 5, "tokens_entrada": 9000,
                                  "tokens_salida": 1200}
    assert consumo.resumir([], date(2026, 10, 1), date(2026, 10, 2), None)["por_modelo"] == []


# ------------------------------------------------------------------ API
def test_solo_el_administrador_ve_el_consumo(monkeypatch):
    monkeypatch.setenv("ZENIT_MODELO_DIAGNOSTICO", "claude-sonnet-5-5")
    assert permitido("administrador", "diagnostico:consumo")
    for rol in ("operador", "seguridad", "finanzas"):
        assert not permitido(rol, "diagnostico:consumo")
        assert con_sesion(_app(), rol).get("/api/diagnostico/consumo").status_code == 403
    assert TestClient(_app()).get("/api/diagnostico/consumo").status_code == 401
    r = con_sesion(_app(), "administrador").get("/api/diagnostico/consumo")
    assert r.status_code == 200
    assert r.json()["modelo_configurado"] == "claude-sonnet-5-5" and r.json()["por_dia"] == []


def test_fechas_al_reves_son_422_y_sin_modelo_configurado_es_null(monkeypatch):
    monkeypatch.delenv("ZENIT_MODELO_DIAGNOSTICO", raising=False)
    api = con_sesion(_app(), "administrador")
    assert api.get("/api/diagnostico/consumo", params={"desde": "2026-10-10", "hasta": "2026-10-01"}).status_code == 422
    assert api.get("/api/diagnostico/consumo").json()["modelo_configurado"] is None


# ------------------------------------------------------------------ con PostgreSQL
@con_pg
def test_consumo_desde_la_tabla_diagnosticos():
    c = preparar_pg(PG)
    try:
        with c.cursor() as cur:
            cur.execute("INSERT INTO anomalias (activo, metrica, inicio, severidad, puntaje, evidencia, version_modelo) "
                        "VALUES ('a', 'm', now(), 'alta', 1, %s, 'v') RETURNING id", (Jsonb({}),))
            aid = cur.fetchone()[0]
            for modelo, t_in, t_out, generado in [
                ("claude-sonnet-5-5", 1800, 240, datetime(2026, 10, 20, 23, 59, tzinfo=timezone.utc)),
                ("claude-sonnet-5-5", 1500, 200, datetime(2026, 10, 20, 1, 0, tzinfo=timezone.utc)),
                ("claude-haiku-5-5", 900, 100, datetime(2026, 10, 21, 0, 1, tzinfo=timezone.utc)),
                ("claude-sonnet-5-5", 1000, 100, datetime(2026, 9, 1, tzinfo=timezone.utc)),   # fuera del periodo
            ]:
                cur.execute("INSERT INTO diagnosticos (anomalia_id, texto, evidencia_citada, modelo, tokens_entrada, "
                            "tokens_salida, generado) VALUES (%s, 'h', %s, %s, %s, %s, %s)",
                            (aid, Jsonb([]), modelo, t_in, t_out, generado))
        c.commit()
    finally:
        c.close()
    r = con_sesion(_app(PG), "administrador").get("/api/diagnostico/consumo",
                                                  params={"desde": "2026-10-20", "hasta": "2026-10-21"}).json()
    assert r["por_dia"] == [
        {"dia": "2026-10-20", "modelo": "claude-sonnet-5-5", "diagnosticos": 2, "tokens_entrada": 3300, "tokens_salida": 440},
        {"dia": "2026-10-21", "modelo": "claude-haiku-5-5", "diagnosticos": 1, "tokens_entrada": 900, "tokens_salida": 100},
    ]
