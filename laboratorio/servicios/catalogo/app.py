import os
import asyncpg
from fastapi import FastAPI, HTTPException

app = FastAPI()
_pool = None

# Existencias con que vuelve un articulo agotado (ver reservar).
REPOSICION = int(os.environ.get("CATALOGO_REPOSICION", "100"))

DSN = (f"postgresql://{os.environ['ALMACEN_USUARIO']}:{os.environ['ALMACEN_CLAVE']}"
       f"@{os.environ.get('ALMACEN_HOST', 'almacen')}:5432/{os.environ['ALMACEN_BD']}")


@app.on_event("startup")
async def arrancar():
    global _pool
    _pool = await asyncpg.create_pool(DSN, min_size=2, max_size=10)
    async with _pool.acquire() as c:
        await c.execute("""
            CREATE TABLE IF NOT EXISTS articulos (
                id SERIAL PRIMARY KEY,
                nombre TEXT NOT NULL,
                existencias INT NOT NULL DEFAULT 100
            )""")
        await c.execute("""
            INSERT INTO articulos (nombre)
            SELECT 'articulo-' || g FROM generate_series(1, 200) g
            WHERE NOT EXISTS (SELECT 1 FROM articulos)""")


@app.get("/salud")
async def salud():
    async with _pool.acquire() as c:
        await c.fetchval("SELECT 1")
    return {"estado": "ok"}


@app.get("/articulos/{articulo_id}")
async def leer(articulo_id: int):
    async with _pool.acquire() as c:
        fila = await c.fetchrow(
            "SELECT id, nombre, existencias FROM articulos WHERE id=$1", articulo_id)
    if fila is None:
        raise HTTPException(404, "no existe")
    return dict(fila)


@app.post("/articulos/{articulo_id}/reservar")
async def reservar(articulo_id: int):
    async with _pool.acquire() as c:
        fila = await c.fetchrow(
            """UPDATE articulos SET existencias = existencias - 1
               WHERE id=$1 AND existencias > 0
               RETURNING id, existencias""", articulo_id)
        if fila is None:
            # Reposicion: sin ella, a 5 peticiones por segundo las 20 000
            # unidades se agotan en una hora y desde ahi toda reserva responde
            # 409. El error dejaria de ser un evento y pasaria a ser el estado
            # normal del laboratorio. Con la reposicion, cada articulo da un
            # 409 cada REPOSICION reservas.
            await c.execute("UPDATE articulos SET existencias = $2 WHERE id = $1",
                            articulo_id, REPOSICION)
    if fila is None:
        raise HTTPException(409, "sin existencias")
    return dict(fila)
