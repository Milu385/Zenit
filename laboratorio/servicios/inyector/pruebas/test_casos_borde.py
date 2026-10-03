"""Casos de borde de una campana que corre semanas sin nadie mirando."""
import json
import threading
from datetime import datetime, timedelta, timezone

import pytest

from inyector import campana, fallas
from inyector.campana import Config, ConfigInvalida, correr, ejecutar, estado_campana, recuperar
from inyector.fallas import Caida, SinMargen
from inyector.verdad import Registro
from test_inyector import ClienteDocker, Contenedor, Espia, lineas


@pytest.fixture
def cfg(tmp_path):
    return Config(nodo="zenit-nodo-prueba", ruta_verdad=tmp_path / "lab" / "verdad.jsonl",
                  carpeta_relleno=tmp_path / "relleno", calentamiento_h=0)


@pytest.fixture
def registro(cfg):
    return Registro(cfg.ruta_verdad, cfg.nodo)


def callar(_):
    pass


def test_una_linea_cortada_sin_salto_no_se_traga_la_siguiente(registro):
    registro.ruta.write_text('{"uid":"x","evento":"ini')  # apagado a mitad, sin salto de linea
    uid = registro.abrir("cpu", "80%", {})
    assert [e["uid"] for e in registro.abiertas()] == [uid]


def test_el_cierre_dice_que_paso_con_el_efecto(registro):
    ejecutar(Espia(registro), registro, threading.Event(), avisar=callar)
    completo = lineas(registro)[-1]["cierre"]
    assert completo["efecto"] == "completo" and completo["efecto_inicio"] <= completo["efecto_fin"]

    ejecutar(Espia(registro, error=SinMargen("lleno")), registro, threading.Event(), avisar=callar)
    assert lineas(registro)[-1]["cierre"] == {"efecto": "ninguno"}

    parar = threading.Event()
    threading.Timer(0.3, parar.set).start()
    ejecutar(Espia(registro, duracion_s=30), registro, parar, avisar=callar)
    assert lineas(registro)[-1]["cierre"]["efecto"] == "parcial"


def test_el_latido_da_el_fin_estimado_tras_un_apagado(cfg, registro):
    uid = registro.abrir("cpu", "80%", {})
    hace_una_hora = datetime.now(timezone.utc) - timedelta(hours=1)
    registro.ruta.with_name("latido.json").write_text(
        json.dumps({"uid": uid, "momento": hace_una_hora.isoformat().replace("+00:00", "Z")}))
    recuperar(registro, cfg, ClienteDocker(Contenedor()), avisar=callar)
    fin = datetime.fromisoformat(lineas(registro)[-1]["fin"].replace("Z", "+00:00"))
    assert abs((fin - hace_una_hora).total_seconds() - campana.LATIDO_S) < 1  # no la hora del reinicio
    assert not registro.ruta.with_name("latido.json").exists()


def test_el_latido_se_borra_al_terminar(registro):
    ejecutar(Espia(registro), registro, threading.Event(), avisar=callar)
    assert not registro.ruta.with_name("latido.json").exists()


def test_un_servicio_detenido_sin_inyeccion_abierta_se_levanta(cfg, registro):
    c = Contenedor()
    c.status = "exited"  # una restauracion fallida que ya quedo cerrada en el registro
    recuperar(registro, cfg, ClienteDocker(c), avisar=callar)
    assert c.status == "running"


def test_la_restauracion_de_una_caida_reintenta(registro, monkeypatch):
    monkeypatch.setattr(fallas.time, "sleep", lambda s: None)
    c = Contenedor()
    fallos = {"n": 2}
    original = c.start

    def start():
        if fallos["n"]:
            fallos["n"] -= 1
            raise RuntimeError("docker ocupado")
        original()
    c.start = start
    _, estado, _ = ejecutar(Caida(servicio="catalogo", cliente=ClienteDocker(c), duracion_s=0),
                            registro, threading.Event(), avisar=callar)
    assert estado == "completada" and c.status == "running"


def test_configuracion_invalida_se_explica():
    with pytest.raises(ConfigInvalida, match="latencia"):
        Config(nodo="n", tipos=("cpu", "latencia")).validar()
    with pytest.raises(ConfigInvalida, match="SERVICIOS_CAIDA"):
        Config(nodo="n", servicios_caida=()).validar()
    with pytest.raises(ConfigInvalida, match="INTERVALO"):
        Config(nodo="n", intervalo_min=90, intervalo_max=60).validar()


def test_la_campana_sigue_con_su_configuracion_aunque_cambie_el_entorno(cfg):
    est = estado_campana(cfg)
    otro = Config(**{**cfg.__dict__, "intervalo_min": 5, "intervalo_max": 6, "tipos": ("cpu",), "semilla": 99})
    plan = otro.con_guardados(est)
    assert (plan.intervalo_min, plan.tipos, plan.semilla_del_nodo()) == (60, cfg.tipos, cfg.semilla_del_nodo())


def test_campana_json_danado_no_detiene_al_inyector(cfg):
    ruta = cfg.ruta_verdad.with_name("campana.json")
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text('{"campana": "c-')
    est = estado_campana(cfg, avisar=callar)
    assert est["campana"].startswith("c-zenit-nodo-prueba")
    assert list(ruta.parent.glob("campana.json.danado-*"))


class RelojFalso:
    def __init__(self, inicio):
        self.ahora, self.hechas = inicio, []

    def __call__(self):
        return self.ahora


class ParadaFalsa(threading.Event):
    """parar.wait avanza el reloj simulado en vez de dormir."""

    def __init__(self, reloj):
        super().__init__()
        self.reloj = reloj

    def wait(self, timeout=None):
        if self.is_set():
            return True
        self.reloj.ahora += timedelta(seconds=timeout or 0)
        return False


def _campana_falsa(monkeypatch, inicio, reloj, cuantas, retraso_min):
    def ejecutar_falso(falla, registro, parar, avisar):
        reloj.hechas.append((reloj.ahora, falla.duracion_s))
        reloj.ahora += timedelta(seconds=falla.duracion_s, minutes=retraso_min)
        if len(reloj.hechas) >= cuantas:
            parar.set()
        return "uid", "completada", None

    monkeypatch.setattr(campana, "ejecutar", ejecutar_falso)
    monkeypatch.setattr(campana, "estado_campana",
                        lambda c, avisar=print, **k: {"campana": "c", "inicio": inicio.isoformat(), **c.guardables()})


def test_la_normalidad_se_cuenta_desde_el_fin_real(cfg, monkeypatch):
    inicio = datetime.now(timezone.utc)
    reloj = RelojFalso(inicio)
    _campana_falsa(monkeypatch, inicio, reloj, cuantas=15, retraso_min=25)  # cada una tarda 25 min de mas
    correr(cfg, ParadaFalsa(reloj), ClienteDocker(Contenedor()), avisar=callar, reloj=reloj)
    assert len(reloj.hechas) == 15
    for (a, dur), (b, _) in zip(reloj.hechas, reloj.hechas[1:]):
        fin_real = a + timedelta(seconds=dur, minutes=25)
        assert b - fin_real >= timedelta(minutes=cfg.normal_min)


def test_lo_que_cayo_con_el_nodo_apagado_no_se_inyecta_tarde(cfg, monkeypatch):
    inicio = datetime.now(timezone.utc) - timedelta(days=2)  # la campana empezo hace dos dias
    reloj = RelojFalso(datetime.now(timezone.utc))
    t0 = reloj.ahora
    _campana_falsa(monkeypatch, inicio, reloj, cuantas=1, retraso_min=0)
    correr(cfg, ParadaFalsa(reloj), ClienteDocker(Contenedor()), avisar=callar, reloj=reloj)
    assert reloj.hechas and reloj.hechas[0][0] >= t0  # la primera es futura, no una atrasada


def test_disco_y_caida_cierran_cuando_se_restauran_no_en_el_ultimo_latido(cfg, registro):
    uid = registro.abrir("disco", "80%", {"modo": "abrupto"})
    hace_una_hora = datetime.now(timezone.utc) - timedelta(hours=1)
    registro.ruta.with_name("latido.json").write_text(
        json.dumps({"uid": uid, "momento": hace_una_hora.isoformat().replace("+00:00", "Z")}))
    cfg.carpeta_relleno.mkdir(parents=True)
    (cfg.carpeta_relleno / "relleno-1.bin").write_bytes(b"x")  # el disco siguio lleno hasta ahora
    antes = datetime.now(timezone.utc)
    recuperar(registro, cfg, ClienteDocker(Contenedor()), avisar=callar)
    fin = datetime.fromisoformat(lineas(registro)[-1]["fin"].replace("Z", "+00:00"))
    assert fin >= antes - timedelta(seconds=1)


class ParadaConSuspension(ParadaFalsa):
    """La primera espera dura 5 horas, como una VM suspendida."""

    def __init__(self, reloj):
        super().__init__(reloj)
        self.primera = True

    def wait(self, timeout=None):
        if self.is_set():
            return True
        self.reloj.ahora += timedelta(hours=5) if self.primera else timedelta(seconds=timeout or 0)
        self.primera = False
        return False


def test_tras_una_suspension_no_se_inyecta_tarde(cfg, monkeypatch):
    inicio = datetime.now(timezone.utc)
    reloj = RelojFalso(inicio)
    cfg.calentamiento_h = 1
    _campana_falsa(monkeypatch, inicio, reloj, cuantas=1, retraso_min=0)
    planeadas = [s["momento"] for s, _ in zip(campana.calendario(cfg, inicio, "c"), range(10))]
    correr(cfg, ParadaConSuspension(reloj), ClienteDocker(Contenedor()), avisar=callar, reloj=reloj)
    hecha = reloj.hechas[0][0]
    assert hecha > inicio + timedelta(hours=5)                    # la que tocaba a la hora 1 se salto
    assert min(abs((hecha - p).total_seconds()) for p in planeadas) <= 60  # y la que se hizo fue a su hora
