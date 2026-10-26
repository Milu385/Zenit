"""Evaluacion formal por evento (H-029).

    python -m zenit.deteccion.evaluacion \
        --inyecciones exportacion/inyecciones.csv --anomalias anomalias.csv \
        --desde 2026-10-24T00:00Z --hasta 2026-10-28T00:00Z --version mad-v2 \
        --salida informe-h029/

Mide un detector contra la verdad de referencia del laboratorio. No toca la
plataforma: lee dos CSV y escribe un informe. Sirve igual para la linea base
(la tabla `anomalias` exportada) y para KMeans (H-034), siempre que su archivo
tenga las mismas columnas (`activo`, `metrica`, `inicio` y `version_modelo`
son las que se usan; `estado` y `dimensiones` si estan).

Por evento, no por fila
-----------------------

**Emparejamiento.** Una anomalia corresponde a una inyeccion si es del mismo
activo y empieza entre el inicio de la inyeccion y su fin mas 5 minutos
(`MARGEN_FIN`). Los 5 minutos cubren la persistencia (3 puntos) y el retraso
del resumen por minuto: una inyeccion corta puede terminar antes de que la
regla de persistencia se cumpla.

- Una inyeccion con al menos una anomalia emparejada es un **acierto**.
- Una anomalia emparejada con una inyeccion es **correcta**, aunque haya
  varias para la misma inyeccion (una de memoria y otra de paginacion, por
  ejemplo): todas senalan algo que si paso.
- Una anomalia sin inyeccion es un **falso positivo**.

  precision     = anomalias correctas / anomalias evaluadas
  exhaustividad = inyecciones detectadas / inyecciones evaluadas
  F1            = media armonica de las dos

**Tiempo hasta la deteccion.** Inicio de la primera anomalia emparejada menos
inicio de la inyeccion, por inyeccion detectada.

**Que inyecciones cuentan.** Solo las `completada`, con activo resuelto y con
inicio dentro de la ventana evaluada. Una anomalia que cae sobre una
inyeccion `fallida` o `en_curso` (o sobre una sin activo, o fuera de la
ventana) no se cuenta ni como correcta ni como falso positivo: no hay verdad
de referencia confiable para decir que fue. Se informa aparte, como
`ignoradas`, para que el numero se vea.

**Latencia.** El metodo no la ve (senales.py, "Lo que este catalogo no cubre
todavia"). Sus inyecciones se informan en su propia fila y no entran en los
totales, salvo con `--incluir-latencia`. Es la limitacion declarada, no un
resultado que se esconda.

**Tipos.** Los de la tabla `inyecciones` (cpu, memoria, disco, caida,
latencia), con disco partido en gradual y abrupto segun `parametros.modo`,
igual que la campana (laboratorio/.../campana.py).

**Marcas del operador (P-DET-04).** De las anomalias marcadas
`falso_positivo`, cuantas lo eran segun la verdad de referencia y cuantas
estaban emparejadas con una inyeccion (el operador se equivoco).

**Reproducibilidad (H-036).** El informe declara las versiones del modelo
evaluadas, la ventana, el conjunto de inyecciones (cantidad y huella de sus
uid) y la fecha de corte.
"""
import argparse
import csv
import hashlib
import json
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

MARGEN_FIN = timedelta(minutes=5)
TIPOS = ("cpu", "memoria", "disco_gradual", "disco_abrupto", "caida", "latencia")
FUERA_DEL_ALCANCE = ("latencia",)


# ------------------------------------------------------------------ lectura
def fecha(texto) -> Optional[datetime]:
    """ISO 8601 a datetime en UTC. Acepta la `Z` final y el espacio de psql."""
    if texto is None:
        return None
    if isinstance(texto, datetime):
        return texto if texto.tzinfo else texto.replace(tzinfo=timezone.utc)
    texto = str(texto).strip()
    if not texto:
        return None
    texto = texto.replace("Z", "+00:00").replace(" ", "T", 1)
    if texto[-3:-2] in "+-" and texto[-6:-5] not in "+-" and ":" not in texto[-3:]:
        texto += ":00"                     # psql escribe +00, no +00:00
    d = datetime.fromisoformat(texto)
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _json(texto) -> dict:
    if isinstance(texto, dict):
        return texto
    try:
        return json.loads(texto) if texto else {}
    except (TypeError, ValueError):
        return {}


@dataclass(frozen=True)
class Inyeccion:
    uid: str
    tipo: str            # cpu, memoria, disco_gradual, disco_abrupto, caida, latencia
    activo: Optional[str]
    inicio: datetime
    fin: Optional[datetime]
    estado: str

    @property
    def hasta(self) -> datetime:
        """Hasta cuando una anomalia todavia se le atribuye."""
        return (self.fin or self.inicio) + MARGEN_FIN


@dataclass(frozen=True)
class Anomalia:
    id: str
    activo: str
    metrica: str
    inicio: datetime
    estado: str = ""
    version_modelo: str = ""
    dimensiones: dict = field(default_factory=dict, hash=False, compare=False)


def tipo_de(fila: dict) -> str:
    tipo = fila["tipo"]
    if tipo == "disco":
        return "disco_" + (_json(fila.get("parametros")).get("modo") or "gradual")
    return tipo


def leer_inyecciones(ruta) -> list:
    with open(ruta, newline="") as f:
        return [Inyeccion(uid=r["uid"], tipo=tipo_de(r), activo=r.get("activo") or None,
                          inicio=fecha(r["inicio"]), fin=fecha(r.get("fin")), estado=r["estado"])
                for r in csv.DictReader(f)]


def leer_anomalias(ruta) -> list:
    with open(ruta, newline="") as f:
        return [Anomalia(id=r.get("id") or str(i), activo=r["activo"], metrica=r.get("metrica", ""),
                         inicio=fecha(r["inicio"]), estado=r.get("estado") or "",
                         version_modelo=r.get("version_modelo") or "",
                         dimensiones=_json(r.get("dimensiones")))
                for i, r in enumerate(csv.DictReader(f))]


# ---------------------------------------------------------------- evaluacion
@dataclass
class Resultado:
    desde: datetime
    hasta: datetime
    versiones: Counter
    inyecciones: list                 # las evaluadas
    anomalias: list                   # las evaluadas (correctas + falsos positivos)
    emparejadas: dict                 # anomalia.id -> Inyeccion
    ignoradas: list                   # (Anomalia, motivo)
    por_inyeccion: dict               # uid -> [Anomalia]
    fuera_del_alcance: list           # inyecciones evaluables que no entran en totales


def _atribuir(anomalia, candidatas):
    """La inyeccion del mismo activo que cubre el inicio de la anomalia. Si
    hubiera dos (no pasa con 45 min de normalidad entre inyecciones), la que
    empezo mas tarde: es la mas cercana a la anomalia."""
    cubren = [i for i in candidatas
              if i.activo == anomalia.activo and i.inicio <= anomalia.inicio <= i.hasta]
    return max(cubren, key=lambda i: i.inicio) if cubren else None


def evaluar(inyecciones: Iterable[Inyeccion], anomalias: Iterable[Anomalia], desde: datetime,
            hasta: datetime, *, version: str = "", incluir_latencia: bool = False) -> Resultado:
    inyecciones, anomalias = list(inyecciones), list(anomalias)
    en_ventana = [a for a in anomalias if desde <= a.inicio < hasta
                  and (not version or a.version_modelo.startswith(version))]
    alcance = () if incluir_latencia else FUERA_DEL_ALCANCE

    def evaluable(i):
        return i.estado == "completada" and i.activo and desde <= i.inicio < hasta

    validas = [i for i in inyecciones if evaluable(i)]
    evaluadas = [i for i in validas if i.tipo not in alcance]
    fuera = [i for i in validas if i.tipo in alcance]
    # todo lo que no es una inyeccion evaluada en totales es "sin verdad confiable"
    otras = [i for i in inyecciones if i not in evaluadas]

    emparejadas, ignoradas, contadas = {}, [], []
    por_inyeccion = defaultdict(list)
    for a in sorted(en_ventana, key=lambda a: a.inicio):
        iny = _atribuir(a, evaluadas)
        if iny is not None:
            emparejadas[a.id] = iny
            por_inyeccion[iny.uid].append(a)
            contadas.append(a)
            continue
        otra = _atribuir(a, otras)
        if otra is not None:
            motivo = (f"{otra.tipo}, fuera del alcance" if otra in fuera
                      else f"inyeccion {otra.estado}" if otra.estado != "completada"
                      else "inyeccion fuera de la ventana")
            ignoradas.append((a, motivo))
            continue
        contadas.append(a)          # falso positivo
    return Resultado(desde=desde, hasta=hasta,
                     versiones=Counter(a.version_modelo for a in en_ventana),
                     inyecciones=evaluadas, anomalias=contadas, emparejadas=emparejadas,
                     ignoradas=ignoradas, por_inyeccion=dict(por_inyeccion),
                     fuera_del_alcance=fuera)


def _f1(p, r):
    return 2 * p * r / (p + r) if p + r else 0.0


def metricas(r: Resultado, tipo: Optional[str] = None) -> dict:
    """Precision, exhaustividad, F1 y tiempos, en total o para un tipo.

    Por tipo, la precision se calcula sobre las anomalias emparejadas con ese
    tipo: un falso positivo no tiene tipo, asi que los falsos positivos solo
    entran en la precision total. Por eso la fila de un tipo dice `-` en
    falsos positivos."""
    inys = [i for i in r.inyecciones if tipo is None or i.tipo == tipo]
    detectadas = [i for i in inys if i.uid in r.por_inyeccion]
    retardos = [(min(a.inicio for a in r.por_inyeccion[i.uid]) - i.inicio).total_seconds() / 60
                for i in detectadas]
    exhaustividad = len(detectadas) / len(inys) if inys else 0.0
    if tipo is None:
        correctas = len(r.emparejadas)
        falsos = len(r.anomalias) - correctas
        precision = correctas / len(r.anomalias) if r.anomalias else 0.0
    else:
        correctas = sum(len(r.por_inyeccion.get(i.uid, [])) for i in inys)
        falsos = None
        precision = None
    return {
        "inyecciones": len(inys), "detectadas": len(detectadas),
        "anomalias_correctas": correctas, "falsos_positivos": falsos,
        "precision": precision, "exhaustividad": exhaustividad,
        "f1": _f1(precision, exhaustividad) if precision is not None else None,
        "retardo_medio_min": statistics.mean(retardos) if retardos else None,
        "retardo_mediano_min": statistics.median(retardos) if retardos else None,
        "retardo_max_min": max(retardos) if retardos else None,
    }


def falsos_positivos_por_nodo(r: Resultado) -> dict:
    dias = max((r.hasta - r.desde).total_seconds() / 86400, 1e-9)
    por_nodo = Counter(a.activo for a in r.anomalias if a.id not in r.emparejadas)
    return {activo: {"total": n, "por_dia": n / dias} for activo, n in sorted(por_nodo.items())}


def marcas_del_operador(r: Resultado) -> dict:
    """P-DET-04: las marcadas `falso_positivo`, contra la verdad de referencia."""
    marcadas = [a for a in r.anomalias if a.estado == "falso_positivo"]
    acertadas = [a for a in marcadas if a.id not in r.emparejadas]
    return {"marcadas": len(marcadas), "eran_falso_positivo": len(acertadas),
            "estaban_emparejadas": len(marcadas) - len(acertadas),
            "falsos_positivos_sin_marcar": sum(1 for a in r.anomalias
                                               if a.id not in r.emparejadas and a.estado != "falso_positivo")}


def huella(inyecciones) -> str:
    return hashlib.sha256("\n".join(sorted(i.uid for i in inyecciones)).encode()).hexdigest()[:12]


# ------------------------------------------------------------------- informe
def _pct(x):
    return "-" if x is None else f"{x:.1%}".replace(".", ",")


def _min(x):
    return "-" if x is None else f"{x:.1f}".replace(".", ",")


def informe(r: Resultado, *, corte: Optional[datetime] = None, nombre: str = "") -> str:
    tot = metricas(r)
    lineas = [f"# Evaluacion por evento{' · ' + nombre if nombre else ''} (H-029)", ""]
    lineas += [
        "## Reproducibilidad", "",
        "| Dato | Valor |", "|---|---|",
        f"| Version del modelo | {', '.join(f'`{v}` ({n})' for v, n in r.versiones.most_common()) or '-'} |",
        f"| Ventana evaluada (UTC) | {r.desde:%Y-%m-%d %H:%M} a {r.hasta:%Y-%m-%d %H:%M} |",
        f"| Inyecciones evaluadas | {len(r.inyecciones)} completadas, huella `{huella(r.inyecciones)}` |",
        f"| Fecha de corte | {(corte or datetime.now(timezone.utc)):%Y-%m-%d %H:%M} UTC |",
        f"| Emparejamiento | mismo activo, inicio entre el de la inyeccion y su fin + {int(MARGEN_FIN.total_seconds() // 60)} min |",
        "",
        "## Resultado", "",
        "| Tipo | Inyecciones | Detectadas | Exhaustividad | Anomalias correctas | Falsos positivos | Precision | F1 | Retardo medio (min) | Retardo mediano (min) |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for tipo in TIPOS:
        if tipo in FUERA_DEL_ALCANCE and not any(i.tipo == tipo for i in r.inyecciones):
            continue
        m = metricas(r, tipo)
        if not m["inyecciones"]:
            continue
        lineas.append(f"| {tipo} | {m['inyecciones']} | {m['detectadas']} | {_pct(m['exhaustividad'])} | "
                      f"{m['anomalias_correctas']} | - | - | - | {_min(m['retardo_medio_min'])} | "
                      f"{_min(m['retardo_mediano_min'])} |")
    lineas.append(f"| **Total** | {tot['inyecciones']} | {tot['detectadas']} | {_pct(tot['exhaustividad'])} | "
                  f"{tot['anomalias_correctas']} | {tot['falsos_positivos']} | {_pct(tot['precision'])} | "
                  f"{_pct(tot['f1'])} | {_min(tot['retardo_medio_min'])} | {_min(tot['retardo_mediano_min'])} |")

    lineas += ["", "## Falsos positivos por nodo", "",
               "| Activo | Falsos positivos | Por dia |", "|---|---|---|"]
    for activo, d in falsos_positivos_por_nodo(r).items():
        lineas.append(f"| {activo} | {d['total']} | {_min(d['por_dia'])} |")
    dias = (r.hasta - r.desde).total_seconds() / 86400
    lineas.append(f"| **Total** | {tot['falsos_positivos']} | {_min(tot['falsos_positivos'] / dias if dias else None)} |")

    fp = Counter(a.metrica for a in r.anomalias if a.id not in r.emparejadas)
    if fp:
        lineas += ["", "Falsos positivos por metrica: "
                   + ", ".join(f"`{m}` {n}" for m, n in fp.most_common()) + "."]

    mo = marcas_del_operador(r)
    lineas += ["", "## Marcas del operador (P-DET-04)", "",
               f"- Marcadas como falso positivo: {mo['marcadas']}.",
               f"- Eran falso positivo segun la verdad de referencia: {mo['eran_falso_positivo']}.",
               f"- Estaban emparejadas con una inyeccion (la marca se equivoco): {mo['estaban_emparejadas']}.",
               f"- Falsos positivos que nadie marco: {mo['falsos_positivos_sin_marcar']}."]

    perdidas = [i for i in r.inyecciones if i.uid not in r.por_inyeccion]
    lineas += ["", "## Inyecciones no detectadas", ""]
    lineas += [f"- `{i.uid}` ({i.tipo}, {i.activo}, {i.inicio:%Y-%m-%d %H:%M})" for i in perdidas] or ["Ninguna."]

    if r.fuera_del_alcance or r.ignoradas:
        lineas += ["", "## Fuera de los totales", ""]
        if r.fuera_del_alcance:
            lineas.append(f"- Inyecciones de latencia: {len(r.fuera_del_alcance)}. El metodo no mira "
                          "la latencia (limitacion declarada); anomalias que coincidieron con alguna "
                          f"de ellas: {sum(1 for _, m in r.ignoradas if m.endswith('fuera del alcance'))}.")
        motivos = Counter(m for _, m in r.ignoradas if not m.endswith("fuera del alcance"))
        for motivo, n in motivos.most_common():
            lineas.append(f"- Anomalias sobre una {motivo}: {n} (ni correctas ni falsos positivos).")
    return "\n".join(lineas) + "\n"


def escribir_detalle(r: Resultado, salida: Path) -> None:
    salida.mkdir(parents=True, exist_ok=True)
    with open(salida / "por_inyeccion.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["uid", "tipo", "activo", "inicio", "fin", "detectada", "anomalias", "retardo_min"])
        for i in r.inyecciones:
            anos = r.por_inyeccion.get(i.uid, [])
            retardo = (min(a.inicio for a in anos) - i.inicio).total_seconds() / 60 if anos else ""
            w.writerow([i.uid, i.tipo, i.activo, i.inicio.isoformat(), i.fin.isoformat() if i.fin else "",
                        bool(anos), " ".join(a.id for a in anos), retardo])
    with open(salida / "por_anomalia.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "activo", "metrica", "inicio", "estado", "version_modelo", "resultado", "inyeccion"])
        for a in r.anomalias:
            iny = r.emparejadas.get(a.id)
            w.writerow([a.id, a.activo, a.metrica, a.inicio.isoformat(), a.estado, a.version_modelo,
                        "correcta" if iny else "falso_positivo", iny.uid if iny else ""])
        for a, motivo in r.ignoradas:
            w.writerow([a.id, a.activo, a.metrica, a.inicio.isoformat(), a.estado, a.version_modelo,
                        "ignorada", motivo])


def principal(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m zenit.deteccion.evaluacion",
                                description="Evaluacion por evento contra la verdad de referencia (H-029)")
    p.add_argument("--inyecciones", required=True, help="inyecciones.csv de la exportacion")
    p.add_argument("--anomalias", required=True, help="anomalias.csv (tabla exportada o salida de H-034)")
    p.add_argument("--desde", required=True, type=fecha, help="inicio de la ventana, UTC (fecha de los arreglos)")
    p.add_argument("--hasta", required=True, type=fecha, help="fin de la ventana, UTC")
    p.add_argument("--version", default="", help="solo anomalias cuyo version_modelo empieza asi (mad-v2)")
    p.add_argument("--incluir-latencia", action="store_true", help="contar la latencia en los totales")
    p.add_argument("--corte", type=fecha, help="fecha de corte declarada (la de la exportacion)")
    p.add_argument("--nombre", default="", help="titulo del informe, por ejemplo 'linea base'")
    p.add_argument("--salida", type=Path, help="carpeta para informe.md, por_inyeccion.csv y por_anomalia.csv")
    a = p.parse_args(argv)

    r = evaluar(leer_inyecciones(a.inyecciones), leer_anomalias(a.anomalias), a.desde, a.hasta,
                version=a.version, incluir_latencia=a.incluir_latencia)
    texto = informe(r, corte=a.corte, nombre=a.nombre)
    print(texto)
    if a.salida:
        escribir_detalle(r, a.salida)
        (a.salida / "informe.md").write_text(texto)
    return 0


if __name__ == "__main__":
    sys.exit(principal())
