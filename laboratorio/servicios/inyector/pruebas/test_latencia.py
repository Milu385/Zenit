"""H-027: latencia y perdida de paquetes en el enlace pedidos -> catalogo.

Corren sin Docker: el contenedor falso interpreta las ordenes ip y tc que el
inyector le manda por exec_run y guarda el estado de su interfaz.
"""
import json
import threading
from collections import namedtuple

import pytest

from inyector.campana import Config, ejecutar, fabricar, recuperar, sortear
from inyector.fallas import ErrorFalla, Latencia, restaurar_enlaces
from inyector.verdad import Registro

ExecResult = namedtuple("ExecResult", "exit_code output")
IP_CATALOGO = "172.20.0.5"


class Contenedor:
    def __init__(self, servicio, ip, redes=("zenit-laboratorio_default",)):
        self.name = f"zenit-laboratorio-{servicio}-1"
        self.servicio = servicio
        self.status = "running"
        self.attrs = {"NetworkSettings": {"Networks": {r: {"IPAddress": ip} for r in redes}}}
        self.qdisc: list[str] = []          # lo que hay en eth0
        self.ordenes: list[list[str]] = []
        self.falla_en = None                # una orden que responde con error

    def reload(self):
        pass

    def exec_run(self, orden):
        self.ordenes.append(orden)
        texto = " ".join(orden)
        if self.falla_en and self.falla_en in texto:
            return ExecResult(2, b"RTNETLINK answers: Operation not permitted")
        if orden[:4] == ["ip", "-o", "route", "get"]:
            return ExecResult(0, f"{orden[4]} dev eth0 src 172.20.0.4 uid 0 \\    cache".encode())
        if orden[:4] == ["ip", "-o", "link", "show"]:
            return ExecResult(0, b"1: lo: <LOOPBACK,UP> mtu 65536\n42: eth0@if43: <BROADCAST,UP> mtu 1500")
        if orden[:3] == ["tc", "qdisc", "show"]:
            return ExecResult(0, ("\n".join(self.qdisc) or "qdisc noqueue 0: root refcnt 2").encode())
        if orden[:3] == ["tc", "qdisc", "add"]:
            self.qdisc.append(texto)
            return ExecResult(0, b"")
        if orden[:3] == ["tc", "qdisc", "del"]:
            if not self.qdisc:
                return ExecResult(2, b"Error: Cannot delete qdisc with handle of zero.")
            self.qdisc = []
            return ExecResult(0, b"")
        if orden[:3] == ["tc", "filter", "add"]:
            self.qdisc.append(texto)
            return ExecResult(0, b"")
        return ExecResult(127, b"orden desconocida")

    @property
    def netem(self):
        return any("netem" in q for q in self.qdisc)


class ClienteDocker:
    def __init__(self, *contenedores):
        self.por_servicio = {c.servicio: c for c in contenedores}
        self.containers = self

    def list(self, all=True, filters=None):
        servicio = [f for f in filters["label"] if f.startswith("com.docker.compose.service=")][0].split("=")[1]
        return [self.por_servicio[servicio]] if servicio in self.por_servicio else []


@pytest.fixture
def enlace():
    pedidos, catalogo = Contenedor("pedidos", "172.20.0.4"), Contenedor("catalogo", IP_CATALOGO)
    return pedidos, catalogo, ClienteDocker(pedidos, catalogo)


@pytest.fixture
def registro(tmp_path):
    return Registro(tmp_path / "lab" / "verdad.jsonl", "zenit-nodo-prueba")


def lineas(registro):
    return [json.loads(l) for l in registro.ruta.read_text().splitlines()]


def _latencia(cliente, **kw):
    return Latencia(cliente=cliente, **{"duracion_s": 0, "retardo_ms": 400, "variacion_ms": 80, "perdida": 5, **kw})


def test_degrada_solo_el_trafico_hacia_catalogo_y_lo_restaura(enlace, registro):
    pedidos, catalogo, cliente = enlace
    f = _latencia(cliente)
    f.iniciar()
    assert pedidos.netem and not catalogo.ordenes            # se toca el origen, no el destino ni el nodo
    filtro = [q for q in pedidos.qdisc if "filter" in q][0]
    assert f"match ip dst {IP_CATALOGO}/32" in filtro and "flowid 1:4" in filtro
    netem = [q for q in pedidos.qdisc if "netem" in q][0]
    assert "delay 400ms 80ms distribution normal" in netem and "loss 5%" in netem
    f.restaurar()
    assert pedidos.qdisc == []
    f.restaurar()                                             # idempotente
    assert pedidos.qdisc == []


def test_la_verdad_dice_donde_esta_la_causa_y_donde_se_nota(enlace, registro):
    _, _, cliente = enlace
    _, estado, _ = ejecutar(_latencia(cliente), registro, threading.Event(), avisar=lambda _: None)
    inicio, fin = lineas(registro)
    assert estado == "completada"
    assert inicio["tipo"] == "latencia"
    assert (inicio["causa_en"], inicio["manifiesta_en"]) == ("catalogo", "pedidos")
    assert inicio["parametros"]["origen"] == "pedidos" and inicio["parametros"]["destino"] == "catalogo"
    assert inicio["intensidad"] == "400ms±80ms perdida 5%"
    assert fin["cierre"]["ip_destino"] == IP_CATALOGO and fin["cierre"]["efecto"] == "completo"


def test_restaura_el_enlace_aunque_se_interrumpa(enlace, registro):
    pedidos, _, cliente = enlace
    parar = threading.Event()
    parar.set()
    _, estado, motivo = ejecutar(_latencia(cliente, duracion_s=60), registro, parar, avisar=lambda _: None)
    assert estado == "fallida" and motivo == "interrumpida"
    assert pedidos.qdisc == []


def test_sin_permiso_de_red_queda_fallida_y_sin_restos(enlace, registro):
    pedidos, _, cliente = enlace
    pedidos.falla_en = "netem"                                # p. ej. falta cap_add NET_ADMIN
    _, estado, motivo = ejecutar(_latencia(cliente), registro, threading.Event(), avisar=lambda _: None)
    assert estado == "fallida" and "Operation not permitted" in motivo
    assert not pedidos.netem


def test_destino_detenido_o_en_otra_red_no_se_inyecta(enlace, registro):
    pedidos, catalogo, cliente = enlace
    catalogo.status = "exited"
    _, estado, motivo = ejecutar(_latencia(cliente), registro, threading.Event(), avisar=lambda _: None)
    assert estado == "fallida" and "no esta en marcha" in motivo
    aislado = Contenedor("catalogo", IP_CATALOGO, redes=("otra",))
    with pytest.raises(ErrorFalla, match="no comparten"):
        _latencia(ClienteDocker(pedidos, aislado)).iniciar()


def test_si_el_origen_se_reinicia_a_mitad_queda_fallida(enlace, registro, monkeypatch):
    from inyector import fallas

    pedidos, _, cliente = enlace
    monkeypatch.setattr(fallas, "REVISAR_ENLACE_S", 0)
    threading.Timer(1.2, lambda: pedidos.qdisc.clear()).start()  # un reinicio deja la red limpia
    _, estado, motivo = ejecutar(_latencia(cliente, duracion_s=5), registro, threading.Event(), avisar=lambda _: None)
    assert estado == "fallida" and "antes de tiempo" in motivo


def test_una_degradacion_que_quedo_de_antes_no_se_suma(enlace):
    pedidos, _, cliente = enlace
    pedidos.qdisc = ["tc qdisc add dev eth0 parent 1:4 handle 40: netem delay 999ms"]
    f = _latencia(cliente)
    f.iniciar()
    assert f.detalle["netem_previo_quitado"] is True
    assert not any("999ms" in q for q in pedidos.qdisc)


def test_topes(enlace):
    _, _, cliente = enlace
    f = _latencia(cliente, retardo_ms=10_000, variacion_ms=50_000, perdida=90)
    assert (f.retardo_ms, f.variacion_ms, f.perdida) == (2000, 2000, 30)
    with pytest.raises(ValueError):
        _latencia(cliente, origen="catalogo", destino="catalogo")


def test_recuperar_quita_la_latencia_que_dejo_un_inyector_caido(enlace, registro, tmp_path):
    pedidos, _, cliente = enlace
    cfg = Config(nodo="zenit-nodo-prueba", ruta_verdad=registro.ruta, carpeta_relleno=tmp_path / "relleno",
                 servicios_caida=())
    f = _latencia(cliente)
    registro.abrir(f.tipo, f.intensidad, f.parametros, causa_en=f.causa_en, manifiesta_en=f.manifiesta_en)
    f.iniciar()                                               # el inyector muere aqui, sin restaurar
    assert pedidos.netem
    assert recuperar(registro, cfg, cliente, avisar=lambda _: None) == 1
    assert not pedidos.netem
    assert lineas(registro)[-1]["estado"] == "fallida"
    assert restaurar_enlaces(cliente, "zenit-laboratorio", ["pedidos"]) == []


def test_la_campana_sortea_y_fabrica_latencias(enlace, tmp_path):
    import random

    _, _, cliente = enlace
    cfg = Config(nodo="n", ruta_verdad=tmp_path / "v.jsonl", tipos=("latencia",)).validar()
    for _ in range(20):
        spec = sortear(random.Random(_), "latencia", cfg)
        f = fabricar(spec, cfg, cliente)
        assert isinstance(f, Latencia) and (f.origen, f.destino) == ("pedidos", "catalogo")
        assert 0 < f.retardo_ms <= 2000 and 0 <= f.perdida <= 30
