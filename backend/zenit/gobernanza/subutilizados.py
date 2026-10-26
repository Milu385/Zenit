"""Activos subutilizados y tendencia del gasto (H-047, pantalla 6).

Fuera del contrato v1.2 (NOTAS.md). Cruza lo que cuesta cada activo (los
cargos atribuidos de costos.py) con lo que usa (CPU y memoria del resumen por
hora, H-019) en el mismo periodo.

Un activo queda marcado como subutilizado si cumple TODO el criterio:
    - tuvo costo en el periodo;
    - hay al menos HORAS_MINIMAS horas de uso medidas (con menos no se dice nada);
    - su CPU promedio esta por debajo de CPU_PROMEDIO_MAX;
    - ni su hora de mas CPU llego a CPU_PICO_MAX (un activo que solo trabaja
      en picos no esta de sobra);
    - su memoria promedio esta por debajo de MEMORIA_PROMEDIO_MAX.

El criterio viaja en la respuesta para que la pantalla lo muestre tal cual.
Es un dato para revisar, no una recomendacion: no se sugiere apagar ni
redimensionar nada.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Optional

from zenit.esquema import SIN_RESOLVER

from .costos import Cargo

CPU_PROMEDIO_MAX = 0.10
CPU_PICO_MAX = 0.40
MEMORIA_PROMEDIO_MAX = 0.40
HORAS_MINIMAS = 24
# La telemetria huerfana se guarda con zenit_asset_id = sin_resolver: no es un
# activo del catalogo y no tiene costo propio, asi que no va en la tabla.
NO_ES_ACTIVO = {SIN_RESOLVER}


def criterio() -> dict:
    return {"cpu_promedio_max": CPU_PROMEDIO_MAX, "cpu_pico_max": CPU_PICO_MAX,
            "memoria_promedio_max": MEMORIA_PROMEDIO_MAX, "horas_minimas": HORAS_MINIMAS}


def _dinero(d: Decimal) -> float:
    return float(d.quantize(Decimal("0.01")))


def _promedio(xs: list) -> Optional[float]:
    return round(sum(xs) / len(xs), 4) if xs else None


def evaluar(costo: float, cpu: list, memoria: list) -> tuple[Optional[bool], str]:
    """(subutilizado, motivo). None si no hay datos suficientes para decirlo."""
    horas = min(len(cpu), len(memoria)) if memoria else len(cpu)
    if costo <= 0:
        return None, "sin costo en el periodo"
    if horas < HORAS_MINIMAS:
        return None, f"solo {horas} h de uso medidas; hacen falta {HORAS_MINIMAS}"
    cpu_prom, pico, mem = _promedio(cpu), max(cpu), _promedio(memoria)
    razones = []
    if cpu_prom >= CPU_PROMEDIO_MAX:
        razones.append(f"CPU promedio {cpu_prom:.0%}")
    if pico >= CPU_PICO_MAX:
        razones.append(f"pico de CPU {pico:.0%}")
    if mem is None:
        return None, "sin datos de memoria"
    if mem >= MEMORIA_PROMEDIO_MAX:
        razones.append(f"memoria promedio {mem:.0%}")
    if razones:
        return False, "usa recursos: " + ", ".join(razones)
    return True, (f"CPU promedio {cpu_prom:.0%} y pico {pico:.0%}, memoria promedio {mem:.0%}, "
                  f"en {horas} h medidas")


def _mes(d: date) -> str:
    return d.strftime("%Y-%m")


def analizar(cargos: list[Cargo], uso: dict, entornos: dict, desde: date, hasta: date) -> dict:
    """La respuesta de GET /api/gobernanza/costos/analisis.

    cargos: todos los importados; entran los que empiezan en [desde, hasta],
            igual que en el reporte de costos.
    uso: {activo: {"cpu": [...], "memoria": [...]}} de uso_por_hora.
    entornos: {activo: entorno} del catalogo.
    """
    dentro = [c for c in cargos if c.desde and desde <= c.desde <= hasta]
    costo: dict = defaultdict(Decimal)
    proveedor: dict = {}
    por_mes_activo: dict = defaultdict(lambda: defaultdict(Decimal))
    por_mes_entorno: dict = defaultdict(lambda: defaultdict(Decimal))
    meses = set()
    for c in dentro:
        mes = _mes(c.desde)
        meses.add(mes)
        entorno = entornos.get(c.activo, "sin asignar" if not c.activo else "desconocido")
        por_mes_entorno[entorno][mes] += c.monto
        if c.activo:
            costo[c.activo] += c.monto
            proveedor[c.activo] = c.proveedor
            por_mes_activo[c.activo][mes] += c.monto
    meses_orden = sorted(meses)

    activos = []
    for activo in sorted((set(costo) | set(uso)) - NO_ES_ACTIVO):
        u = uso.get(activo, {})
        cpu, memoria = u.get("cpu", []), u.get("memoria", [])
        monto = _dinero(costo.get(activo, Decimal(0)))
        sub, motivo = evaluar(monto, cpu, memoria)
        activos.append({
            "activo": activo, "entorno": entornos.get(activo, "desconocido"), "proveedor": proveedor.get(activo),
            "costo": monto, "horas_medidas": min(len(cpu), len(memoria)) if memoria else len(cpu),
            "cpu_promedio": _promedio(cpu), "cpu_pico": max(cpu) if cpu else None,
            "memoria_promedio": _promedio(memoria), "subutilizado": sub, "motivo": motivo,
        })
    activos.sort(key=lambda a: (a["subutilizado"] is not True, -a["costo"], a["activo"]))

    def serie(montos: dict) -> list:
        return [_dinero(montos.get(m, Decimal(0))) for m in meses_orden]

    return {
        "moneda": "USD",
        "periodo": {"desde": desde.isoformat(), "hasta": hasta.isoformat()},
        "criterio": criterio(),
        "activos": activos,
        "tendencia": {
            "meses": meses_orden,
            "por_entorno": sorted(({"entorno": e, "montos": serie(m)} for e, m in por_mes_entorno.items()),
                                  key=lambda x: (-sum(x["montos"]), x["entorno"])),
            "por_activo": sorted(({"activo": a, "entorno": entornos.get(a, "desconocido"), "montos": serie(m)}
                                  for a, m in por_mes_activo.items()),
                                 key=lambda x: (-sum(x["montos"]), x["activo"])),
        },
    }
