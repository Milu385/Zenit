"""Version del modelo y consumo de tokens (H-042, RNF-REP-02).

Cada diagnostico guarda con que modelo se genero y cuantos tokens uso
(`diagnosticos.modelo`, `tokens_entrada`, `tokens_salida`, `generado`). Aqui
se resume eso por dia (UTC) y por modelo, para que el administrador vea cuanto
se gasta y con que version. Lo sirve `GET /api/diagnostico/consumo`.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

DIAS_POR_DEFECTO = 30
DIAS_MAXIMOS = 366


def periodo(desde: Optional[date], hasta: Optional[date], hoy: Optional[date] = None) -> tuple[date, date]:
    """Sin fechas, los ultimos 30 dias contando hoy. Lanza ValueError si el
    periodo esta al reves o pasa de un ano."""
    hoy = hoy or datetime.now(timezone.utc).date()
    hasta = hasta or hoy
    desde = desde or hasta - timedelta(days=DIAS_POR_DEFECTO - 1)
    if desde > hasta:
        raise ValueError("desde no puede ser posterior a hasta")
    if (hasta - desde).days + 1 > DIAS_MAXIMOS:
        raise ValueError(f"el periodo pasa de {DIAS_MAXIMOS} dias")
    return desde, hasta


def leer(conexion, desde: date, hasta: date) -> list[tuple]:
    """[(dia, modelo, diagnosticos, tokens_entrada, tokens_salida)] del periodo, ambos dias incluidos."""
    inicio = datetime.combine(desde, time.min, timezone.utc)
    fin = datetime.combine(hasta + timedelta(days=1), time.min, timezone.utc)
    with conexion.cursor() as cur:
        cur.execute(
            "SELECT (generado AT TIME ZONE 'UTC')::date AS dia, modelo, count(*), "
            "coalesce(sum(tokens_entrada), 0), coalesce(sum(tokens_salida), 0) "
            "FROM diagnosticos WHERE generado >= %s AND generado < %s "
            "GROUP BY 1, 2 ORDER BY 1, 2",
            (inicio, fin),
        )
        return cur.fetchall()


def resumir(filas: list[tuple], desde: date, hasta: date, configurado: Optional[str]) -> dict:
    """La respuesta de la API: por dia y modelo, y los totales por modelo."""
    por_dia = []
    totales: dict = {}
    for dia, modelo, n, t_in, t_out in filas:
        por_dia.append({"dia": dia.isoformat() if hasattr(dia, "isoformat") else str(dia), "modelo": modelo,
                        "diagnosticos": int(n), "tokens_entrada": int(t_in), "tokens_salida": int(t_out)})
        t = totales.setdefault(modelo, {"modelo": modelo, "diagnosticos": 0, "tokens_entrada": 0, "tokens_salida": 0})
        t["diagnosticos"] += int(n)
        t["tokens_entrada"] += int(t_in)
        t["tokens_salida"] += int(t_out)
    return {
        "periodo": {"desde": desde.isoformat(), "hasta": hasta.isoformat()},
        "modelo_configurado": configurado,
        "por_dia": por_dia,
        "por_modelo": sorted(totales.values(), key=lambda t: (-t["diagnosticos"], t["modelo"])),
    }
