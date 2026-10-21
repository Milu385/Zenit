"""Lectura de incidentes para la API (H-038, contrato v1.2, seccion 3).

Un incidente es una fila de `anomalias` (la escribe el detector, H-032) con
su diagnostico mas reciente de `diagnosticos`, si existe. Este modulo solo
lee, salvo `marcar`: abrir y cerrar es del detector, y confirmar o descartar
lo hace el operador (H-037).

La evidencia se devuelve siempre, haya o no diagnostico, y con la forma del
contrato. El detector guarda la ventana como minutos de linea base; aqui se
traduce a un intervalo con `desde` y `hasta`, que es lo que la interfaz usa
para pintar la serie. El valor original queda en `ventana_minutos`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

ESTADOS = ("detectada", "diagnosticada", "confirmada", "falso_positivo", "cerrada")
SEVERIDADES = ("alta", "media", "baja")
MANUALES = ("confirmada", "falso_positivo")
# los mismos que excluye el detector al cerrar (deteccion/cierre.py)
TERMINALES = ("cerrada", "falso_positivo")
TERMINAL = object()   # respuesta de `marcar` cuando el incidente ya no admite cambios
LIMITE = 500

_COLUMNAS = ("id", "activo", "metrica", "dimensiones", "inicio", "fin", "estado",
             "severidad", "puntaje", "evidencia", "version_modelo", "marcada_por", "marcada_en")

# alta primero; una severidad desconocida va al final en vez de romper el orden
_ORDEN = ("ORDER BY CASE a.severidad WHEN 'alta' THEN 0 WHEN 'media' THEN 1 "
          "WHEN 'baja' THEN 2 ELSE 3 END, a.inicio DESC, a.id DESC")


def _utc(v: Optional[datetime]) -> Optional[datetime]:
    if v is None:
        return None
    return v.replace(tzinfo=timezone.utc) if v.tzinfo is None else v.astimezone(timezone.utc)


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def listar(conexion, *, estado: Optional[str] = None, activo: Optional[str] = None,
           desde: Optional[datetime] = None, hasta: Optional[datetime] = None,
           limite: int = LIMITE) -> list[dict]:
    """Incidentes ordenados por severidad y luego por inicio, el mas reciente primero.

    El rango selecciona los incidentes que se cruzan con [desde, hasta]: los
    que empezaron antes de `hasta` y no habian terminado en `desde`. Asi un
    incidente que sigue abierto aparece en cualquier rango posterior a su inicio.
    """
    condiciones, valores = [], []
    if estado:
        condiciones.append("a.estado = %s")
        valores.append(estado)
    if activo:
        condiciones.append("a.activo = %s")
        valores.append(activo)
    if hasta is not None:
        condiciones.append("a.inicio <= %s")
        valores.append(_utc(hasta))
    if desde is not None:
        condiciones.append("(a.fin IS NULL OR a.fin >= %s)")
        valores.append(_utc(desde))
    donde = f"WHERE {' AND '.join(condiciones)} " if condiciones else ""
    columnas = ", ".join(f"a.{c}" for c in _COLUMNAS)
    sql = (f"SELECT {columnas}, EXISTS (SELECT 1 FROM diagnosticos d WHERE d.anomalia_id = a.id) "
           f"FROM anomalias a {donde}{_ORDEN} LIMIT %s")
    with conexion.cursor() as cur:
        cur.execute(sql, (*valores, limite))
        filas = cur.fetchall()
    return [resumen({**dict(zip(_COLUMNAS, f[:-1])), "tiene_diagnostico": bool(f[-1])}) for f in filas]


def anomalia(conexion, id_: int) -> Optional[dict]:
    """La fila cruda de `anomalias`, con fechas como datetime, o None si no existe."""
    with conexion.cursor() as cur:
        cur.execute(f"SELECT {', '.join(_COLUMNAS)} FROM anomalias WHERE id = %s", (id_,))
        fila = cur.fetchone()
    return dict(zip(_COLUMNAS, fila)) if fila else None


def obtener(conexion, id_: int, ahora: Optional[datetime] = None) -> Optional[dict]:
    """El incidente con su evidencia y su diagnostico mas reciente, o None si no existe."""
    with conexion.cursor() as cur:
        cur.execute(f"SELECT {', '.join(_COLUMNAS)} FROM anomalias WHERE id = %s", (id_,))
        fila = cur.fetchone()
        if fila is None:
            return None
        cur.execute("SELECT texto, evidencia_citada, modelo, tokens_entrada, tokens_salida, generado "
                    "FROM diagnosticos WHERE anomalia_id = %s ORDER BY generado DESC, id DESC LIMIT 1", (id_,))
        diag = cur.fetchone()
    anomalia = dict(zip(_COLUMNAS, fila))
    salida = resumen({**anomalia, "tiene_diagnostico": diag is not None})
    salida["evidencia"] = evidencia_publica(anomalia, ahora)
    salida["version_modelo"] = anomalia["version_modelo"]
    salida["marcada_por"] = anomalia["marcada_por"]
    salida["marcada_en"] = _iso(anomalia["marcada_en"])
    salida["diagnostico"] = None
    if diag is not None:
        texto, citada, modelo, t_in, t_out, generado = diag
        salida["diagnostico"] = {
            "texto": texto, "evidencia_citada": citada or [], "modelo": modelo,
            "tokens_entrada": t_in, "tokens_salida": t_out, "generado": _iso(generado),
        }
    return salida


def marcar(conexion, id_: int, estado: str, usuario: str, ahora: Optional[datetime] = None):
    """Confirma o descarta un incidente y guarda quien y cuando (H-037).

    Devuelve el incidente con la forma de la lista; None si no existe;
    TERMINAL si ya esta cerrado o descartado. La condicion va en el mismo
    UPDATE, como en el cierre del detector, para que ninguno pise al otro si
    coinciden.
    """
    if estado not in MANUALES:
        raise ValueError(f"estado manual desconocido {estado!r}")
    ahora = _utc(ahora) or datetime.now(timezone.utc)
    columnas = ", ".join(_COLUMNAS)
    with conexion.cursor() as cur:
        cur.execute(
            "UPDATE anomalias SET estado = %s, marcada_por = %s, marcada_en = %s "
            "WHERE id = %s AND estado <> ALL(%s) "
            f"RETURNING {columnas}, EXISTS (SELECT 1 FROM diagnosticos d WHERE d.anomalia_id = anomalias.id)",
            (estado, usuario, ahora, id_, list(TERMINALES)),
        )
        fila = cur.fetchone()
        if fila is None:
            cur.execute("SELECT 1 FROM anomalias WHERE id = %s", (id_,))
            existe = cur.fetchone() is not None
    conexion.commit()
    if fila is None:
        return TERMINAL if existe else None
    salida = resumen({**dict(zip(_COLUMNAS, fila[:-1])), "tiene_diagnostico": bool(fila[-1])})
    salida["marcada_por"] = usuario
    salida["marcada_en"] = _iso(ahora)
    return salida


def resumen(a: dict) -> dict:
    """Un elemento de `GET /api/incidentes`, con la forma del contrato."""
    return {
        "id": a["id"],
        "activo": a["activo"],
        "metrica": a["metrica"],
        "dimensiones": a["dimensiones"] or {},
        "inicio": _iso(a["inicio"]),
        "fin": _iso(a["fin"]),
        "estado": a["estado"],
        "severidad": a["severidad"],
        "puntaje": a["puntaje"],
        "tiene_diagnostico": bool(a.get("tiene_diagnostico")),
    }


def evidencia_publica(a: dict, ahora: Optional[datetime] = None) -> dict:
    """La evidencia del detector con la forma del contrato.

    - `ventana` pasa a ser {desde, hasta}: desde = inicio menos la ventana de
      linea base; hasta = fin, o ahora si sigue abierta.
    - `metricas_relacionadas` existe siempre: las senales del detector que
      miran el mismo tipo de fallo (H-039). La lista completa, con registros y
      trazas, esta en `GET /api/incidentes/{id}/evidencia`.
    - El resto de lo que guardo el detector se conserva tal cual.
    """
    cruda = dict(a.get("evidencia") or {})
    inicio = _utc(a["inicio"])
    fin = _utc(a.get("fin")) or _utc(ahora) or datetime.now(timezone.utc)
    ventana = cruda.get("ventana")
    if isinstance(ventana, dict) and "desde" in ventana and "hasta" in ventana:
        intervalo = ventana
    else:
        minutos = ventana if isinstance(ventana, (int, float)) and ventana > 0 else 60
        cruda["ventana_minutos"] = minutos
        intervalo = {"desde": _iso(inicio - timedelta(minutes=minutos)), "hasta": _iso(max(fin, inicio))}
    cruda["ventana"] = intervalo
    cruda.setdefault("metricas_relacionadas", [])
    cruda.setdefault("valor_observado", None)
    cruda.setdefault("valor_esperado", None)
    cruda.setdefault("metodo", a.get("version_modelo"))
    if not cruda.get("metricas_relacionadas") and a.get("metrica"):
        from zenit.diagnostico.evidencia import metricas_relacionadas

        cruda["metricas_relacionadas"] = metricas_relacionadas(a["metrica"], a.get("dimensiones") or {})
    return cruda
