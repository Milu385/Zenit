"""Deteccion no supervisada con KMeans, por lotes (H-034).

    python scripts/deteccion/no_supervisada.py --csv Modelos/Clustering/data.csv \
        --entrenar 2026-10-06T00:00Z 2026-10-07T00:00Z \
        --detectar 2026-10-07T00:00Z 2026-10-08T00:00Z --salida kmeans.csv

    python scripts/deteccion/no_supervisada.py --exportacion exportaciones/azure \
        --entrenar 2026-10-16T00:00Z <fecha de los arreglos> \
        --detectar <fecha de los arreglos> <fecha de corte> --salida kmeans.csv

No es un servicio ni escribe en la plataforma: lee el CSV del laboratorio o
la exportacion de metricas (`python -m zenit.admin exportar`) y produce un
CSV con las columnas de la tabla `anomalias`. Ese archivo se evalua con el
mismo procedimiento que la linea base (`python -m zenit.deteccion.evaluacion`),
asi que la comparacion es con la misma regla de emparejamiento, la misma
ventana y las mismas inyecciones. No va a la tabla `anomalias`: apareceria
como incidentes en la interfaz.

El metodo
---------

Es el del cuaderno `Modelos/Clustering/clustering.ipynb`, con lo minimo para
que de anomalias con inicio y fin en vez de un perfil por fila:

1. **Variables.** Las siete del cuaderno: `cpu_user`, `cpu_system`,
   `memoria_used`, `disco_used_frac`, `red_bytes_s`, `tasa_llamadas_pedidos`
   y `tasa_llamadas_catalogo`, a 1 minuto (como la linea base, para que la
   persistencia y los tiempos signifiquen lo mismo).
2. **Normalizacion por activo, contra su propia ultima hora.** Las tasas
   pasan por `log1p`. Para cada minuto, z robusto contra la mediana y el MAD
   de los 60 minutos anteriores (solo pasado), con un piso del MAD de 0,1
   desviaciones estandar de la ventana de entrenamiento, y recortado a +-20
   para el agrupamiento. A diferencia de la linea base, el z tiene **signo**:
   una caida (las llamadas bajan) es un z negativo, y es lo que este metodo
   puede ver y la linea base no.
3. **KMeans** con `k` grupos (6, el del cuaderno) sobre los minutos de la
   ventana de entrenamiento, de todos los activos juntos (cada uno ya esta
   normalizado contra si mismo). Se entrena una vez y no se reentrena
   durante la deteccion (H-036).
4. **Que grupos son normales.** Uno cuyo centroide no se aparta en ninguna
   variable mas que el corte critico de la linea base (|z| < 3,5). No se
   nombran a mano como en el cuaderno: con otros datos los numeros de grupo
   cambian.
5. **Un minuto es anomalo** si cae en un grupo no normal, o si cae en uno
   normal pero mas lejos de su centroide que el percentil 99,5 de las
   distancias de entrenamiento a los grupos normales. Lo segundo hace falta
   porque KMeans asigna todo punto a algun grupo: un fallo de un tipo que no
   aparecio en el entrenamiento cae en el grupo normal mas cercano.
6. **Anomalias**, con las mismas reglas de la linea base: se abre con 3
   minutos anomalos seguidos (persistencia) y se cierra con 15 normales
   seguidos; el `fin` es el primer minuto normal. `metrica` es la variable
   mas desviada en el minuto de apertura y `dimensiones` dice en que grupo y
   en que sentido (`sube` o `baja`).
7. **Puntaje y severidad.** El puntaje es el |z| sin recortar de esa
   variable en la apertura, y la severidad sale de las mismas decadas de la
   linea base (z / 3,5: baja, media desde 10, alta desde 100).

La version dice con que se entreno: `kmeans-v1 k=6 entrenado=AAAA-MM-DD/AAAA-MM-DD`.

Medido en data.csv
------------------

Entrenado con el 6 de octubre y detectando el 7 (13 inyecciones), los dos
evaluados con `python -m zenit.deteccion.evaluacion` en la misma ventana. La
linea base es `mad-v2` corrida sobre las 13 columnas del CSV
(`scripts/deteccion/medir_csv.py`):

| Metodo | Detectadas | Falsos positivos | Precision | F1 | Retardo medio | Disco gradual |
|---|---|---|---|---|---|---|
| Linea base (mad-v2) | 13 de 13 | 3 (`carga_1m`) | 88,0 % | 93,6 % | 1,8 min | 1,8 min |
| KMeans (kmeans-v1 k=6) | 13 de 13 | 0 | 100 % | 100 % | 2,7 min | 4,3 min |

Como leerlo, sin venderlo de mas:

- **Es optimista para KMeans.** Entrena y detecta en el mismo nodo, con la
  misma campana y los mismos tipos de fallo: los grupos de fallo que aprende
  el 6 son exactamente los que aparecen el 7. Ademas `k=6` se eligio en el
  cuaderno mirando los dos dias. La medicion que vale es la de Azure, con
  otros nodos y entrenando antes de la ventana de evaluacion.
- **Dos dias no alcanzan para separar los metodos**: 13 inyecciones, y los
  dos las detectan todas. La diferencia esta en los 3 falsos positivos de
  carga, que KMeans no ve porque no usa esa variable.
- **KMeans tarda mas en el disco gradual** (4,3 contra 1,8 min): un llenado
  que empieza despacio tarda en alejarse lo suficiente del grupo normal.
- **Las caidas.** En este CSV la linea base tambien las detecta, porque al
  caer un servicio otras senales suben (la carga, por ejemplo). KMeans las
  ve directamente, como bajada de las llamadas (`direccion: baja`).

De la exportacion a las siete variables
---------------------------------------

`data.csv` ya traia las columnas. La exportacion trae una tabla Parquet por
metrica, con las etiquetas y dimensiones de cada punto y la columna `value`.
`variables_de_exportacion` las arma asi, siguiendo `senales.py`:

| Variable | Tabla | Como |
|---|---|---|
| cpu_user, cpu_system | system_cpu_utilization | `state` = user / system, promedio de los nucleos |
| memoria_used | system_memory_utilization | `state` = used |
| disco_used_frac | system_filesystem_usage | used / (used + free + reserved), montaje `/` |
| red_bytes_s | system_network_io | tasa por segundo, suma de interfaces sin `lo` y de las dos direcciones |
| tasa_llamadas_* | traces_span_metrics_calls | tasa por segundo, `service_name` que contiene `pedido` / `catalogo` |

Esta parte no se pudo probar contra una exportacion real de Azure (no
estaba al escribirla): la prueba usa tablas construidas a mano con la forma
que describe `zenit.admin.exportar`. Lo primero al correrla con la de Azure
es mirar el resumen que imprime (minutos y nulos por variable).
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans

VARIABLES = ["cpu_user", "cpu_system", "memoria_used", "disco_used_frac",
             "red_bytes_s", "tasa_llamadas_pedidos", "tasa_llamadas_catalogo"]
TASAS = ["red_bytes_s", "tasa_llamadas_pedidos", "tasa_llamadas_catalogo"]

K_DEFECTO = 6               # el del cuaderno de clustering (codo y silueta)
VENTANA = 60                # minutos de historia para la normalizacion
Z_CRITICO = 3.5             # el de la linea base: separa grupos normales
Z_RECORTE = 20.0            # recorte para el agrupamiento, como el cuaderno
FRACCION_PISO = 0.1         # piso del MAD: 0,1 desviaciones estandar del entrenamiento
PERCENTIL_DISTANCIA = 99.5  # distancia maxima a un grupo normal
PERSISTENCIA = 3
NORMALIDAD_PARA_CERRAR = 15
EXCESO_MEDIA, EXCESO_ALTA = 10, 100
SEMILLA = 42


# --------------------------------------------------------------- variables
def variables_de_csv(ruta) -> pd.DataFrame:
    """data.csv del laboratorio -> (activo, minuto) x VARIABLES."""
    df = pd.read_csv(ruta, parse_dates=["time"])
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return (df.set_index("time").groupby("activo")[VARIABLES]
            .resample("1min").mean())


def _leer(carpeta: Path, tabla: str) -> pd.DataFrame:
    ruta = carpeta / "metricas" / f"{tabla}.parquet"
    if not ruta.exists():
        print(f"aviso: falta {ruta}", file=sys.stderr)
        return pd.DataFrame(columns=["time", "zenit_asset_id", "value"])
    df = pd.read_parquet(ruta)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df


def _por_minuto(df: pd.DataFrame, nombre: str, agregar="mean") -> pd.Series:
    """Promedio por minuto de cada (activo, serie) y despues `agregar` entre
    series del mismo activo (nucleos, interfaces...)."""
    if df.empty:
        return pd.Series(dtype=float, name=nombre)
    claves = [c for c in df.columns if c not in ("time", "value")]
    df = df.assign(minuto=df["time"].dt.floor("1min"))
    por_serie = df.groupby(claves + ["minuto"], dropna=False)["value"].mean()
    por_activo = getattr(por_serie.groupby(["zenit_asset_id", "minuto"]), agregar)()
    return por_activo.rename(nombre)


def _tasa_por_activo(df: pd.DataFrame, nombre: str) -> pd.Series:
    """Contadores: tasa de cada serie por separado y despues la suma por activo."""
    if df.empty:
        return pd.Series(dtype=float, name=nombre)
    claves = [c for c in df.columns if c not in ("time", "value")]
    df = df.assign(minuto=df["time"].dt.floor("1min"))
    por_serie = (df.groupby(claves + ["minuto"], dropna=False)["value"].mean()
                 .reset_index().sort_values(claves + ["minuto"]))
    serie = por_serie.groupby(claves, dropna=False)
    tasa = serie["value"].diff() / serie["minuto"].diff().dt.total_seconds()
    por_serie["tasa"] = tasa.where(tasa >= 0)     # un salto negativo es un reinicio del contador
    return por_serie.groupby(["zenit_asset_id", "minuto"])["tasa"].sum(min_count=1).rename(nombre)


def variables_de_exportacion(carpeta) -> pd.DataFrame:
    """Exportacion de la plataforma -> (activo, minuto) x VARIABLES."""
    carpeta = Path(carpeta)
    cpu = _leer(carpeta, "system_cpu_utilization")
    mem = _leer(carpeta, "system_memory_utilization")
    fs = _leer(carpeta, "system_filesystem_usage")
    red = _leer(carpeta, "system_network_io")
    llamadas = _leer(carpeta, "traces_span_metrics_calls")

    columnas = []
    for estado in ("user", "system"):
        sub = cpu[cpu.get("state") == estado] if "state" in cpu else cpu.iloc[0:0]
        columnas.append(_por_minuto(sub.drop(columns=["state"], errors="ignore"), f"cpu_{estado}"))
    sub = mem[mem.get("state") == "used"] if "state" in mem else mem.iloc[0:0]
    columnas.append(_por_minuto(sub.drop(columns=["state"], errors="ignore"), "memoria_used"))

    if not fs.empty and {"state", "mountpoint"} <= set(fs.columns):
        raiz = fs[fs["mountpoint"] == "/"]
        por_estado = (raiz.assign(minuto=raiz["time"].dt.floor("1min"))
                      .groupby(["zenit_asset_id", "minuto", "state"])["value"].mean().unstack("state"))
        total = por_estado.sum(axis=1, min_count=1)
        columnas.append((por_estado.get("used") / total).rename("disco_used_frac"))
    else:
        columnas.append(pd.Series(dtype=float, name="disco_used_frac"))

    if not red.empty and "device" in red.columns:
        red = red[red["device"] != "lo"]
    columnas.append(_tasa_por_activo(red, "red_bytes_s"))

    for servicio, nombre in (("pedido", "tasa_llamadas_pedidos"), ("catalogo", "tasa_llamadas_catalogo")):
        if not llamadas.empty and "service_name" in llamadas.columns:
            sub = llamadas[llamadas["service_name"].fillna("").str.contains(servicio, case=False)]
        else:
            sub = llamadas.iloc[0:0]
        columnas.append(_tasa_por_activo(sub, nombre))

    X = pd.concat(columnas, axis=1)
    X.index = X.index.set_names(["activo", "time"])
    return X[VARIABLES].sort_index()


# ------------------------------------------------------------ normalizacion
def normalizar(X: pd.DataFrame, piso: pd.Series) -> pd.DataFrame:
    """z robusto con signo de cada minuto contra los VENTANA anteriores del
    mismo activo. Sin recortar: el recorte es para el agrupamiento."""
    salida = []
    for activo, g in X.groupby(level="activo"):
        g = g.droplevel("activo").copy()
        g[TASAS] = np.log1p(g[TASAS].clip(lower=0))
        previa = g.shift(1)
        mediana = previa.rolling(VENTANA, min_periods=VENTANA).median()
        mad = (previa - mediana).abs().rolling(VENTANA, min_periods=VENTANA).median()
        z = 0.6745 * (g - mediana) / mad.clip(lower=piso, axis=1)
        z["activo"] = activo
        salida.append(z.set_index("activo", append=True).swaplevel())
    return pd.concat(salida).sort_index()


def piso_del_mad(X: pd.DataFrame) -> pd.Series:
    Y = X.copy()
    Y[TASAS] = np.log1p(Y[TASAS].clip(lower=0))
    return (FRACCION_PISO * Y.std()).clip(lower=1e-6).fillna(1e-6)


def _entre(df, desde, hasta):
    t = df.index.get_level_values("time")
    return df[(t >= desde) & (t < hasta)]


# ----------------------------------------------------------------- modelo
class Modelo:
    def __init__(self, k: int = K_DEFECTO):
        self.k = k
        self.kmeans = KMeans(n_clusters=k, n_init=10, max_iter=300, random_state=SEMILLA)

    def entrenar(self, Z: pd.DataFrame):
        Zr = Z.clip(-Z_RECORTE, Z_RECORTE)
        self.kmeans.fit(Zr.to_numpy())
        centros = self.kmeans.cluster_centers_
        self.normales = {g for g in range(self.k) if np.abs(centros[g]).max() < Z_CRITICO}
        grupos, distancias = self._asignar(Zr)
        en_normales = distancias[np.isin(grupos, list(self.normales))]
        self.distancia_max = (float(np.percentile(en_normales, PERCENTIL_DISTANCIA))
                              if len(en_normales) else np.inf)
        return self

    def _asignar(self, Zr):
        d = self.kmeans.transform(Zr.to_numpy())
        grupos = d.argmin(axis=1)
        return grupos, d[np.arange(len(grupos)), grupos]

    def anomalos(self, Z: pd.DataFrame) -> pd.DataFrame:
        """Para cada minuto: grupo, distancia y si es anomalo."""
        grupos, distancias = self._asignar(Z.clip(-Z_RECORTE, Z_RECORTE))
        anomalo = ~np.isin(grupos, list(self.normales)) | (distancias > self.distancia_max)
        return pd.DataFrame({"grupo": grupos, "distancia": distancias, "anomalo": anomalo}, index=Z.index)

    def centroides(self) -> pd.DataFrame:
        c = pd.DataFrame(self.kmeans.cluster_centers_, columns=VARIABLES)
        c["normal"] = [g in self.normales for g in range(self.k)]
        return c


def severidad(z: float) -> str:
    exceso = z / Z_CRITICO
    return "alta" if exceso >= EXCESO_ALTA else "media" if exceso >= EXCESO_MEDIA else "baja"


def anomalias(Z: pd.DataFrame, estado: pd.DataFrame, version: str) -> list:
    """Rachas de minutos anomalos -> filas de `anomalias` (misma persistencia y
    cierre que la linea base)."""
    filas = []
    for activo, g in estado.groupby(level="activo"):
        g = g.droplevel("activo")
        z = Z.loc[activo]
        racha = normales = 0
        abierta = None
        tiempos = list(g.index)
        for i, t in enumerate(tiempos):
            continuo = i > 0 and (t - tiempos[i - 1]).total_seconds() <= 120
            if not continuo:
                racha = 0
            if g.at[t, "anomalo"]:
                racha += 1
                normales = 0
                if abierta is None and racha == PERSISTENCIA:
                    fila = z.loc[t]
                    variable = fila.abs().idxmax()
                    puntaje = float(abs(fila[variable]))
                    abierta = {
                        "activo": activo, "metrica": variable,
                        "dimensiones": json.dumps({"grupo": int(g.at[t, "grupo"]),
                                                   "direccion": "sube" if fila[variable] > 0 else "baja"}),
                        "inicio": t, "fin": None, "estado": "detectada",
                        "severidad": severidad(puntaje), "puntaje": round(puntaje, 3),
                        "evidencia": json.dumps({"distancia": round(float(g.at[t, "distancia"]), 3),
                                                 "metodo": "kmeans-v1"}),
                        "version_modelo": version,
                    }
            else:
                racha = 0
                if abierta is not None:
                    if normales == 0 or not continuo:
                        normales, primero_normal = 1, t      # un hueco reinicia la cuenta
                    else:
                        normales += 1
                    if normales == NORMALIDAD_PARA_CERRAR:
                        abierta["fin"] = primero_normal
                        filas.append(abierta)
                        abierta, normales = None, 0
        if abierta is not None:
            filas.append(abierta)       # sigue abierta al final de la ventana
    filas.sort(key=lambda f: (f["inicio"], f["activo"]))
    for i, f in enumerate(filas, start=1):
        f["id"] = f"k{i}"
    return filas


COLUMNAS_SALIDA = ["id", "activo", "metrica", "dimensiones", "inicio", "fin", "estado",
                   "severidad", "puntaje", "evidencia", "version_modelo"]


def correr(X: pd.DataFrame, entrenar, detectar, k: int = K_DEFECTO):
    """Entrena con la ventana `entrenar` y detecta en `detectar`. Devuelve
    (filas, modelo, version)."""
    piso = piso_del_mad(_entre(X, *entrenar))
    Z = normalizar(X, piso).dropna()
    modelo = Modelo(k).entrenar(_entre(Z, *entrenar))
    version = f"kmeans-v1 k={k} entrenado={entrenar[0]:%Y-%m-%d}/{entrenar[1]:%Y-%m-%d}"
    Zd = _entre(Z, *detectar)
    return anomalias(Zd, modelo.anomalos(Zd), version), modelo, version


def _fecha(texto: str) -> datetime:
    return pd.Timestamp(texto.replace("Z", "+00:00")).tz_convert(timezone.utc).to_pydatetime()


def principal(argv=None) -> int:
    p = argparse.ArgumentParser(description="KMeans por lotes con la salida de `anomalias` (H-034)")
    fuente = p.add_mutually_exclusive_group(required=True)
    fuente.add_argument("--csv", help="data.csv del laboratorio")
    fuente.add_argument("--exportacion", help="carpeta de `zenit.admin exportar`")
    p.add_argument("--entrenar", nargs=2, type=_fecha, required=True, metavar=("DESDE", "HASTA"))
    p.add_argument("--detectar", nargs=2, type=_fecha, required=True, metavar=("DESDE", "HASTA"))
    p.add_argument("-k", type=int, default=K_DEFECTO)
    p.add_argument("--salida", required=True, help="CSV con las columnas de `anomalias`")
    a = p.parse_args(argv)
    if a.entrenar[1] > a.detectar[0]:
        print("aviso: la ventana de entrenamiento se cruza con la de deteccion", file=sys.stderr)

    X = variables_de_csv(a.csv) if a.csv else variables_de_exportacion(a.exportacion)
    print(f"{X.index.get_level_values('activo').nunique()} activos, {len(X)} minutos")
    print("nulos por variable:", X.isna().sum().to_dict())
    filas, modelo, version = correr(X, a.entrenar, a.detectar, a.k)
    print(f"\n{version}: {len(modelo.normales)} de {a.k} grupos normales, "
          f"distancia maxima {modelo.distancia_max:.2f}")
    print(modelo.centroides().round(1).to_string())
    pd.DataFrame(filas, columns=COLUMNAS_SALIDA).to_csv(a.salida, index=False)
    print(f"\n{len(filas)} anomalias en {a.salida}")
    return 0


if __name__ == "__main__":
    sys.exit(principal())
