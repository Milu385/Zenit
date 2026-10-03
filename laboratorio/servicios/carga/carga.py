"""Generador de carga sobre la aplicacion de referencia (H-023).

Lazo abierto: las peticiones salen segun la tasa del perfil, sin esperar a
que termine la anterior. Asi, cuando un fallo vuelve lenta a la aplicacion,
la carga no baja con el; si bajara, el fallo y la carga se moverian juntos y
el detector no podria distinguirlos. Un tope de peticiones en vuelo evita
acumular miles de conexiones durante una caida; las que no caben se cuentan.

Con CARGA_PERFIL apuntando a un archivo de laboratorio/perfiles la tasa sigue
ese perfil y cambia minuto a minuto. Sin perfil, CARGA_RPS es constante.
"""
import asyncio
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from perfil import Perfil

DESTINO = os.environ.get("URL_PEDIDOS", "http://pedidos:8000/pedidos")
RPS = float(os.environ.get("CARGA_RPS", "5"))
EN_VUELO = int(os.environ.get("CARGA_MAX_EN_VUELO", "50"))
DURACION = float(os.environ.get("CARGA_DURACION", "0"))  # 0 = indefinida
RUTA_PERFIL = os.environ.get("CARGA_PERFIL", "")
INFORME_S = 60


def tasa_actual(perfil):
    if perfil is None:
        return RPS
    return max(perfil.tasa(datetime.now(timezone.utc)), 0.05)


class Cuentas:
    def __init__(self):
        self.reiniciar()

    def reiniciar(self):
        self.ok, self.errores, self.sin_lugar, self.latencias = 0, 0, 0, []


async def peticion(cliente, cupo, cuentas):
    inicio = time.monotonic()
    try:
        r = await cliente.post(DESTINO)
        if r.status_code < 500:
            cuentas.ok += 1
        else:
            cuentas.errores += 1
    except Exception:
        cuentas.errores += 1
    finally:
        cuentas.latencias.append(time.monotonic() - inicio)
        cupo.release()


async def informar(perfil, cuentas):
    # un resumen por minuto, no una linea por peticion: el registro del contenedor no crece sin limite
    while True:
        await asyncio.sleep(INFORME_S)
        lat = sorted(cuentas.latencias)
        p50 = lat[len(lat) // 2] * 1000 if lat else 0
        print(f"tasa objetivo {tasa_actual(perfil):.2f}/s · ok {cuentas.ok} · errores {cuentas.errores} "
              f"· sin lugar {cuentas.sin_lugar} · p50 {p50:.0f} ms", flush=True)
        cuentas.reiniciar()


async def principal():
    perfil = Perfil.cargar(Path(RUTA_PERFIL)) if RUTA_PERFIL else None
    if perfil is not None:
        print(f"perfil {RUTA_PERFIL}: base {perfil.base_rps} rps, amplitud {perfil.amplitud():.1f} a 1", flush=True)
    fin = time.monotonic() + DURACION if DURACION > 0 else 0
    cupo = asyncio.Semaphore(EN_VUELO)
    cuentas = Cuentas()
    # el bucle de eventos solo guarda referencias debiles a las tareas: sin este
    # conjunto, una tarea en vuelo puede desaparecer y llevarse su lugar del cupo
    vivas = set()
    limites = httpx.Limits(max_connections=EN_VUELO, max_keepalive_connections=10)
    async with httpx.AsyncClient(timeout=10.0, limits=limites) as cliente:
        informe = asyncio.create_task(informar(perfil, cuentas))
        while fin == 0 or time.monotonic() < fin:
            # llegadas de Poisson: intervalos exponenciales con la tasa del momento
            await asyncio.sleep(random.expovariate(tasa_actual(perfil)))
            if cupo.locked():
                cuentas.sin_lugar += 1
                continue
            await cupo.acquire()
            tarea = asyncio.create_task(peticion(cliente, cupo, cuentas))
            vivas.add(tarea)
            tarea.add_done_callback(vivas.discard)
        informe.cancel()


if __name__ == "__main__":
    asyncio.run(principal())
