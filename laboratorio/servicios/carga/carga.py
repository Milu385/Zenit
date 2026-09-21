import asyncio
import os
import time
import httpx

DESTINO = os.environ.get("URL_PEDIDOS", "http://pedidos:8000/pedidos")
RPS = float(os.environ.get("CARGA_RPS", "5"))
CONCURRENCIA = int(os.environ.get("CARGA_CONCURRENCIA", "4"))
DURACION = float(os.environ.get("CARGA_DURACION", "0"))  # 0 = indefinida


async def trabajador(cliente, intervalo, fin):
    while fin == 0 or time.monotonic() < fin:
        inicio = time.monotonic()
        try:
            r = await cliente.post(DESTINO)
            print(f"{r.status_code} {(time.monotonic()-inicio)*1000:.0f}ms", flush=True)
        except Exception as e:
            print(f"error {type(e).__name__}", flush=True)
        espera = intervalo - (time.monotonic() - inicio)
        if espera > 0:
            await asyncio.sleep(espera)


async def principal():
    intervalo = CONCURRENCIA / RPS
    fin = time.monotonic() + DURACION if DURACION > 0 else 0
    async with httpx.AsyncClient(timeout=10.0) as cliente:
        await asyncio.gather(*[
            trabajador(cliente, intervalo, fin) for _ in range(CONCURRENCIA)
        ])


asyncio.run(principal())
