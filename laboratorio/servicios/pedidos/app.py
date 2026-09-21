import os
import random
import httpx
from fastapi import FastAPI, HTTPException

CATALOGO = os.environ.get("URL_CATALOGO", "http://catalogo:8000")

app = FastAPI()
cliente = httpx.AsyncClient(timeout=float(os.environ.get("TIEMPO_LIMITE", "5.0")))


@app.get("/salud")
async def salud():
    return {"estado": "ok", "entorno": os.environ.get("ZENIT_ENTORNO", "desconocido")}


@app.post("/pedidos")
async def crear():
    articulo = random.randint(1, 200)
    # esta llamada NO es decorativa: es donde se manifiesta la latencia inyectada
    r = await cliente.get(f"{CATALOGO}/articulos/{articulo}")
    if r.status_code != 200:
        raise HTTPException(502, "catalogo no respondio")

    reserva = await cliente.post(f"{CATALOGO}/articulos/{articulo}/reservar")
    return {
        "articulo": r.json(),
        "reservado": reserva.status_code == 200,
    }
