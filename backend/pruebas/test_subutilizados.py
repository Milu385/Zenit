"""H-047: subutilizados (costo contra uso) y tendencia del gasto."""
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi.testclient import TestClient

from sesion_prueba import con_sesion, opciones
from zenit.api.main import crear_app
from zenit.gobernanza import subutilizados
from zenit.gobernanza.costos import Cargo, CostosMemoria, Factura
from zenit.influx import ErrorInflux


def _cargo(activo, monto, desde, proveedor="digitalocean"):
    return Cargo(proveedor, "Droplets", f"{activo or 'algo'} (s-1vcpu-1gb)", Decimal(str(monto)), "USD",
                 desde, None, activo, "" if activo else "no nombra ningun activo", "factura exportada")


HORAS = 48
OCIOSO = {"cpu": [0.03] * HORAS, "memoria": [0.20] * HORAS}
OCUPADO = {"cpu": [0.55] * HORAS, "memoria": [0.70] * HORAS}
PICOS = {"cpu": [0.02] * (HORAS - 1) + [0.85], "memoria": [0.20] * HORAS}


def test_marca_solo_al_que_cuesta_y_no_usa():
    cargos = [_cargo("ocioso", 6, date(2026, 9, 1)), _cargo("ocupado", 12, date(2026, 9, 1)),
              _cargo("picos", 6, date(2026, 9, 1))]
    r = subutilizados.analizar(cargos, {"ocioso": OCIOSO, "ocupado": OCUPADO, "picos": PICOS}, {},
                               date(2026, 9, 1), date(2026, 9, 30))
    por = {a["activo"]: a for a in r["activos"]}
    assert por["ocioso"]["subutilizado"] is True
    assert por["ocupado"]["subutilizado"] is False and "CPU promedio 55%" in por["ocupado"]["motivo"]
    assert por["picos"]["subutilizado"] is False and "pico de CPU 85%" in por["picos"]["motivo"]
    assert r["activos"][0]["activo"] == "ocioso"                       # los marcados primero
    assert r["criterio"] == subutilizados.criterio()


def test_sin_datos_suficientes_o_sin_costo_no_se_dice_nada():
    pocas = {"cpu": [0.01] * 5, "memoria": [0.1] * 5}
    cargos = [_cargo("nuevo", 6, date(2026, 9, 1))]
    r = subutilizados.analizar(cargos, {"nuevo": pocas, "gratis": OCIOSO}, {}, date(2026, 9, 1), date(2026, 9, 30))
    por = {a["activo"]: a for a in r["activos"]}
    assert por["nuevo"]["subutilizado"] is None and "5 h" in por["nuevo"]["motivo"]
    assert por["gratis"]["subutilizado"] is None and por["gratis"]["costo"] == 0
    sin_memoria = subutilizados.evaluar(6, [0.01] * HORAS, [])
    assert sin_memoria[0] is None


def test_tendencia_por_mes_por_entorno_y_por_activo():
    cargos = [_cargo("a", 6, date(2026, 8, 1)), _cargo("a", 7, date(2026, 9, 1)), _cargo("b", 10, date(2026, 9, 1)),
              _cargo(None, 1.5, date(2026, 9, 1))]
    r = subutilizados.analizar(cargos, {}, {"a": "aws", "b": "digitalocean"}, date(2026, 8, 1), date(2026, 9, 30))
    t = r["tendencia"]
    assert t["meses"] == ["2026-08", "2026-09"]
    entornos = {x["entorno"]: x["montos"] for x in t["por_entorno"]}
    assert entornos == {"aws": [6.0, 7.0], "digitalocean": [0.0, 10.0], "sin asignar": [0.0, 1.5]}
    assert [x["activo"] for x in t["por_activo"]] == ["a", "b"]            # el de mas gasto primero
    assert t["por_activo"][1]["montos"] == [0.0, 10.0]


def test_respeta_el_periodo():
    cargos = [_cargo("a", 6, date(2026, 8, 1)), _cargo("a", 7, date(2026, 9, 1))]
    r = subutilizados.analizar(cargos, {}, {}, date(2026, 9, 1), date(2026, 9, 30))
    assert r["tendencia"]["meses"] == ["2026-09"] and r["activos"][0]["costo"] == 7.0


# ------------------------------------------------------------------ API
class _Repo:
    def __init__(self, uso=None, caido=False):
        self.uso, self.caido, self.pedido = uso or {}, caido, None

    def disponible(self):
        return True

    def uso_por_hora(self, desde, hasta):
        if self.caido:
            raise ErrorInflux("no responde")
        self.pedido = (desde, hasta)
        return self.uso


def _app(repo):
    facturas = CostosMemoria()
    facturas.guardar(Factura("h", "digitalocean", datetime.now(timezone.utc), "daniel",
                             (_cargo("ocioso", 6, date(2026, 9, 1)),)))
    return crear_app(repo, "/no/existe.json", facturas=facturas, **opciones())


def test_api_con_permiso_de_costos():
    repo = _Repo({"ocioso": OCIOSO})
    api = con_sesion(_app(repo), "finanzas")
    r = api.get("/api/gobernanza/costos/analisis", params={"desde": "2026-09-01", "hasta": "2026-09-30"})
    assert r.status_code == 200, r.text
    assert r.json()["activos"][0]["subutilizado"] is True
    assert r.json()["uso"]["disponible"] is True
    assert repo.pedido[0] == datetime(2026, 9, 1, tzinfo=timezone.utc)
    assert con_sesion(_app(repo), "operador").get("/api/gobernanza/costos/analisis").status_code == 403
    assert TestClient(_app(repo)).get("/api/gobernanza/costos/analisis").status_code == 401
    assert api.get("/api/gobernanza/costos/analisis", params={"desde": "2026-10-01", "hasta": "2026-09-01"}).status_code == 422


def test_api_sin_motor_de_series_sigue_con_la_tendencia():
    api = con_sesion(_app(_Repo(caido=True)), "administrador")
    r = api.get("/api/gobernanza/costos/analisis").json()
    assert r["uso"]["disponible"] is False
    assert r["activos"][0]["subutilizado"] is None
    assert r["tendencia"]["meses"] == ["2026-09"]


def test_la_telemetria_huerfana_no_aparece_como_activo():
    """sin_resolver llega en el uso por hora (la telemetria huerfana), pero no es un activo."""
    cargos = [_cargo("a", 6, date(2026, 9, 1))]
    r = subutilizados.analizar(cargos, {"a": OCIOSO, "sin_resolver": OCIOSO}, {}, date(2026, 9, 1), date(2026, 9, 30))
    assert [x["activo"] for x in r["activos"]] == ["a"]
    assert all(x["activo"] != "sin_resolver" for x in r["tendencia"]["por_activo"])
