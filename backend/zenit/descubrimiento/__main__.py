"""Trabajador de descubrimiento.

    python -m zenit.descubrimiento --cada 900        lo corre el servicio descubridor
    python -m zenit.descubrimiento                   una pasada y termina
    python -m zenit.descubrimiento --probar          lista lo que ve cada proveedor, sin escribir nada

Cada pasada recorre los proveedores configurados uno por uno. El fallo de uno
queda registrado en su fila de `descubrimientos` y no detiene a los demas.
"""
import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone

from zenit import catalogo
from zenit.descubrimiento import proveedores_desde_entorno

log = logging.getLogger("zenit.descubrimiento")


def pasada(url: str, proveedores: list, despues=None) -> list:
    """Una ejecucion por proveedor. Devuelve [(proveedor, Resultado o texto de error)]."""
    salida = []
    for p in proveedores:
        with catalogo.conectar(url) as c:
            n = catalogo.abrir_ejecucion(c, p.nombre)
            try:
                recursos = p.listar()
                r = catalogo.reconciliar(c, p.nombre, recursos)
            except Exception as e:  # noqa: BLE001 - se registra y se sigue con el siguiente
                c.rollback()
                texto = f"{type(e).__name__}: {e}"
                catalogo.cerrar_ejecucion(c, n, error=texto)
                log.error("%s: %s", p.nombre, texto)
                salida.append((p.nombre, texto))
                continue
            catalogo.cerrar_ejecucion(c, n, r)
            log.info("%s: vistos=%d nuevos=%d retirados=%d reactivados=%d",
                     p.nombre, r.vistos, r.nuevos, r.retirados, r.reactivados)
            salida.append((p.nombre, r))
    if despues is not None:
        try:
            despues()
        except Exception:  # noqa: BLE001
            log.exception("fallo el paso posterior al descubrimiento")
    return salida


def principal(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    p = argparse.ArgumentParser(prog="python -m zenit.descubrimiento")
    p.add_argument("--cada", type=int, default=0, help="segundos entre pasadas; 0 = una sola")
    p.add_argument("--proveedor", action="append", help="solo este proveedor (repetible)")
    p.add_argument("--probar", action="store_true", help="solo listar, sin tocar el catalogo")
    args = p.parse_args(argv)

    proveedores = [x for x in proveedores_desde_entorno()
                   if not args.proveedor or x.nombre in args.proveedor]
    if not proveedores:
        print("ningun proveedor configurado: DO_TOKEN, PROXMOX_URL y PROXMOX_TOKEN, o DESCUBRIR_AWS=1",
              file=sys.stderr)
        return 2 if not args.cada else _esperar_para_siempre()

    if args.probar:
        codigo = 0
        for x in proveedores:
            try:
                recursos = x.listar()
            except Exception as e:  # noqa: BLE001
                print(f"{x.nombre}: ERROR {type(e).__name__}: {e}")
                codigo = 1
                continue
            print(f"{x.nombre}: {len(recursos)} recursos")
            for r in recursos:
                print(f"  {r.clave:40s} {r.nombre:25s} {r.region or '-':10s} {r.atributos.get('estado')}")
        return codigo

    url = os.environ.get("PG_URL")
    if not url:
        print("falta PG_URL", file=sys.stderr)
        return 2
    catalogo.esperar_pg(url)
    despues = None
    while True:
        try:
            pasada(url, proveedores, despues)
        except Exception:  # noqa: BLE001 - PostgreSQL caido: se intenta en la siguiente
            log.exception("pasada fallida")
        if not args.cada:
            return 0
        print(f"{datetime.now(timezone.utc):%H:%M:%S} siguiente pasada en {args.cada} s", flush=True)
        time.sleep(args.cada)


def _esperar_para_siempre() -> int:
    # Como servicio, sin proveedores no hay que reiniciar en bucle: se queda
    # quieto y lo dice una vez.
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    sys.exit(principal())
