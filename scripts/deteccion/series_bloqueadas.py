"""Cuantifica las series que las anomalias de mad-v1 dejaron bloqueadas (H-029).

    python scripts/deteccion/series_bloqueadas.py <carpeta de la exportacion> \
        [--desde 2026-10-21T13:13Z] [--hasta 2026-10-26T14:01Z] [--salida bloqueo/]

Por que hace falta
------------------

Mientras una anomalia sigue abierta, el detector no abre otra en la misma
serie (`Detector._ya_registrada`). Con mad-v1 algunas no cerraban nunca: la
escala congelada al abrir era tan estrecha que la serie, ya recuperada,
seguia contando como critica (cierre.py, decision 5). Se cerraron a mano el
26 de octubre (`cierre_manual = t` en `anomalias.csv`; respaldo en
`abiertas-mad-v1-2026-10-26.csv`), y hasta entonces su serie no pudo
detectar nada. Eso baja la exhaustividad de mad-v1 por una razon que no es
el metodo de deteccion sino el de cierre, y el informe tiene que separarlo.

Que mide, por cada anomalia del respaldo
----------------------------------------

1. **Cuanto estuvo bloqueada:** de su `inicio` al `fin` del cierre manual.
2. **Inyecciones en su serie mientras tanto:** las `completada` del mismo
   activo que empezaron dentro del bloqueo. Se cuentan todas y, aparte, las
   del tipo que esa senal deberia ver (`Senal.fallas` en `senales.py`): una
   inyeccion de cpu no se pierde porque la serie de memoria este bloqueada.
3. **Cuales de esas se perdieron en H-029:** las que ninguna anomalia de
   mad-v1, de ninguna serie, detecto (`evaluacion.evaluar` en la ventana).
4. **Cuales habria visto la serie con el cierre arreglado** (si la
   exportacion trae sus metricas): la deteccion de mad-v1 (historia de 5
   puntos, sin piso) sobre la serie de la anomalia, leida igual que el
   repositorio (promedio por minuto de las filas con esas dimensiones,
   convertida a tasa si es acumulada), con el cierre de ahora (piso del 5 %,
   PR #26) y el mismo bloqueo mientras la anomalia nueva siga abierta
   (`medir_piso.simular`). Se empareja con la regla de H-029. No es "sin
   ningun bloqueo": cada anomalia nueva tambien bloquea su serie hasta que
   cierra, como en la plataforma. Es lo que la serie aportaba y el cierre
   roto le quito.

`fin` de las 14 anomalias cerradas a mano no es real (es la hora del cierre
manual): aqui solo se usa como el final del bloqueo, que es justamente lo
que fue. Su duracion no se usa para nada mas.
"""
import argparse
import json
import pathlib
import sys
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "backend"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from medir_piso import buscar, simular  # noqa: E402
from zenit.deteccion.evaluacion import (MARGEN_FIN, evaluar, fecha,  # noqa: E402
                                        leer_anomalias, leer_inyecciones)
from zenit.deteccion.senales import SENALES, preparar  # noqa: E402

HISTORIA_MAD_V1 = 5


def senal_de(metrica: str, dimensiones: dict):
    """La senal del catalogo que abrio esta anomalia: misma metrica y sus
    filtros contenidos en las dimensiones."""
    for s in SENALES:
        if s.metrica == metrica and all(dimensiones.get(k) == v for k, v in s.filtros.items()):
            return s
    return None


def serie_de(raiz: pathlib.Path, activo: str, metrica: str, dimensiones: dict, senal):
    """La serie de la anomalia a 1 minuto, como la lee el repositorio. None
    si la exportacion no la trae (otro activo, otra metrica)."""
    try:
        ruta = buscar(raiz, f"{metrica}.parquet")
    except SystemExit:
        return None
    df = pd.read_parquet(ruta)
    if "zenit_asset_id" not in df or not set(dimensiones) <= set(df.columns):
        return None
    df = df[df.zenit_asset_id == activo]
    for k, v in dimensiones.items():
        df = df[df[k].astype(str) == str(v)]
    if df.empty:
        return None
    s = df.set_index("time")["value"].sort_index().resample("1min").mean().dropna()
    puntos = [(t.to_pydatetime(), float(v)) for t, v in s.items()]
    return preparar(senal, puntos) if senal else puntos


def medir(raiz, abiertas_csv, anomalias_csv, inyecciones_csv, desde, hasta):
    inyecciones = leer_inyecciones(inyecciones_csv)
    completadas = [i for i in inyecciones if i.estado == "completada" and i.activo]
    anomalias = pd.read_csv(anomalias_csv)
    fin_manual = {int(r.id): fecha(r.fin) for r in anomalias.itertuples()
                  if str(getattr(r, "cierre_manual", "f")).lower() in ("t", "true", "1")}
    # que inyecciones detecto mad-v1 en H-029, con cualquier serie
    r = evaluar(inyecciones, leer_anomalias(anomalias_csv), desde, hasta, version="mad-v1")
    detectadas_h029 = set(r.por_inyeccion)
    evaluadas_h029 = {i.uid for i in r.inyecciones}

    filas = []
    for a in pd.read_csv(abiertas_csv).itertuples():
        dimensiones = json.loads(a.dimensiones) if isinstance(a.dimensiones, str) else {}
        inicio = fecha(a.inicio)
        fin = fin_manual.get(int(a.id))
        senal = senal_de(a.metrica, dimensiones)
        fallas = senal.fallas if senal else ()
        dentro = [i for i in completadas if i.activo == a.activo and fin and inicio < i.inicio <= fin]
        propias = [i for i in dentro if any(i.tipo.startswith(f) for f in fallas)]
        en_ventana = [i for i in propias if i.uid in evaluadas_h029]
        perdidas = [i for i in en_ventana if i.uid not in detectadas_h029]

        puntos = serie_de(raiz, a.activo, a.metrica, dimensiones, senal) if raiz else None
        habria = None
        if puntos is not None and fin is not None:
            abiertas = simular(puntos, 0.0, a.activo, "", inicio + pd.Timedelta(minutes=1), fin,
                               historia_minima=HISTORIA_MAD_V1)
            habria = [i for i in propias
                      if any(i.inicio <= x.inicio <= (i.fin or i.inicio) + MARGEN_FIN for x in abiertas)]
        filas.append({
            "id": int(a.id), "activo": a.activo, "metrica": a.metrica,
            "dimensiones": json.dumps(dimensiones, ensure_ascii=False, sort_keys=True),
            "fallas": ",".join(fallas) or "-",
            "inicio": inicio.isoformat(), "fin_manual": fin.isoformat() if fin else "",
            "dias_bloqueada": round((fin - inicio).total_seconds() / 86400, 2) if fin else None,
            "inyecciones_en_el_bloqueo": len(dentro),
            "del_tipo_de_la_senal": len(propias),
            "en_la_ventana_h029": len(en_ventana),
            "perdidas_en_h029": len(perdidas),
            "habria_detectado_sin_bloqueo": None if habria is None else len(habria),
            "habria_y_se_perdio": None if habria is None else len([i for i in habria if i in perdidas]),
            "uids_perdidas": " ".join(i.uid for i in perdidas),
            "uids_habria_y_se_perdio": "" if habria is None else " ".join(
                i.uid for i in habria if i in perdidas),
        })
    return pd.DataFrame(filas), r


def _pct(x):
    return f"{x:.1%}".replace(".", ",")


def informe(df: pd.DataFrame, r, desde, hasta) -> str:
    perdidas_union = set(" ".join(df.uids_perdidas).split())
    recuperables = set(" ".join(df.uids_habria_y_se_perdio.fillna("")).split())
    total = len(r.inyecciones)
    detectadas = len(r.por_inyeccion)
    lineas = [
        "# Series bloqueadas por anomalias de mad-v1 que no cerraban (H-029)", "",
        f"Ventana de mad-v1 en H-029: {desde:%Y-%m-%d %H:%M} a {hasta:%Y-%m-%d %H:%M} UTC. "
        f"Anomalias del respaldo: {len(df)}; con cierre manual: {df.fin_manual.astype(bool).sum()}.", "",
        "| Id | Activo | Serie | Debe ver | Bloqueada desde | Dias | Inyecciones en el bloqueo | Del tipo de la senal | En la ventana | Perdidas en H-029 | Con el cierre arreglado la serie habria visto |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for f in df.sort_values("inicio").itertuples():
        habria = "sin metricas" if pd.isna(f.habria_detectado_sin_bloqueo) else \
            f"{int(f.habria_detectado_sin_bloqueo)} (de las perdidas, {int(f.habria_y_se_perdio)})"
        dims = ", ".join(f"{k}={v}" for k, v in json.loads(f.dimensiones).items())
        lineas.append(
            f"| {f.id} | {f.activo.replace('activo-', '')} | `{f.metrica}` {dims} | {f.fallas} | "
            f"{f.inicio[:16].replace('T', ' ')} | {str(f.dias_bloqueada).replace('.', ',')} | "
            f"{f.inyecciones_en_el_bloqueo} | {f.del_tipo_de_la_senal} | {f.en_la_ventana_h029} | "
            f"{f.perdidas_en_h029} | {habria} |")
    sin_ellas = detectadas + len(recuperables)
    lineas += [
        "",
        f"- Inyecciones evaluadas por mad-v1: {total}; detectadas: {detectadas} ({_pct(detectadas / total)}).",
        f"- Inyecciones perdidas que cayeron en una serie bloqueada del tipo que esa serie debe ver: "
        f"{len(perdidas_union)} de {total - detectadas}.",
        f"- De esas, las que la propia serie habria detectado con el cierre arreglado (deteccion de "
        f"mad-v1 corrida fuera de linea sobre la exportacion): {len(recuperables)}. Con ellas la "
        f"exhaustividad de mad-v1 seria {sin_ellas} de {total} ({_pct(sin_ellas / total)}).",
    ]
    return "\n".join(lineas) + "\n"


def principal(argv=None):
    p = argparse.ArgumentParser(description="Series bloqueadas por anomalias de mad-v1 (H-029)")
    p.add_argument("exportacion", type=pathlib.Path)
    p.add_argument("--abiertas", type=pathlib.Path, help="abiertas-mad-v1-2026-10-26.csv")
    p.add_argument("--anomalias", type=pathlib.Path, help="anomalias.csv (con cierre_manual)")
    p.add_argument("--inyecciones", type=pathlib.Path)
    p.add_argument("--desde", type=fecha, default=fecha("2026-10-21T13:13Z"))
    p.add_argument("--hasta", type=fecha, required=True, help="hora del despliegue de mad-v2")
    p.add_argument("--salida", type=pathlib.Path)
    a = p.parse_args(argv)
    raiz = a.exportacion
    df, r = medir(raiz, a.abiertas or buscar(raiz, "abiertas-mad-v1-2026-10-26.csv"),
                  a.anomalias or buscar(raiz, "anomalias.csv"),
                  a.inyecciones or buscar(raiz, "inyecciones.csv"), a.desde, a.hasta)
    texto = informe(df, r, a.desde, a.hasta)
    print(texto)
    if a.salida:
        a.salida.mkdir(parents=True, exist_ok=True)
        (a.salida / "informe.md").write_text(texto)
        df.to_csv(a.salida / "series_bloqueadas.csv", index=False)


if __name__ == "__main__":
    principal()
