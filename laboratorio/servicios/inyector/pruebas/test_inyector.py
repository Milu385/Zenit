"""Pruebas del inyector. Corren sin Docker: la caida usa un cliente falso.

    cd laboratorio/servicios/inyector && python -m pytest pruebas/
"""
import json
import shutil
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from inyector import fallas
from inyector.campana import (Config, Ocupado, calendario, candado_del_nodo, ejecutar, estado_campana,
                              fabricar, recuperar)
from inyector.fallas import Caida, Cpu, Disco, ErrorFalla, Falla, Memoria, SinMargen
from inyector.verdad import Registro

INICIO = datetime(2026, 10, 6, 8, 0, tzinfo=timezone.utc)


@pytest.fixture
def cfg(tmp_path):
    return Config(nodo="zenit-nodo-prueba", ruta_verdad=tmp_path / "lab" / "verdad.jsonl",
                  carpeta_relleno=tmp_path / "relleno", calentamiento_h=0)


@pytest.fixture
def registro(cfg):
    return Registro(cfg.ruta_verdad, cfg.nodo)


def lineas(registro):
    return [json.loads(l) for l in registro.ruta.read_text().splitlines()]


# ------------------------------------------------------------------ verdad de referencia
def test_inicio_y_fin_con_el_mismo_uid(registro):
    uid = registro.abrir("cpu", "80%", {"duracion_s": 60})
    assert registro.abiertas()[0]["uid"] == uid
    registro.cerrar(uid, "completada")
    a, b = lineas(registro)
    assert a["evento"] == "inicio" and b["evento"] == "fin" and a["uid"] == b["uid"] == uid
    assert a["nodo"] == "zenit-nodo-prueba" and a["inicio"].endswith("Z")
    assert registro.abiertas() == []


def test_rechaza_tipos_y_estados_fuera_del_contrato(registro):
    with pytest.raises(ValueError):
        registro.abrir("terremoto", "alta", {})
    with pytest.raises(ValueError):
        registro.cerrar("x", "en_curso")


def test_una_linea_cortada_no_impide_leer_las_demas(registro):
    uid = registro.abrir("cpu", "80%", {})
    with registro.ruta.open("a") as f:
        f.write('{"uid": "cortada", "evento": "ini')  # apagado a mitad de una escritura
    assert [e["uid"] for e in registro.abiertas()] == [uid]


class Espia(Falla):
    """Fallo falso que anota en que orden pasan las cosas."""

    def __init__(self, registro, error=None, **kw):
        super().__init__(tipo="cpu", intensidad="50%", duracion_s=kw.pop("duracion_s", 0), **kw)
        self.registro, self.error, self.pasos = registro, error, []

    def iniciar(self):
        # la regla de H-024: cuando se inyecta, la linea de inicio ya esta en el disco
        self.pasos.append(("iniciar", len(self.registro.abiertas())))
        if self.error:
            raise self.error

    def restaurar(self):
        self.pasos.append(("restaurar", None))


def test_registra_antes_de_inyectar_y_cierra_al_terminar(registro):
    f = Espia(registro)
    uid, estado, motivo = ejecutar(f, registro, threading.Event(), avisar=lambda _: None)
    assert f.pasos == [("iniciar", 1), ("restaurar", None)]
    assert estado == "completada" and motivo is None
    assert lineas(registro)[-1] == {**lineas(registro)[-1], "uid": uid, "estado": "completada"}


def test_si_el_fallo_no_se_puede_provocar_queda_fallida_y_se_restaura(registro):
    f = Espia(registro, error=ErrorFalla("stress-ng no esta instalado"))
    _, estado, motivo = ejecutar(f, registro, threading.Event(), avisar=lambda _: None)
    assert estado == "fallida" and "stress-ng" in motivo
    assert ("restaurar", None) in f.pasos
    assert lineas(registro)[-1]["motivo"] == "stress-ng no esta instalado"


def test_un_error_inesperado_tambien_cierra_la_inyeccion(registro):
    f = Espia(registro, error=KeyError("x"))
    _, estado, motivo = ejecutar(f, registro, threading.Event(), avisar=lambda _: None)
    assert estado == "fallida" and "error inesperado" in motivo
    assert registro.abiertas() == []


def test_detener_a_mitad_deja_la_inyeccion_fallida(registro):
    parar = threading.Event()
    f = Espia(registro, duracion_s=30)
    threading.Timer(0.3, parar.set).start()
    t0 = time.monotonic()
    _, estado, motivo = ejecutar(f, registro, parar, avisar=lambda _: None)
    assert time.monotonic() - t0 < 5
    assert (estado, motivo) == ("fallida", "interrumpida")


def test_nunca_dos_inyecciones_a_la_vez(cfg):
    with candado_del_nodo(cfg.ruta_verdad, esperar=False):
        with pytest.raises(Ocupado):
            with candado_del_nodo(cfg.ruta_verdad, esperar=False):
                pass


# ------------------------------------------------------------------ fallos concretos
@pytest.mark.skipif(shutil.which("stress-ng") is None, reason="stress-ng no instalado")
def test_cpu_real_con_stress_ng(registro):
    f = Cpu(duracion_s=2, carga=30)
    _, estado, _ = ejecutar(f, registro, threading.Event(), avisar=lambda _: None)
    assert estado == "completada"
    assert f._proc.poll() is not None  # no queda stress-ng corriendo


def test_cpu_no_pasa_del_tope():
    assert Cpu(carga=100).carga == fallas.TOPE_CPU


def test_memoria_sin_margen_no_inyecta(registro):
    gib = 1024 ** 3
    f = Memoria(objetivo=0.80, leer=lambda: (4 * gib, int(0.22 * gib)))  # ya ocupada al 94 %
    _, estado, motivo = ejecutar(f, registro, threading.Event(), avisar=lambda _: None)
    assert estado == "fallida" and "ya ocupada" in motivo


def test_memoria_calcula_lo_que_falta_hasta_el_objetivo():
    gib = 1024 ** 3
    f = Memoria(objetivo=0.95, leer=lambda: (4 * gib, 3 * gib))  # ocupado 25 %, tope 85 %
    assert f.objetivo == fallas.TOPE_MEMORIA
    f.comando = lambda: ["true"]  # no ocupar memoria de verdad en la prueba
    f.duracion_s = 0
    try:
        f.iniciar()
    finally:
        f.restaurar()
    assert f.detalle["bytes"] == int(0.85 * 4 * gib - 1 * gib)
    assert f.detalle["ocupado_antes"] == 0.25


def _uso_falso(total, ocupado_inicial):
    def uso(carpeta):
        escrito = sum(p.stat().st_size for p in Path(carpeta).glob("relleno-*.bin"))
        return total, ocupado_inicial + escrito
    return uso


def test_disco_gradual_crece_por_pasos_y_se_borra(cfg, registro, monkeypatch):
    monkeypatch.setattr(fallas, "PASO_DISCO_S", 0.2)
    mib = 1024 * 1024
    tamanos = []
    f = Disco(carpeta=cfg.carpeta_relleno, objetivo=0.5, modo="gradual", duracion_s=2,
              uso=_uso_falso(100 * mib, 10 * mib), minimo=mib)
    original = f._crecer

    def crecer():
        original()
        tamanos.append(f._escrito)
    f._crecer = crecer
    _, estado, _ = ejecutar(f, registro, threading.Event(), avisar=lambda _: None)
    assert estado == "completada"
    assert len(tamanos) == 8 and tamanos == sorted(tamanos)   # 80 % de 2 s en pasos de 0,2 s
    assert tamanos[-1] == 40 * mib                            # hasta el 50 % de 100 MiB, desde 10 MiB
    assert list(cfg.carpeta_relleno.glob("relleno-*.bin")) == []


def test_disco_abrupto_llega_de_una_vez(cfg):
    mib = 1024 * 1024
    f = Disco(carpeta=cfg.carpeta_relleno, objetivo=0.5, modo="abrupto", duracion_s=0,
              uso=_uso_falso(100 * mib, 10 * mib), minimo=mib)
    f.iniciar()
    try:
        assert f._escrito == 40 * mib and f._pendiente == []
    finally:
        f.restaurar()


def test_disco_sin_margen(cfg):
    mib = 1024 * 1024
    f = Disco(carpeta=cfg.carpeta_relleno, objetivo=0.85, uso=lambda c: (100 * mib, 90 * mib), minimo=mib)
    with pytest.raises(SinMargen):
        f.iniciar()


class Contenedor:
    def __init__(self, nombre="zenit-laboratorio-catalogo-1"):
        self.name, self.status, self.attrs, self.acciones = nombre, "running", {"State": {}}, []

    def reload(self):
        pass

    def stop(self, timeout=10):
        self.acciones.append("stop")
        self.status = "exited"

    def start(self):
        self.acciones.append("start")
        self.status = "running"


class ClienteDocker:
    def __init__(self, contenedor):
        self.contenedor = contenedor
        self.containers = self
        self.filtros = None

    def list(self, all=True, filters=None):
        self.filtros = filters
        return [self.contenedor]


def test_caida_detiene_y_restablece_el_servicio(registro):
    c = Contenedor()
    cliente = ClienteDocker(c)
    f = Caida(servicio="catalogo", proyecto="zenit-laboratorio", cliente=cliente, duracion_s=0)
    _, estado, _ = ejecutar(f, registro, threading.Event(), avisar=lambda _: None)
    assert estado == "completada" and c.acciones == ["stop", "start"]
    assert "com.docker.compose.service=catalogo" in cliente.filtros["label"]
    assert "restablecido" in lineas(registro)[-1]["cierre"]  # H-028: el restablecimiento queda registrado


def test_caida_de_un_servicio_ya_detenido_no_cuenta(registro):
    c = Contenedor()
    c.status = "exited"
    f = Caida(servicio="catalogo", cliente=ClienteDocker(c), duracion_s=0)
    _, estado, motivo = ejecutar(f, registro, threading.Event(), avisar=lambda _: None)
    assert estado == "fallida" and "ya estaba detenido" in motivo


def test_caida_que_alguien_revierte_queda_fallida(registro):
    c = Contenedor()
    f = Caida(servicio="catalogo", cliente=ClienteDocker(c), duracion_s=5)
    threading.Timer(1.2, c.start).start()  # alguien lo levanta a mano a mitad
    _, estado, motivo = ejecutar(f, registro, threading.Event(), avisar=lambda _: None)
    assert estado == "fallida" and "antes de tiempo" in motivo


# ------------------------------------------------------------------ campana
def _primeros(cfg, n, inicio=INICIO):
    return [s for s, _ in zip(calendario(cfg, inicio, "c-prueba"), range(n))]


def test_calendario_reproducible_y_distinto_por_nodo(cfg):
    a, b = _primeros(cfg, 30), _primeros(cfg, 30)
    assert a == b
    otro = Config(**{**cfg.__dict__, "nodo": "zenit-nodo-otro"})
    assert [s["momento"] for s in _primeros(otro, 30)] != [s["momento"] for s in a]


def test_calendario_respeta_calentamiento_intervalos_y_normalidad(cfg):
    cfg.calentamiento_h = 12
    specs = _primeros(cfg, 200)
    assert specs[0]["momento"] == INICIO + timedelta(hours=12)
    for ant, sig in zip(specs, specs[1:]):
        hueco = sig["momento"] - ant["momento"]
        assert timedelta(minutes=cfg.intervalo_min) <= hueco
        fin_ant = ant["momento"] + timedelta(seconds=ant["duracion_s"])
        assert sig["momento"] - fin_ant >= timedelta(minutes=cfg.normal_min)
    tipos = {s["tipo"] for s in specs}
    assert tipos == set(cfg.tipos)
    for s in specs:  # cada especificacion se puede fabricar
        assert fabricar(s, cfg).tipo in ("cpu", "memoria", "disco", "caida")


def test_mirar_el_plan_no_arranca_la_campana(cfg):
    estado_campana(cfg, crear=False)
    assert not cfg.ruta_verdad.with_name("campana.json").exists()
    a = estado_campana(cfg)
    b = estado_campana(cfg, momento=INICIO + timedelta(days=3))
    assert a == b  # el reinicio del contenedor no mueve el calendario


def test_recuperar_cierra_lo_abierto_y_restaura_el_nodo(cfg, registro):
    c = Contenedor()
    c.status = "exited"  # el inyector murio con catalogo detenido
    registro.abrir("caida", "total", {"servicio": "catalogo"})
    registro.abrir("disco", "80%", {"modo": "abrupto"})
    cfg.carpeta_relleno.mkdir(parents=True)
    (cfg.carpeta_relleno / "relleno-1.bin").write_bytes(b"x" * 10)
    n = recuperar(registro, cfg, ClienteDocker(c), avisar=lambda _: None)
    assert n == 2 and registro.abiertas() == []
    fines = [e for e in lineas(registro) if e["evento"] == "fin"]
    assert all(e["estado"] == "fallida" and e["cierre"]["fin_estimado"] for e in fines)
    assert c.status == "running"
    assert list(cfg.carpeta_relleno.glob("*.bin")) == []
