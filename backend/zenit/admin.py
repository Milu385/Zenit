"""Herramienta de administracion del almacen (H-005).

Se corre dentro del contenedor de la API, que ya tiene la red y el token:

    docker compose exec api python -m zenit.admin crear-bases
    docker compose exec api python -m zenit.admin verificar
    docker compose exec api python -m zenit.admin huecos activo-aws-nodo-01 --minutos 30

crear-bases es idempotente: si la base existe, solo ajusta su retencion.
"""
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

from zenit.esquema import COLUMNA_TIEMPO, COLUMNA_VALOR, ETIQUETAS_CANONICAS, es_identificador
from zenit.influx import ClienteInflux, ErrorInflux

# Niveles de resolucion y retencion. Documento de esquema de medicion,
# seccion 3. Se pueden sobrescribir por variable de entorno.
BASES = (
    ("zenit_raw", os.environ.get("RETENCION_RAW", "7d")),
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
    formato = "%Y-%m-%d %H:%M:%S"
    filas = cliente.consultar(
        "zenit_raw",
        f'SELECT DISTINCT CAST(time AS BIGINT) AS t FROM "{metrica}" '
        f"WHERE \"zenit_asset_id\" = $activo AND time >= TIMESTAMP '{desde.strftime(formato)}' "
        f"AND time < TIMESTAMP '{hasta.strftime(formato)}' ORDER BY t",
        {"activo": activo},
    )
    marcas = [int(f["t"]) // 1_000_000_000 for f in filas]
    limites = [int(desde.timestamp())] + marcas + [int(hasta.timestamp())]
    saltos = [(a, b) for a, b in zip(limites, limites[1:]) if b - a > 20]
    print(f"{metrica} de {activo}, {desde.strftime('%H:%M:%S')} a {hasta.strftime('%H:%M:%S')} UTC: "
          f"{len(marcas)} muestras, {len(saltos)} huecos de mas de 20 s")
    for a, b in saltos:
        ini = datetime.fromtimestamp(a, timezone.utc).strftime("%H:%M:%S")
        fin = datetime.fromtimestamp(b, timezone.utc).strftime("%H:%M:%S")
        print(f"  hueco de {b - a} s entre {ini} y {fin}")
    return 1 if saltos else 0


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
    args = p.parse_args(argv)

    cliente = _cliente()
    try:
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
