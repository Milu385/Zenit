"""Linea de ordenes del inyector.

    python -m inyector campana                     corre la campana (lo que hace el contenedor)
    python -m inyector plan --n 10                 muestra las proximas inyecciones sin ejecutar nada
    python -m inyector manual cpu --carga 80 --duracion 300
    python -m inyector manual memoria --objetivo 0.75 --duracion 300
    python -m inyector manual disco --modo gradual --objetivo 0.8 --duracion 600
    python -m inyector manual caida --servicio catalogo --duracion 180
    python -m inyector abiertas                    inyecciones sin cerrar
    python -m inyector recuperar                   cierra las abiertas y restaura el nodo
"""
import argparse
import json
import signal
import sys
import threading

from .campana import Config, Ocupado, calendario, candado_del_nodo, correr, ejecutar, estado_campana, fabricar, recuperar, _fecha
from .verdad import Registro, iso


def _avisar(texto: str) -> None:
    print(texto, flush=True)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m inyector")
    sub = p.add_subparsers(dest="orden", required=True)
    sub.add_parser("campana")
    pl = sub.add_parser("plan")
    pl.add_argument("--n", type=int, default=10)
    sub.add_parser("abiertas")
    sub.add_parser("recuperar")
    m = sub.add_parser("manual")
    m.add_argument("tipo", choices=["cpu", "memoria", "disco", "caida"])
    m.add_argument("--duracion", type=int, default=300, help="segundos")
    m.add_argument("--carga", type=int, default=80, help="cpu: porcentaje por nucleo")
    m.add_argument("--objetivo", type=float, default=0.75, help="memoria y disco: fraccion ocupada al final")
    m.add_argument("--modo", choices=["gradual", "abrupto"], default="gradual", help="disco")
    m.add_argument("--servicio", default="catalogo", help="caida")
    args = p.parse_args(argv)

    cfg = Config.desde_entorno()
    registro = Registro(cfg.ruta_verdad, cfg.nodo)
    parar = threading.Event()
    for senal in (signal.SIGTERM, signal.SIGINT):
        signal.signal(senal, lambda *_: parar.set())

    if args.orden == "campana":
        correr(cfg, parar, avisar=_avisar)
        return 0
    if args.orden == "plan":
        est = estado_campana(cfg, crear=False)
        plan = cfg.con_guardados(est).validar()
        for spec, _ in zip(calendario(plan, _fecha(est["inicio"]), est["campana"]), range(args.n)):
            spec = {**spec, "momento": iso(spec["momento"])}
            print(json.dumps(spec, ensure_ascii=False))
        return 0
    if args.orden == "abiertas":
        for e in registro.abiertas():
            print(json.dumps(e, ensure_ascii=False))
        return 0
    if args.orden == "recuperar":
        with candado_del_nodo(cfg.ruta_verdad, esperar=True):
            recuperar(registro, cfg, avisar=_avisar)
        return 0

    spec = {"tipo": args.tipo, "duracion_s": args.duracion, "carga": args.carga,
            "objetivo": args.objetivo, "modo": args.modo, "servicio": args.servicio, "campana": "manual"}
    try:
        with candado_del_nodo(cfg.ruta_verdad, esperar=False):
            _, estado, _ = ejecutar(fabricar(spec, cfg), registro, parar, _avisar)
    except Ocupado as e:
        print(f"{e}; espera a que termine o detén la campaña", file=sys.stderr)
        return 3
    return 0 if estado == "completada" else 1


if __name__ == "__main__":
    sys.exit(main())
