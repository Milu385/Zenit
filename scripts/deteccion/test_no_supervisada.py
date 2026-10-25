"""Pruebas de scripts/deteccion/no_supervisada.py (H-034).

    pytest scripts/deteccion/test_no_supervisada.py

Necesitan pandas, numpy, scikit-learn y pyarrow, que no estan en las
dependencias del backend: H-034 corre fuera de la plataforma.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import no_supervisada as ns  # noqa: E402

T0 = pd.Timestamp("2026-10-25T00:00Z")
ACTIVO = "activo-azure-nodo-01"


def _normal(n, semilla=0):
    rng = np.random.default_rng(semilla)
    base = {"cpu_user": 0.15, "cpu_system": 0.03, "memoria_used": 0.2, "disco_used_frac": 0.15,
            "red_bytes_s": 20000.0, "tasa_llamadas_pedidos": 22.0, "tasa_llamadas_catalogo": 44.0}
    return pd.DataFrame({v: b * (1 + rng.normal(0, 0.03, n)) for v, b in base.items()})


def _X(df, activo=ACTIVO, inicio=T0):
    df = df.copy()
    df.index = pd.MultiIndex.from_arrays(
        [[activo] * len(df), pd.date_range(inicio, periods=len(df), freq="1min")],
        names=["activo", "time"])
    return df[ns.VARIABLES]


def _con_fallos(n=1440, semilla=0):
    """Un dia normal con fallos de varios tipos repartidos: lo que el
    entrenamiento necesita para formar grupos de fallo."""
    df = _normal(n, semilla)
    for inicio in range(120, n - 60, 180):
        tipo = (inicio // 180) % 4
        tramo = slice(inicio, inicio + 12)
        if tipo == 0:
            df.loc[tramo, "cpu_user"] = 0.9
        elif tipo == 1:
            df.loc[tramo, "memoria_used"] = 0.75
        elif tipo == 2:
            df.loc[tramo, "disco_used_frac"] = 0.8
        else:
            df.loc[tramo, ["tasa_llamadas_pedidos", "tasa_llamadas_catalogo", "red_bytes_s"]] = 0.0
    return df


def _correr(df_entrenar, df_detectar):
    X = pd.concat([_X(df_entrenar), _X(df_detectar, inicio=T0 + pd.Timedelta(minutes=len(df_entrenar)))])
    entrenar = (T0, T0 + pd.Timedelta(minutes=len(df_entrenar)))
    detectar = (entrenar[1], entrenar[1] + pd.Timedelta(minutes=len(df_detectar)))
    return ns.correr(X, entrenar, detectar)


def test_un_fallo_de_cpu_abre_una_sola_anomalia_con_su_variable():
    detectar = _normal(240, semilla=5)
    detectar.loc[150:165, "cpu_user"] = 0.9
    filas, modelo, version = _correr(_con_fallos(), detectar)

    assert len(filas) == 1
    f = filas[0]
    assert f["metrica"] == "cpu_user" and json.loads(f["dimensiones"])["direccion"] == "sube"
    inicio_fallo = T0 + pd.Timedelta(minutes=1440 + 150)
    assert inicio_fallo <= f["inicio"] <= inicio_fallo + pd.Timedelta(minutes=ns.PERSISTENCIA)
    assert f["fin"] == T0 + pd.Timedelta(minutes=1440 + 166)          # el primer minuto normal
    assert version.startswith("kmeans-v1 k=6 entrenado=2026-10-25/2026-10-26")


def test_una_caida_se_ve_como_baja():
    """Lo que la linea base no puede ver: las llamadas bajan en vez de subir."""
    detectar = _normal(240, semilla=6)
    detectar.loc[150:160, ["tasa_llamadas_pedidos", "tasa_llamadas_catalogo", "red_bytes_s"]] = 0.0
    filas, _, _ = _correr(_con_fallos(), detectar)

    assert len(filas) == 1
    assert json.loads(filas[0]["dimensiones"])["direccion"] == "baja"


def test_un_dia_normal_no_abre_nada():
    filas, modelo, _ = _correr(_con_fallos(), _normal(600, semilla=7))
    assert filas == []
    assert modelo.normales                       # hay al menos un grupo normal


def test_un_pico_aislado_no_abre_nada():
    detectar = _normal(240, semilla=8)
    detectar.loc[150, "cpu_user"] = 0.95         # un solo minuto: la persistencia lo descarta
    filas, _, _ = _correr(_con_fallos(), detectar)
    assert filas == []


def test_un_fallo_que_no_estaba_en_el_entrenamiento_se_ve_por_la_distancia():
    """KMeans asigna todo punto a algun grupo. Un fallo de un tipo que no
    aparecio al entrenar cae en el grupo normal mas cercano, y lo que lo
    delata es la distancia a ese grupo."""
    entrenar = _normal(1440, semilla=1)          # sin ningun fallo
    detectar = _normal(240, semilla=9)
    detectar.loc[150:165, "memoria_used"] = 0.8
    filas, modelo, _ = _correr(entrenar, detectar)

    assert len(modelo.normales) == modelo.k     # todos los grupos son normales
    assert len(filas) == 1 and filas[0]["metrica"] == "memoria_used"


def test_severidad_por_decadas_como_la_linea_base():
    assert ns.severidad(3.5) == "baja"
    assert ns.severidad(35.0) == "media"
    assert ns.severidad(350.0) == "alta"


def test_la_salida_tiene_las_columnas_de_anomalias(tmp_path):
    csv = tmp_path / "data.csv"
    df = pd.concat([_con_fallos(), _normal(240, semilla=3)], ignore_index=True)
    df.loc[1440 + 150:1440 + 165, "cpu_user"] = 0.9
    df.insert(0, "time", pd.date_range(T0, periods=len(df), freq="1min").strftime("%Y-%m-%dT%H:%M:%SZ"))
    df.insert(1, "activo", ACTIVO)
    df.to_csv(csv, index=False)
    salida = tmp_path / "kmeans.csv"

    assert ns.principal(["--csv", str(csv), "--entrenar", "2026-10-25T00:00Z", "2026-10-26T00:00Z",
                         "--detectar", "2026-10-26T00:00Z", "2026-10-26T04:00Z",
                         "--salida", str(salida)]) == 0
    out = pd.read_csv(salida)
    assert list(out.columns) == ns.COLUMNAS_SALIDA
    assert len(out) == 1 and out.loc[0, "estado"] == "detectada"


# ------------------------------------------------------ desde la exportacion
def _parquet(carpeta, tabla, filas):
    (carpeta / "metricas").mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(filas)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df.to_parquet(carpeta / "metricas" / f"{tabla}.parquet")


def test_variables_de_exportacion(tmp_path):
    """Tablas con la forma de `zenit.admin exportar`: una fila por punto, con
    etiquetas, dimensiones y `value`."""
    filas_cpu, filas_mem, filas_fs, filas_red, filas_calls = [], [], [], [], []
    for s in range(0, 180, 10):                              # 3 minutos a 10 s
        t = T0 + pd.Timedelta(seconds=s)
        comun = {"time": t, "zenit_asset_id": ACTIVO, "host_name": "h"}
        for cpu in ("0", "1"):
            filas_cpu += [{**comun, "cpu": cpu, "state": "user", "value": 0.2 if cpu == "0" else 0.4},
                          {**comun, "cpu": cpu, "state": "idle", "value": 0.6}]
        filas_mem += [{**comun, "state": "used", "value": 0.3}, {**comun, "state": "free", "value": 0.7}]
        for mp, used in (("/", 25.0), ("/boot", 99.0)):
            filas_fs += [{**comun, "mountpoint": mp, "state": "used", "value": used},
                         {**comun, "mountpoint": mp, "state": "free", "value": 75.0},
                         {**comun, "mountpoint": mp, "state": "reserved", "value": 0.0}]
        minuto = s // 60
        # contador: 60 B/s en eth0 por direccion, lo no cuenta
        for d in ("transmit", "receive"):
            filas_red += [{**comun, "device": "eth0", "direction": d, "value": 3600.0 * minuto},
                          {**comun, "device": "lo", "direction": d, "value": 1e9 * minuto}]
        filas_calls += [{**comun, "service_name": "pedidos", "status_code": "Unset", "value": 120.0 * minuto},
                        {**comun, "service_name": "catalogo", "status_code": "Unset", "value": 60.0 * minuto}]
    _parquet(tmp_path, "system_cpu_utilization", filas_cpu)
    _parquet(tmp_path, "system_memory_utilization", filas_mem)
    _parquet(tmp_path, "system_filesystem_usage", filas_fs)
    _parquet(tmp_path, "system_network_io", filas_red)
    _parquet(tmp_path, "traces_span_metrics_calls", filas_calls)

    X = ns.variables_de_exportacion(tmp_path)

    assert list(X.columns) == ns.VARIABLES
    fila = X.loc[(ACTIVO, T0 + pd.Timedelta(minutes=1))]
    assert fila["cpu_user"] == pytest.approx(0.3)           # promedio de los nucleos
    assert np.isnan(fila["cpu_system"])                     # no vino ese estado
    assert fila["memoria_used"] == pytest.approx(0.3)
    assert fila["disco_used_frac"] == pytest.approx(0.25)   # solo el montaje /
    assert fila["red_bytes_s"] == pytest.approx(120.0)      # 60 B/s x 2 direcciones, sin lo
    assert fila["tasa_llamadas_pedidos"] == pytest.approx(2.0)
    assert fila["tasa_llamadas_catalogo"] == pytest.approx(1.0)


def test_falta_una_tabla_no_rompe(tmp_path, capsys):
    _parquet(tmp_path, "system_memory_utilization",
             [{"time": T0, "zenit_asset_id": ACTIVO, "state": "used", "value": 0.3}])
    X = ns.variables_de_exportacion(tmp_path)
    assert X["memoria_used"].notna().sum() == 1
    assert "falta" in capsys.readouterr().err
