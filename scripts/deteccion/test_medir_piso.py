"""Pruebas de scripts/deteccion/medir_piso.py (medicion del piso, mad-v3).

    pytest scripts/deteccion/test_medir_piso.py

Construyen una exportacion pequena con la forma de `zenit.admin exportar`
(un Parquet por metrica, inyecciones.csv y manifiesto.json).
"""
import json
import sys
from datetime import timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import medir_piso as mp  # noqa: E402

T0 = pd.Timestamp("2026-10-26T00:00Z")
ACTIVO = "activo-azure-zenit-nodo-azure"
KB = 1024


def _serie(minutos, nivel, saltos, ruido=0):
    """Una serie a 10 s en `nivel`, con un vaiven de `ruido` minuto a minuto
    (para que la linea base tenga MAD) y escalones {minuto: delta}."""
    filas = []
    for m in range(minutos):
        valor = nivel + (m % 3) * ruido + sum(d for desde, d in saltos.items() if m >= desde)
        for s in range(0, 60, 10):
            filas.append((T0 + timedelta(minutes=m, seconds=s), valor))
    return filas


def _exportacion(tmp_path, swap, disco, inyecciones):
    carpeta = tmp_path / "export"
    (carpeta / "metricas").mkdir(parents=True)
    for tabla, agrupar, valor_grupo, filas in (
        ("system_paging_usage", "device", "/swapfile", swap),
        ("system_filesystem_usage", "mountpoint", "/", disco),
    ):
        df = pd.DataFrame(filas, columns=["time", "value"])
        df["zenit_asset_id"], df[agrupar], df["state"] = ACTIVO, valor_grupo, "used"
        libre = df.assign(state="free", value=1e9)      # la otra dimension no se debe promediar
        pd.concat([df, libre]).to_parquet(carpeta / "metricas" / f"{tabla}.parquet")
    with open(carpeta / "inyecciones.csv", "w") as f:
        f.write("id,uid,tipo,nodo,activo,inicio,fin,intensidad,parametros,estado,causa_en,manifiesta_en\n")
        for n, (uid, tipo, modo, ini, fin) in enumerate(inyecciones):
            parametros = json.dumps({"modo": modo}).replace('"', '""') if modo else "{}"
            f.write(f'{n},{uid},{tipo},nodo,{ACTIVO},{(T0 + timedelta(minutes=ini)).isoformat()},'
                    f'{(T0 + timedelta(minutes=fin)).isoformat()},,"{parametros}",completada,,\n')
    (carpeta / "metricas" / "manifiesto.json").write_text(json.dumps(
        {"desde": T0.isoformat(), "hasta": (T0 + timedelta(hours=6)).isoformat(), "activo": ACTIVO}))
    return carpeta


def _fila(filas, senal, piso):
    return next(f for f in filas if f["senal"] == senal and f["piso_mib"] == piso)


def test_el_piso_quita_un_escalon_de_kb_y_no_un_llenado_de_disco(tmp_path):
    # swap: +40 KB a los 120 min, sin inyeccion (ruido). disco: +5 GB a los
    # 200 min, dentro de una inyeccion de disco abrupta.
    swap = _serie(360, 25_000_000, {120: 40 * KB}, ruido=KB)
    disco = _serie(360, 7_000_000_000, {200: 5_000_000_000, 215: -5_000_000_000}, ruido=2**20)
    carpeta = _exportacion(tmp_path, swap, disco,
                           [("d1", "disco", "abrupto", 199, 215)])
    filas, _ = mp.medir(carpeta, mp.leer_inyecciones(carpeta / "inyecciones.csv"),
                        T0.to_pydatetime(), (T0 + timedelta(hours=6)).to_pydatetime())

    sin = _fila(filas, "system_paging_usage", 0)
    assert (sin["anomalias"], sin["ruido"]) == (1, 1)
    assert _fila(filas, "system_paging_usage", 8)["anomalias"] == 0

    for piso in (0, 64, 256):
        d = _fila(filas, "system_filesystem_usage", piso)
        assert (d["dentro"], d["ruido"], d["detectadas"]) == (1, 0, 1)
    assert _fila(filas, "system_filesystem_usage", 0)["alta_dentro"] == 1


def test_una_anomalia_abierta_bloquea_la_serie(tmp_path):
    # Dos escalones de swap que no vuelven: el segundo no abre otra anomalia
    # mientras la primera siga abierta, igual que la deduplicacion del detector.
    puntos = [(t.to_pydatetime(), v) for t, v in
              pd.DataFrame(_serie(360, 25_000_000, {120: 10_000 * KB, 240: 20_000 * KB}),
                           columns=["t", "v"]).set_index("t").v.resample("1min").mean().items()]
    desde, hasta = puntos[0][0], puntos[-1][0] + timedelta(minutes=1)
    con = mp.simular(puntos, 0, ACTIVO, "/swapfile", desde, hasta)
    sin = mp.simular(puntos, 0, ACTIVO, "/swapfile", desde, hasta, bloqueo=False)
    assert len(con) == 1 and con[0].fin is None
    assert len(sin) == 2
