"""H-031: las herramientas de medicion de P-REN-01 y P-REN-02."""
from datetime import datetime, timedelta, timezone

import falso_influx
from zenit import admin
from zenit.influx import ClienteInflux
from zenit.protocolo_linea import construir_linea

CANON = {"zenit_asset_id": "activo-do-nodo-01", "host_name": "zenit-nodo-do", "service_name": "sonda",
         "cloud_provider": "digitalocean", "cloud_region": "nyc3", "deployment_environment": "lab"}


def test_percentil():
    assert admin._percentil([1, 2, 3, 4, 5], 0.5) == 3
    assert admin._percentil(list(range(1, 101)), 0.95) == 95.05


def test_contar_filas_y_completitud(capsys):
    almacen, url, srv = falso_influx.levantar()
    try:
        for bd in ("zenit_raw", "zenit_interno"):
            almacen.bases[bd] = {"retencion": "30d", "tablas": {}}
        cliente = ClienteInflux(url, "apiv3_prueba")
        inicio = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=10)
        base = int(inicio.timestamp()) * 10**9
        cliente.escribir("zenit_raw", "\n".join(
            construir_linea("zenit_sonda_completitud", CANON, float(i), base + i * 1000) for i in range(999)))
        fin = inicio + timedelta(minutes=5)
        assert admin.contar(cliente, "zenit_sonda_completitud", "activo-do-nodo-01", inicio, fin, 1000) == 0
        assert "CUMPLE" in capsys.readouterr().out                     # 99,9 % justo
        assert admin.contar(cliente, "zenit_sonda_completitud", "activo-do-nodo-01", inicio, fin, 1001) == 1
        cliente.escribir("zenit_interno", "\n".join(
            construir_linea(f"zenit_ingesta_{n}", {"servicio": "ingesta"}, v, base + k * 10**10)
            for k, (n, v) in enumerate([("memoria_bytes", 100 * 2**20), ("memoria_bytes", 120 * 2**20),
                                        ("contrapresion", 3.0), ("contrapresion", 7.0)])))
        capsys.readouterr()
        admin.informe_ingesta(cliente, inicio, fin)
        salida = capsys.readouterr().out
        assert "120 MiB" in salida and "aumento  4" in salida
    finally:
        srv.shutdown()
