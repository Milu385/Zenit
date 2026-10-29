"""Pruebas de scripts/deteccion/series_bloqueadas.py (H-029).

    pytest scripts/deteccion/test_series_bloqueadas.py
"""
import json
import sys
from datetime import timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import series_bloqueadas as sb  # noqa: E402
from zenit.deteccion.evaluacion import fecha  # noqa: E402

T0 = pd.Timestamp("2026-10-21T00:00Z")
LAB = "activo-azure-zenit-nodo-azure"


def _t(minutos):
    return (T0 + timedelta(minutes=minutos)).isoformat()


def _csv(ruta, columnas, filas):
    pd.DataFrame(filas, columns=columnas).to_csv(ruta, index=False)
    return ruta


def test_senal_de_reconoce_la_senal_por_sus_filtros():
    s = sb.senal_de("system_cpu_utilization", {"state": "wait"})
    assert s.fallas == ("disco",)
    assert sb.senal_de("system_memory_utilization", {"state": "used"}).fallas == ("memoria",)
    assert sb.senal_de("no_existe", {}) is None


def test_cuenta_las_inyecciones_de_su_tipo_mientras_estuvo_bloqueada(tmp_path):
    memoria = {"state": "used"}
    iny = _csv(tmp_path / "inyecciones.csv",
               ["id", "uid", "tipo", "nodo", "activo", "inicio", "fin", "intensidad", "parametros",
                "estado", "causa_en", "manifiesta_en"],
               [[1, "m-antes", "memoria", "n", LAB, _t(10), _t(16), "", "{}", "completada", "", ""],
                [2, "m-dentro", "memoria", "n", LAB, _t(200), _t(206), "", "{}", "completada", "", ""],
                [3, "c-dentro", "cpu", "n", LAB, _t(300), _t(306), "", "{}", "completada", "", ""],
                [4, "m-despues", "memoria", "n", LAB, _t(2000), _t(2006), "", "{}", "completada", "", ""]])
    columnas = ["id", "activo", "metrica", "dimensiones", "inicio", "fin", "estado", "severidad",
                "puntaje", "version_modelo", "marcada_por", "marcada_en", "detectada_en", "cierre_manual"]
    ano = _csv(tmp_path / "anomalias.csv", columnas, [
        # la que bloquea: memoria, abierta en el minuto 100, cerrada a mano en el 1000
        [110, LAB, "system_memory_utilization", json.dumps(memoria), _t(100), _t(1000), "cerrada",
         "baja", 6.0, "mad-v1 W=60 z=3.5 m=3", "", "", _t(100), "t"],
        # la cpu si se detecto: no es una perdida
        [111, LAB, "system_cpu_utilization", json.dumps({"state": "user"}), _t(301), _t(310), "cerrada",
         "baja", 5.0, "mad-v1 W=60 z=3.5 m=3", "", "", _t(301), "f"],
    ])
    abiertas = _csv(tmp_path / "abiertas.csv", ["id", "activo", "metrica", "dimensiones", "inicio"],
                    [[110, LAB, "system_memory_utilization", json.dumps(memoria), _t(100)]])

    df, r = sb.medir(None, abiertas, ano, iny, fecha(_t(0)), fecha(_t(3000)))
    f = df.iloc[0]
    assert f.fin_manual.startswith("2026-10-21T16:40")
    assert (f.inyecciones_en_el_bloqueo, f.del_tipo_de_la_senal, f.perdidas_en_h029) == (2, 1, 1)
    assert f.uids_perdidas == "m-dentro"
    assert pd.isna(f.habria_detectado_sin_bloqueo)          # sin exportacion de metricas
    assert "c-dentro" in r.por_inyeccion
