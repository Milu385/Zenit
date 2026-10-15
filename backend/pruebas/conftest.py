import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "ingesta"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

ESQUEMAS_PG = sorted((BACKEND.parent / "deploy" / "postgres").glob("*.sql"))


def preparar_pg(url: str):
    """Base de pruebas vacia con todo deploy/postgres/*.sql aplicado, en orden."""
    import psycopg

    conexion = psycopg.connect(url)
    with conexion.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        for archivo in ESQUEMAS_PG:
            cur.execute(archivo.read_text())
    conexion.commit()
    return conexion
