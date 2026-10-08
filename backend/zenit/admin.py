"""Herramienta de administracion del almacen (H-005).

Se corre dentro del contenedor de la API, que ya tiene la red y el token:

    docker compose exec api python -m zenit.admin crear-bases
    docker compose exec api python -m zenit.admin verificar
    docker compose exec api python -m zenit.admin huecos activo-aws-nodo-01 --minutos 30

Y para el laboratorio de fallos (epica 2):

    python -m zenit.admin cargar-verdad --cada 60          (lo corre el servicio cargador)
    python -m zenit.admin exportar --desde 2026-10-05T00:00Z --hasta 2026-10-07T00:00Z --salida /exportaciones/x

crear-bases es idempotente: si la base existe, solo ajusta su retencion.
"""
import argparse
import csv
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from zenit.esquema import COLUMNA_TIEMPO, COLUMNA_VALOR, ETIQUETAS_CANONICAS, es_identificador
from zenit.influx import ClienteInflux, ErrorInflux

# Niveles de resolucion y retencion. Documento de esquema de medicion,
# seccion 3. Se pueden sobrescribir por variable de entorno.
BASES = (
    ("zenit_raw", os.environ.get("RETENCION_RAW", "30d")),
    ("zenit_1m", os.environ.get("RETENCION_1M", "30d")),
    ("zenit_1h", os.environ.get("RETENCION_1H", "180d")),
)
LIMITE_COLUMNAS = int(os.environ.get("LIMITE_COLUMNAS", "500"))


def _cliente() -> ClienteInflux:
    return ClienteInflux(os.environ.get("INFLUX_URL", "http://influxdb:8181"), os.environ["INFLUX_TOKEN"])


def crear_bases(cliente: ClienteInflux) -> int:
    for bd, retencion in BASES:
        if cliente.crear_base(bd, retencion):
            print(f"creada   {bd:10s} retencion {retencion}")
        else:
            cliente.fijar_retencion(bd, retencion)
            print(f"existia  {bd:10s} retencion ajustada a {retencion}")
    return 0


def verificar(cliente: ClienteInflux, horas: int = 24) -> int:
    """Las tres comprobaciones de la seccion 7 del documento de esquema."""
    fallas = 0
    desde = (datetime.now(timezone.utc) - timedelta(hours=horas)).strftime("%Y-%m-%d %H:%M:%S")
    for bd, _ in BASES:
        filas = cliente.consultar(
            bd,
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema NOT IN ('information_schema', 'system')",
        )
        tablas: dict = {}
        for f in filas:
            tablas.setdefault(f["table_name"], []).append(f["column_name"])
        if not tablas:
            nota = " (se llena con los resumenes de H-018)" if bd != "zenit_raw" else ""
            print(f"\n[{bd}] sin tablas{nota}")
            continue
        print(f"\n[{bd}] {len(tablas)} tablas")
        for tabla, columnas in sorted(tablas.items()):
            problemas = []
            faltan = [e for e in ETIQUETAS_CANONICAS if e not in columnas]
            if faltan:
                problemas.append(f"faltan etiquetas canonicas: {', '.join(faltan)}")
            if len(columnas) > LIMITE_COLUMNAS * 0.8:
                problemas.append(f"{len(columnas)} columnas, cerca del limite de {LIMITE_COLUMNAS}")
            # Las canonicas nunca pueden tener nulos. Las dimensiones propias
            # de una metrica pueden faltar en algunas filas (error_type solo
            # aparece en las peticiones fallidas): se informan, no fallan.
            canonicas = [c for c in ETIQUETAS_CANONICAS if c in columnas]
            propias = [c for c in columnas if c not in (COLUMNA_TIEMPO, COLUMNA_VALOR)
                       and c not in ETIQUETAS_CANONICAS and not c.startswith("value_")
                       and es_identificador(c)]
            avisos = []
            if (canonicas or propias) and es_identificador(tabla):
                conteos = ", ".join(f'count("{c}") AS "{c}"' for c in canonicas + propias)
                (r,) = cliente.consultar(
                    bd, f'SELECT count(*) AS total, {conteos} FROM "{tabla}" '
                        f"WHERE time >= TIMESTAMP '{desde}'")
                nulos = {c: r["total"] - r.get(c, 0) for c in canonicas + propias if r["total"] - r.get(c, 0)}
                malos = {c: n for c, n in nulos.items() if c in ETIQUETAS_CANONICAS}
                if malos:
                    problemas.append("nulos en etiquetas canonicas: " + ", ".join(f"{c} ({n})" for c, n in malos.items()))
                opcionales = {c: n for c, n in nulos.items() if c not in ETIQUETAS_CANONICAS}
                if opcionales:
                    avisos.append("dimensiones opcionales con nulos: " + ", ".join(f"{c} ({n})" for c, n in opcionales.items()))
                filas_txt = f"{r['total']} filas en {horas} h"
            else:
                filas_txt = "sin etiquetas que revisar"
            estado = "OK   " if not problemas else "FALLA"
            fallas += bool(problemas)
            print(f"  {estado} {tabla:45s} {len(columnas):3d} columnas, {filas_txt}")
            for p in problemas:
                print(f"        - {p}")
            for a in avisos:
                print(f"        aviso: {a}")
    print("\nresultado:", "todo en orden" if not fallas else f"{fallas} tablas con problemas")
    return 1 if fallas else 0


def saltos(cliente: ClienteInflux, activo: str, metrica: str,
           desde: datetime, hasta: datetime) -> tuple[int, list]:
    """(muestras, [(inicio, fin)]) con los saltos de mas de 20 s entre muestras, en segundos de la epoca."""
    formato = "%Y-%m-%d %H:%M:%S"
    desde, hasta = desde.astimezone(timezone.utc), hasta.astimezone(timezone.utc)
    marcas = []
    for d, h in _dias(desde, hasta):
        filas = cliente.consultar(
            "zenit_raw",
            f'SELECT DISTINCT CAST(time AS BIGINT) AS t FROM "{metrica}" '
            f"WHERE \"zenit_asset_id\" = $activo AND time >= TIMESTAMP '{d.strftime(formato)}' "
            f"AND time < TIMESTAMP '{h.strftime(formato)}' ORDER BY t",
            {"activo": activo},
        )
        marcas += [int(f["t"]) // 1_000_000_000 for f in filas]
    limites = [int(desde.timestamp())] + marcas + [int(hasta.timestamp())]
    return len(marcas), [(a, b) for a, b in zip(limites, limites[1:]) if b - a > 20]


def huecos(cliente: ClienteInflux, activo: str, minutos: int, metrica: str) -> int:
    """Huecos en la telemetria de un activo. Es la prueba de H-003: tras un
    corte de 5 minutos con la cola persistente del agente, no debe haber
    ninguno.

    Mira las marcas de tiempo crudas y no intervalos de 10 s fijos: el agente
    toma muestras cada 10 s pero sin alinear a la epoca, y un intervalo fijo
    puede quedar vacio sin que falte nada. Un hueco es un salto de mas de
    20 s entre dos muestras consecutivas.
    """
    if not es_identificador(metrica):
        print(f"nombre de metrica no valido: {metrica!r}", file=sys.stderr)
        return 2
    hasta = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(seconds=30)  # lo reciente puede venir en camino
    desde = hasta - timedelta(minutes=minutos)
    n, encontrados = saltos(cliente, activo, metrica, desde, hasta)
    print(f"{metrica} de {activo}, {desde.strftime('%H:%M:%S')} a {hasta.strftime('%H:%M:%S')} UTC: "
          f"{n} muestras, {len(encontrados)} huecos de mas de 20 s")
    for a, b in encontrados:
        ini = datetime.fromtimestamp(a, timezone.utc).strftime("%H:%M:%S")
        fin = datetime.fromtimestamp(b, timezone.utc).strftime("%H:%M:%S")
        print(f"  hueco de {b - a} s entre {ini} y {fin}")
    return 1 if encontrados else 0


# ------------------------------------------------------------------ epica 2
def _conexion_pg():
    import psycopg

    url = os.environ.get("PG_URL")
    if not url:
        raise SystemExit("falta PG_URL (postgresql://usuario:clave@postgres:5432/zenit)")
    return psycopg.connect(url)


def cargar_verdad(carpeta: Path, cada: int, crudo: Optional[Path] = None, activo: str = "") -> int:
    """Pasa la verdad de referencia de los registros de la ingesta a PostgreSQL.

    Con --cada N corre para siempre, una pasada cada N segundos. Un error de
    una pasada (PostgreSQL reiniciando, un archivo a medio escribir) se
    informa y se reintenta en la siguiente: el cargador no se cae por eso.
    """
    from zenit import verdad

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    while True:
        try:
            with _conexion_pg() as conexion:
                filas, cambiadas, descartadas = verdad.pasada(conexion, carpeta, crudo, activo or None)
            print(f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} inyecciones={filas} "
                  f"cambiadas={cambiadas} descartadas={descartadas}", flush=True)
        except Exception as e:
            print(f"pasada fallida: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
            if not cada:
                return 2
        if not cada:
            return 0
        time.sleep(cada)


def _fecha_arg(texto: str) -> datetime:
    """Acepta cualquier zona y devuelve UTC: el motor interpreta las fechas del SQL en UTC."""
    momento = datetime.fromisoformat(texto.replace("Z", "+00:00"))
    momento = momento if momento.tzinfo else momento.replace(tzinfo=timezone.utc)
    return momento.astimezone(timezone.utc)


def _dias(desde: datetime, hasta: datetime):
    """Tramos de un dia como mucho: ninguna consulta toca demasiados archivos del motor."""
    dia = desde
    while dia < hasta:
        fin = min(dia + timedelta(days=1), hasta)
        yield dia, fin
        dia = fin


def _columnas(cliente: ClienteInflux, bd: str = "zenit_raw") -> dict:
    """{tabla: [columnas]} segun el esquema del motor."""
    filas = cliente.consultar(
        bd,
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema NOT IN ('information_schema', 'system')",
    )
    tablas: dict = {}
    for f in filas:
        if es_identificador(f["table_name"]):
            tablas.setdefault(f["table_name"], []).append(f["column_name"])
    return {t: sorted(set(c)) for t, c in sorted(tablas.items())}


def exportar(cliente: ClienteInflux, desde: datetime, hasta: datetime, salida: Path,
             activo: str = "", ruta_catalogo: str = "", conexion=None) -> int:
    """Conjunto de datos para trabajar sin la plataforma (plan de la epica 2, seccion 7).

    salida/
      metricas/<tabla>.parquet   una fila por punto: time (UTC), etiquetas, dimensiones y value
      inyecciones.csv            la verdad de referencia que toca el rango
      huecos.csv                 saltos de mas de 20 s por activo (system_cpu_utilization)
      manifiesto.json            rango, filas por tabla y momento de la exportacion

    Se consulta de a un dia y se escribe cada dia al Parquet antes de pedir el
    siguiente, asi que una exportacion de semanas no llena la memoria de la
    plataforma. El esquema de cada Parquet sale del esquema del motor, igual
    para todos los dias aunque una dimension aparezca a mitad del rango.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    desde, hasta = desde.astimezone(timezone.utc), hasta.astimezone(timezone.utc)
    formato = "%Y-%m-%d %H:%M:%S"
    (salida / "metricas").mkdir(parents=True, exist_ok=True)
    manifiesto = {"desde": desde.isoformat(), "hasta": hasta.isoformat(), "activo": activo or None,
                  "generado": datetime.now(timezone.utc).isoformat(timespec="seconds"), "tablas": {}}
    filtro = ' AND "zenit_asset_id" = $activo' if activo else ""
    params = {"activo": activo} if activo else None
    for tabla, columnas_motor in _columnas(cliente).items():
        etiquetas = [c for c in columnas_motor if c not in (COLUMNA_TIEMPO, COLUMNA_VALOR)]
        esquema = pa.schema([("time", pa.timestamp("ns", tz="UTC"))]
                            + [(c, pa.string()) for c in etiquetas] + [(COLUMNA_VALOR, pa.float64())])
        ruta = salida / "metricas" / f"{tabla}.parquet"
        escritor, total = None, 0
        try:
            for d, h in _dias(desde, hasta):
                filas = cliente.consultar(
                    "zenit_raw",
                    f'SELECT *, CAST(time AS BIGINT) AS time_ns FROM "{tabla}" '
                    f"WHERE time >= TIMESTAMP '{d.strftime(formato)}' AND time < TIMESTAMP '{h.strftime(formato)}'"
                    f"{filtro} ORDER BY time",
                    params,
                )
                if not filas:
                    continue
                datos = {"time": [int(f["time_ns"]) for f in filas]}
                for c in etiquetas:  # el JSON del motor omite los nulos: quedan como None
                    datos[c] = [f.get(c) for f in filas]
                datos[COLUMNA_VALOR] = [f.get(COLUMNA_VALOR) for f in filas]
                if escritor is None:
                    escritor = pq.ParquetWriter(ruta, esquema)
                escritor.write_table(pa.table(datos, schema=esquema))
                total += len(filas)
        finally:
            if escritor is not None:
                escritor.close()
        if total:
            manifiesto["tablas"][tabla] = total
            print(f"  {tabla:45s} {total:8d} filas")

    activos = [activo] if activo else []
    if not activos and ruta_catalogo and Path(ruta_catalogo).exists():
        activos = sorted(set(json.loads(Path(ruta_catalogo).read_text()).values()))
    with open(salida / "huecos.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["activo", "metrica", "desde", "hasta", "segundos"])
        for a in activos:
            _, encontrados = saltos(cliente, a, "system_cpu_utilization", desde, hasta)
            for ini, fin in encontrados:
                w.writerow([a, "system_cpu_utilization",
                            datetime.fromtimestamp(ini, timezone.utc).isoformat(),
                            datetime.fromtimestamp(fin, timezone.utc).isoformat(), fin - ini])

    n_iny = 0
    if conexion is not None:
        with conexion.cursor() as cur, open(salida / "inyecciones.csv", "w", newline="") as f:
            # una inyeccion que empezo antes del rango pero termina dentro tambien cuenta
            cur.execute(
                "SELECT id, uid, tipo, nodo, activo, inicio, fin, intensidad, parametros, estado, causa_en, manifiesta_en "
                "FROM inyecciones WHERE inicio < %(hasta)s AND COALESCE(fin, 'infinity') >= %(desde)s "
                + ("AND activo = %(activo)s " if activo else "") + "ORDER BY inicio",
                {"desde": desde, "hasta": hasta, "activo": activo},
            )
            w = csv.writer(f)
            w.writerow([d.name for d in cur.description])
            for fila in cur:
                w.writerow([json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list))
                            else (v.isoformat() if hasattr(v, "isoformat") else v) for v in fila])
                n_iny += 1
    manifiesto["inyecciones"] = n_iny if conexion is not None else None
    (salida / "manifiesto.json").write_text(json.dumps(manifiesto, indent=2, ensure_ascii=False))
    print(f"exportadas {len(manifiesto['tablas'])} tablas y {n_iny} inyecciones en {salida}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m zenit.admin")
    sub = p.add_subparsers(dest="orden", required=True)
    sub.add_parser("crear-bases", help="crea zenit_raw, zenit_1m y zenit_1h con su retencion")
    v = sub.add_parser("verificar", help="comprobaciones de esquema de H-005")
    v.add_argument("--horas", type=int, default=24)
    h = sub.add_parser("huecos", help="intervalos de 10 s sin datos de un activo")
    h.add_argument("activo")
    h.add_argument("--minutos", type=int, default=30)
    h.add_argument("--metrica", default="system_cpu_utilization")
    c = sub.add_parser("cargar-verdad", help="verdad de referencia de los registros a PostgreSQL")
    c.add_argument("--carpeta", default=os.environ.get("RUTA_SALIDA", "/datos/salida"))
    c.add_argument("--cada", type=int, default=0, help="segundos entre pasadas; 0 = una sola")
    c.add_argument("--crudo", type=Path, help="verdad.jsonl copiado de un nodo, para reparar lo que no llego")
    c.add_argument("--activo", default="", help="con --crudo: zenit_asset_id de ese nodo")
    e = sub.add_parser("exportar", help="metricas, inyecciones y huecos de un rango, para trabajar sin la plataforma")
    e.add_argument("--desde", required=True, type=_fecha_arg)
    e.add_argument("--hasta", required=True, type=_fecha_arg)
    e.add_argument("--salida", required=True, type=Path)
    e.add_argument("--activo", default="")
    args = p.parse_args(argv)

    if args.orden == "cargar-verdad":
        if args.crudo and args.cada:
            print("--crudo es una sola pasada; no se combina con --cada", file=sys.stderr)
            return 2
        return cargar_verdad(Path(args.carpeta), args.cada, args.crudo, args.activo)
    cliente = _cliente()
    try:
        if args.orden == "exportar":
            if args.hasta <= args.desde:
                print("--hasta tiene que ser posterior a --desde", file=sys.stderr)
                return 2
            conexion = _conexion_pg() if os.environ.get("PG_URL") else None
            try:
                return exportar(cliente, args.desde, args.hasta, args.salida, args.activo,
                                os.environ.get("RUTA_CATALOGO", ""), conexion)
            finally:
                if conexion is not None:
                    conexion.close()
        if args.orden == "crear-bases":
            return crear_bases(cliente)
        if args.orden == "verificar":
            return verificar(cliente, args.horas)
        return huecos(cliente, args.activo, args.minutos, args.metrica)
    except ErrorInflux as e:
        print(f"error del almacen: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
