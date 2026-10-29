"""Mide fuera de linea el piso absoluto de la escala para el swap y el disco.

    python scripts/deteccion/medir_piso.py <carpeta de la exportacion> \
        [--desde 2026-10-26T14:01Z] [--hasta 2026-10-27T23:00Z] [--salida piso/]

Es la medicion de la tarea "El piso del swap y del sistema de archivos:
medirlo, no desplegarlo" (senales.py, seccion "Piso de la escala"). Hermano
de `medir_csv.py`: aquel corre el detector sobre las columnas del data.csv de
DigitalOcean; este lo corre sobre la exportacion de Azure (`zenit.admin
exportar`, un Parquet por metrica) y solo para las dos senales en bytes que
no tienen piso:

- `system_paging_usage`  (state=used, por device)      pisos 0, 8, 16 y 64 MiB
- `system_filesystem_usage` (state=used, por mountpoint) pisos 0, 64, 128 y 256 MiB

Como reproduce el servicio
--------------------------

1. **La serie.** Filtra `state=used`, separa por activo y por la dimension
   de agrupacion de la senal y promedia a 1 minuto, que es lo que el
   detector lee del resumen por minuto (`value_avg`).
2. **La apertura.** `detectar_eventos` de `detector.py` tal cual, con la
   historia completa de mad-v2 y el piso absoluto que se esta midiendo. El
   puntaje de un punto solo depende de los 60 minutos anteriores, asi que
   correrlo una vez sobre la serie entera da los mismos puntos criticos que
   el servicio minuto a minuto.
3. **El bloqueo y el cierre.** Mientras una anomalia sigue abierta, la serie
   no abre otra (deduplicacion del detector). Por eso cada evento abre
   una anomalia solo si la anterior ya cerro, y el cierre se calcula con
   `cierre.momento_de_cierre` sobre la linea base congelada al abrir, con el
   piso del 5 % al cerrar (PR #26). El servicio se entera del cierre cuando
   ve los 15 minutos normales: la serie queda bloqueada hasta `fin + 14 min`.

Lo que no reproduce: los huecos del servicio (reinicios, el limite de
`MAX_ANTIGUEDAD` al arrancar) y los cierres manuales. Por eso la fila "sin
piso" de la ventana de mad-v2 se compara con las anomalias reales de esas
dos senales (`--anomalias`): si coinciden, la medicion se puede creer.

Emparejamiento, el de H-029: un evento cae dentro de una inyeccion si es del
mismo activo y empieza entre el inicio de la inyeccion y su fin mas 5
minutos. Se miran todas las inyecciones (tambien `fallida` y latencia) para
decidir que es ruido: un evento sobre cualquier inyeccion no es ruido.

Solo necesita pandas y pyarrow, ademas del paquete zenit.
"""
import argparse
import json
import pathlib
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "backend"))

from zenit.deteccion import cierre  # noqa: E402
from zenit.deteccion.detector import detectar_eventos  # noqa: E402
from zenit.deteccion.evaluacion import fecha, leer_inyecciones  # noqa: E402

MIB = 2 ** 20
MARGEN_FIN = timedelta(minutes=5)


@dataclass(frozen=True)
class SenalBytes:
    tabla: str
    agrupar: str
    pisos_mib: tuple
    fallas: tuple          # tipos de inyeccion que esta senal deberia ver (senales.py)


SENALES = (
    SenalBytes("system_paging_usage", "device", (0, 8, 16, 64), ("memoria",)),
    SenalBytes("system_filesystem_usage", "mountpoint", (0, 64, 128, 256),
               ("disco_gradual", "disco_abrupto")),
)


# ------------------------------------------------------------------ lectura
def buscar(raiz: pathlib.Path, nombre: str) -> pathlib.Path:
    """La exportacion a veces llega con una carpeta de mas (`export/metricas/
    metricas/`): se busca el archivo en vez de suponer la ruta."""
    encontrados = sorted(raiz.rglob(nombre), key=lambda p: len(p.parts))
    if not encontrados:
        sys.exit(f"no encontre {nombre} dentro de {raiz}")
    return encontrados[0]


def series(raiz: pathlib.Path, senal: SenalBytes) -> dict:
    """{(activo, valor de la agrupacion): [(momento, valor), ...]} a 1 minuto."""
    df = pd.read_parquet(buscar(raiz, f"{senal.tabla}.parquet"),
                         columns=["time", "zenit_asset_id", senal.agrupar, "state", "value"])
    df = df[df.state == "used"].dropna(subset=["value"])
    salida = {}
    for (activo, grupo), g in df.groupby(["zenit_asset_id", senal.agrupar]):
        s = g.set_index("time")["value"].sort_index().resample("1min").mean().dropna()
        salida[(activo, grupo)] = [(t.to_pydatetime(), float(v)) for t, v in s.items()]
    return salida


# ---------------------------------------------------------------- simulacion
@dataclass
class Abierta:
    activo: str
    grupo: str
    inicio: datetime
    fin: datetime | None
    puntaje: float
    severidad: str


def simular(puntos: list, piso_bytes: float, activo: str, grupo: str, desde: datetime,
            hasta: datetime, bloqueo: bool = True, historia_minima=None) -> list:
    """Las anomalias que el servicio habria abierto en esta serie entre
    `desde` y `hasta`, con el bloqueo mientras la anterior sigue abierta (ver
    el encabezado). La serie arranca sin nada abierto en `desde`, como quedo
    la plataforma el 26 despues de los cierres manuales.

    Con `bloqueo=False` cada racha critica cuenta como una anomalia: sirve
    para ventanas largas, donde una sola anomalia que no cierra taparia todo
    lo que viene despues y la medicion diria mas del cierre que del piso.

    `historia_minima` es la de `detectar_eventos` (None = mad-v2; 5 = mad-v1)."""
    eventos = [e for e in detectar_eventos(puntos, piso_absoluto=piso_bytes,
                                           historia_minima=historia_minima)
               if desde <= e.momento < hasta]
    momentos = [t for t, _ in puntos]
    abiertas, bloqueada_hasta = [], None
    for e in eventos:
        if bloqueo and bloqueada_hasta is not None and e.momento <= bloqueada_hasta:
            continue
        linea = e.evidencia["linea_base"]
        fin = _cierre(puntos, momentos, e.indice, linea["mediana"], linea["escala"])
        abiertas.append(Abierta(activo, grupo, e.momento, fin, e.puntaje, e.severidad))
        # sin cierre, la serie queda bloqueada hasta el final de los datos
        bloqueada_hasta = (fin + timedelta(minutes=cierre.NORMALIDAD_PARA_CERRAR - 1)
                           if fin else momentos[-1])
    return abiertas


def _cierre(puntos, momentos, indice, mediana, escala):
    """El primer `fin` que el ciclo de cierre encontraria, minuto a minuto.

    Llamar a `cierre.momento_de_cierre` en cada minuto es cuadratico (una
    anomalia que no cierra recorre dias). Aqui se busca el primer minuto en
    que el ciclo lo veria cumplido, con la misma regla (racha de 15 normales
    sin huecos), y ese minuto se confirma con `momento_de_cierre` una sola vez.
    """
    n = cierre.NORMALIDAD_PARA_CERRAR
    escala_cierre = max(escala, cierre.PISO_RELATIVO_CIERRE * abs(mediana))
    hueco_max = 2 * 60          # holgura de un punto, como en cierre.py
    racha_desde = None
    for j in range(indice + 1, len(puntos)):
        normal = (puntos[j][1] - mediana) / escala_cierre < 3.5
        seguido = j == indice + 1 or (momentos[j] - momentos[j - 1]).total_seconds() <= hueco_max
        if not normal:
            racha_desde = None
            continue
        if racha_desde is None or not seguido:
            racha_desde = j
        if j - indice < n or j - racha_desde + 1 < n:
            continue
        fin = cierre.momento_de_cierre(puntos[indice + 1:j + 1], mediana, escala,
                                       despues_de=momentos[indice])
        if fin is not None:
            return fin
    return None


# -------------------------------------------------------------- emparejar
def atribuir(a: Abierta, inyecciones: list):
    cubren = [i for i in inyecciones
              if i.activo == a.activo and i.inicio <= a.inicio <= (i.fin or i.inicio) + MARGEN_FIN]
    return max(cubren, key=lambda i: i.inicio) if cubren else None


def medir(raiz, inyecciones, desde, hasta, bloqueo: bool = True) -> tuple:
    """Una fila por (senal, piso) y el detalle de cada anomalia simulada."""
    filas, detalle = [], []
    evaluables = [i for i in inyecciones if i.estado == "completada" and i.activo
                  and desde <= i.inicio < hasta]
    for senal in SENALES:
        por_serie = series(raiz, senal)
        objetivo = [i for i in evaluables if i.tipo in senal.fallas]
        for piso in senal.pisos_mib:
            anomalias = []
            for (activo, grupo), puntos in por_serie.items():
                anomalias += simular(puntos, piso * MIB, activo, grupo, desde, hasta, bloqueo)
            dentro, ruido, sobre_otra, detectadas = 0, 0, 0, set()
            for a in anomalias:
                iny = atribuir(a, inyecciones)
                if iny is None:
                    ruido += 1
                    resultado = "ruido"
                elif iny in objetivo:
                    dentro += 1
                    detectadas.add(iny.uid)
                    resultado = "dentro"
                else:
                    sobre_otra += 1
                    resultado = f"sobre {iny.tipo} ({iny.estado})"
                detalle.append({"senal": senal.tabla, "piso_mib": piso, "activo": a.activo,
                                "serie": a.grupo, "inicio": a.inicio.isoformat(),
                                "fin": a.fin.isoformat() if a.fin else "",
                                "puntaje": round(a.puntaje, 2), "severidad": a.severidad,
                                "resultado": resultado, "inyeccion": iny.uid if iny else ""})
            por_tipo = {t: (sum(1 for i in objetivo if i.tipo == t and i.uid in detectadas),
                            sum(1 for i in objetivo if i.tipo == t)) for t in senal.fallas}
            filas.append({
                "senal": senal.tabla, "piso_mib": piso, "anomalias": len(anomalias),
                "dentro": dentro, "sobre_otra": sobre_otra, "ruido": ruido,
                "detectadas": len(detectadas), "objetivo": len(objetivo), "por_tipo": por_tipo,
                "alta_dentro": sum(1 for d in detalle if d["senal"] == senal.tabla and d["piso_mib"] == piso
                                   and d["resultado"] == "dentro" and d["severidad"] == "alta"),
            })
    return filas, detalle


# ------------------------------------------------------------------ informe
def tabla(filas, titulo) -> str:
    lineas = [f"### {titulo}", "",
              "| Senal | Piso | Anomalias | Ruido | Sobre otra inyeccion | Dentro | Inyecciones detectadas | `alta` dentro |",
              "|---|---|---|---|---|---|---|---|"]
    for f in filas:
        tipos = ", ".join(f"{t} {d} de {n}" for t, (d, n) in f["por_tipo"].items())
        piso = "sin piso" if not f["piso_mib"] else f"{f['piso_mib']} MiB"
        lineas.append(f"| `{f['senal']}` | {piso} | {f['anomalias']} | {f['ruido']} | {f['sobre_otra']} | "
                      f"{f['dentro']} | {f['detectadas']} de {f['objetivo']} ({tipos}) | {f['alta_dentro']} |")
    return "\n".join(lineas) + "\n"


def contraste(ruta_anomalias, detalle, desde, hasta) -> str:
    """La fila sin piso contra lo que la plataforma abrio de verdad."""
    reales = pd.read_csv(ruta_anomalias)
    reales = reales[reales.metrica.isin([s.tabla for s in SENALES])
                    & reales.version_modelo.fillna("").str.startswith("mad-v2")]
    reales["inicio"] = reales.inicio.map(fecha)
    reales = reales[(reales.inicio >= desde) & (reales.inicio < hasta)]
    lineas = ["### Contraste con la plataforma (sin piso, mad-v2)", "",
              "| Senal | Activo | Abiertas por la plataforma | Simuladas | En las dos (mismo minuto) |",
              "|---|---|---|---|---|"]
    for s in SENALES:
        sim = {(d["activo"], d["inicio"]) for d in detalle if d["senal"] == s.tabla and d["piso_mib"] == 0}
        for activo in sorted(set(reales.activo) | {a for a, _ in sim}):
            r = {(activo, t.isoformat()) for t in reales[(reales.metrica == s.tabla)
                                                         & (reales.activo == activo)].inicio}
            si = {x for x in sim if x[0] == activo}
            nota = "" if si or not r else " (sin metricas en la exportacion)"
            lineas.append(f"| `{s.tabla}` | {activo}{nota} | {len(r)} | {len(si)} | {len(r & si)} |")
    return "\n".join(lineas) + "\n"


def principal(argv=None):
    p = argparse.ArgumentParser(description="Piso de la escala del swap y del disco, fuera de linea")
    p.add_argument("exportacion", type=pathlib.Path, help="carpeta de la exportacion de Azure")
    p.add_argument("--desde", type=fecha, help="inicio de la ventana (por defecto, la de la exportacion)")
    p.add_argument("--hasta", type=fecha, help="fin de la ventana (por defecto, la de la exportacion)")
    p.add_argument("--anomalias", type=pathlib.Path, help="anomalias.csv, para el contraste con la plataforma")
    p.add_argument("--sin-bloqueo", action="store_true",
                   help="cada racha critica cuenta, aunque la anterior no haya cerrado")
    p.add_argument("--salida", type=pathlib.Path, help="carpeta para informe.md y anomalias_simuladas.csv")
    a = p.parse_args(argv)

    manifiesto = json.loads(buscar(a.exportacion, "manifiesto.json").read_text())
    desde = a.desde or fecha(manifiesto["desde"])
    hasta = a.hasta or fecha(manifiesto["hasta"])
    ruta_iny = buscar(a.exportacion, "inyecciones.csv")
    inyecciones = leer_inyecciones(ruta_iny)

    filas, detalle = medir(a.exportacion, inyecciones, desde, hasta, bloqueo=not a.sin_bloqueo)
    texto = (f"Ventana {desde:%Y-%m-%d %H:%M} a {hasta:%Y-%m-%d %H:%M} UTC, "
             f"{'sin bloqueo (cada racha cuenta)' if a.sin_bloqueo else 'con bloqueo y cierre como el servicio'}; "
             f"exportacion generada {manifiesto.get('generado', '?')}, "
             f"activo {manifiesto.get('activo') or 'todos'}.\n\n")
    texto += tabla(filas, "Piso absoluto de la escala")
    ruta_anomalias = None if a.sin_bloqueo else ruta_anomalias_de(a)
    if ruta_anomalias:
        texto += "\n" + contraste(ruta_anomalias, detalle, desde, hasta)
    print(texto)
    if a.salida:
        a.salida.mkdir(parents=True, exist_ok=True)
        (a.salida / "informe.md").write_text(texto)
        pd.DataFrame(detalle).to_csv(a.salida / "anomalias_simuladas.csv", index=False)
    return filas, detalle


def ruta_anomalias_de(a):
    return a.anomalias or next(iter(sorted(a.exportacion.rglob("anomalias.csv"))), None)


if __name__ == "__main__":
    principal()
