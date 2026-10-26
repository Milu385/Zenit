"""Mide el detector sobre el data.csv del laboratorio (punto 1.5, H-033).

    python scripts/deteccion/medir_csv.py Modelos/Clustering/data.csv

Corre `detectar_eventos` serie por serie (una columna del CSV es una serie),
a 1 minuto como en la plataforma, con varias configuraciones del metodo, y
cuenta cuantos eventos caen dentro de una inyeccion y cuantos son ruido.

Emparejamiento, el mismo de H-029: un evento corresponde a una inyeccion si
empieza entre el inicio de la inyeccion y su fin mas 5 minutos. Las
inyecciones salen de las columnas `ref_uid` y `ref_tipo_eval` del CSV (su
primer y su ultimo punto). Es una medicion por evento sobre el CSV de
DigitalOcean, no la evaluacion de H-029: sirve para comparar configuraciones
del metodo entre si, con los mismos datos.

Solo necesita pandas, ademas del paquete zenit.
"""
import pathlib
import sys
from datetime import timedelta

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "backend"))

from zenit.deteccion.detector import detectar_eventos
from zenit.deteccion.senales import PISO_FRACCION

# Columna del CSV -> piso absoluto de la senal que le corresponde en
# senales.py. `disco_used_frac` es fraccion en el CSV pero en la plataforma
# es `system_filesystem_usage`, en bytes, y no lleva piso absoluto: se mide
# como en la plataforma.
COLUMNAS = {
    "cpu_user": PISO_FRACCION,
    "cpu_system": PISO_FRACCION,
    "cpu_wait": PISO_FRACCION,
    "carga_1m": 0.0,
    "memoria_used": PISO_FRACCION,
    "disco_used_frac": 0.0,
    "disco_bytes_s": 0.0,
    "red_bytes_s": 0.0,
    "paginacion_s": 0.0,
    "procesos_running": 0.0,
    "tasa_llamadas_pedidos": 0.0,
    "tasa_llamadas_catalogo": 0.0,
    "tasa_errores_pedidos": 0.0,
}
MARGEN_FIN = timedelta(minutes=5)

CONFIGURACIONES = {
    # nombre: (historia_minima, piso_relativo, usar piso absoluto)
    "mad-v1 (historia 5, sin piso)": (5, 0.0, False),
    "Historia completa, sin piso": (None, 0.0, False),
    "Historia completa + piso absoluto (mad-v2)": (None, 0.0, True),
    "mad-v2 + piso relativo 0,5 %": (None, 0.005, True),
    "mad-v2 + piso relativo 1 %": (None, 0.01, True),
    "mad-v2 + piso relativo 2 %": (None, 0.02, True),
    "mad-v2 + piso relativo 5 %": (None, 0.05, True),
}


def inyecciones(df):
    marcadas = df.dropna(subset=["ref_uid", "ref_tipo_eval"])
    g = marcadas.groupby("ref_uid")
    return pd.DataFrame({"tipo": g.ref_tipo_eval.first(), "inicio": g.time.min(), "fin": g.time.max()})


def emparejar(momento, iny):
    dentro = iny[(iny.inicio <= momento) & (momento <= iny.fin + MARGEN_FIN)]
    return None if dentro.empty else dentro.index[0]


def medir(df, iny, historia_minima, piso_relativo, con_absoluto):
    filas = []
    for columna, piso_abs in COLUMNAS.items():
        serie = df.set_index("time")[columna].resample("1min").mean().dropna()
        puntos = [(t.to_pydatetime(), float(v)) for t, v in serie.items()]
        for e in detectar_eventos(puntos, historia_minima=historia_minima,
                                  piso_relativo=piso_relativo,
                                  piso_absoluto=piso_abs if con_absoluto else 0.0):
            filas.append({"columna": columna, "momento": e.momento, "puntaje": e.puntaje,
                          "severidad": e.severidad, "inyeccion": emparejar(pd.Timestamp(e.momento), iny)})
    return pd.DataFrame(filas)


def principal(ruta):
    df = pd.read_csv(ruta, parse_dates=["time"])
    iny = inyecciones(df)
    print(f"{len(iny)} inyecciones: {iny.tipo.value_counts().to_dict()}\n")
    print("| Configuracion | Eventos | Dentro | Ruido | Inyecciones detectadas | `alta` dentro |")
    print("|---|---|---|---|---|---|")
    detalle = {}
    for nombre, (hist, rel, absoluto) in CONFIGURACIONES.items():
        ev = medir(df, iny, hist, rel, absoluto)
        dentro = ev.inyeccion.notna().sum() if len(ev) else 0
        detectadas = ev.inyeccion.nunique() if len(ev) else 0
        alta = int(((ev.severidad == "alta") & ev.inyeccion.notna()).sum()) if len(ev) else 0
        print(f"| {nombre} | {len(ev)} | {dentro} | {len(ev) - dentro} | {detectadas} de {len(iny)} | {alta} |")
        detalle[nombre] = ev
    for nombre in list(CONFIGURACIONES)[:3:2]:
        ev = detalle[nombre]
        if len(ev):
            ruido = ev[ev.inyeccion.isna()]
            print(f"\nRuido por columna, {nombre}: {ruido.columna.value_counts().to_dict()}")
            perdidas = set(iny.index) - set(ev.inyeccion.dropna())
            print(f"Inyecciones sin detectar, {nombre}: "
                  f"{iny.loc[sorted(perdidas)].tipo.value_counts().to_dict() if perdidas else {}}")
    return detalle


if __name__ == "__main__":
    principal(sys.argv[1] if len(sys.argv) > 1 else "Modelos/Clustering/data.csv")
