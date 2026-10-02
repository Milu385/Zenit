from datetime import datetime, timedelta, timezone

import falso_influx
from zenit import admin
from zenit.influx import ClienteInflux
from zenit.protocolo_linea import construir_linea

CANON = {
    "zenit_asset_id": "a1", "host_name": "h", "service_name": "host",
    "cloud_provider": "aws", "cloud_region": "us-east-1", "deployment_environment": "lab",
}


def _cliente():
    almacen, url, srv = falso_influx.levantar()
    return almacen, ClienteInflux(url, "apiv3_prueba"), srv


def test_crear_bases_es_idempotente(capsys):
    almacen, cliente, srv = _cliente()
    try:
        assert admin.crear_bases(cliente) == 0
        assert {b: almacen.bases[b]["retencion"] for b in almacen.bases} == {
            "zenit_raw": "7d", "zenit_1m": "30d", "zenit_1h": "180d"}
        almacen.bases["zenit_raw"]["retencion"] = "1d"
        assert admin.crear_bases(cliente) == 0      # segunda vez: no falla, corrige
        assert almacen.bases["zenit_raw"]["retencion"] == "7d"
        assert "existia" in capsys.readouterr().out
    finally:
        srv.shutdown()


def test_verificar_detecta_nulos_y_etiquetas_faltantes(capsys):
    almacen, cliente, srv = _cliente()
    try:
        admin.crear_bases(cliente)
        t = int(datetime.now(timezone.utc).timestamp()) * 10**9
        lineas = [
            construir_linea("buena", {**CANON, "cpu": "cpu0"}, 1.0, t),
            construir_linea("con_nulos", {**CANON, "device": "sda"}, 1.0, t),
            construir_linea("con_nulos", CANON, 1.0, t + 1),            # sin device: nulo
            construir_linea("incompleta", {"host_name": "h"}, 1.0, t),  # sin canonicas
        ]
        cliente.escribir("zenit_raw", "\n".join(lineas))
        assert admin.verificar(cliente) == 1
        salida = capsys.readouterr().out
        assert "OK    buena" in salida
        assert "aviso: dimensiones opcionales con nulos: device (1)" in salida
        assert "OK    con_nulos" in salida
        assert "faltan etiquetas canonicas" in salida
        assert "se llena con los resumenes de H-018" in salida
    finally:
        srv.shutdown()


def test_huecos_encuentra_el_corte(capsys):
    almacen, cliente, srv = _cliente()
    try:
        admin.crear_bases(cliente)
        ahora = datetime.now(timezone.utc).replace(microsecond=0)
        ahora -= timedelta(seconds=ahora.second % 10)
        lineas = []
        for paso in range(1, 91):  # 15 minutos hacia atras
            if 40 <= paso < 46:
                continue           # un minuto perdido
            t = int((ahora - timedelta(seconds=10 * paso)).timestamp()) * 10**9
            lineas.append(construir_linea("system_cpu_utilization", {**CANON, "state": "user"}, 0.1, t))
        cliente.escribir("zenit_raw", "\n".join(lineas))
        assert admin.huecos(cliente, "a1", 10, "system_cpu_utilization") == 1
        salida = capsys.readouterr().out
        assert "1 huecos de mas de 20 s" in salida and "hueco de 70 s" in salida
    finally:
        srv.shutdown()


def test_huecos_tolera_el_desfase_del_agente(capsys):
    almacen, cliente, srv = _cliente()
    try:
        admin.crear_bases(cliente)
        ahora = int(datetime.now(timezone.utc).timestamp())
        # cada 10 s, pero corrido 7 s y con 2 s de temblor
        lineas = [construir_linea("system_cpu_utilization", {**CANON, "state": "user"}, 0.1,
                                  (ahora - 10 * k - 7 + (k % 3)) * 10**9) for k in range(3, 80)]
        cliente.escribir("zenit_raw", "\n".join(lineas))
        assert admin.huecos(cliente, "a1", 10, "system_cpu_utilization") == 0
    finally:
        srv.shutdown()
