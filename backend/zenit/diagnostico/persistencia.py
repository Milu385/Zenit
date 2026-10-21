"""Guardar un diagnostico con la evidencia que se uso (H-039, tercer criterio).

Lo llama H-040 cuando el modelo responde. La evidencia recuperada queda en la
misma fila que la hipotesis, para que cada cita se pueda comprobar despues
contra lo que el modelo vio, aunque la telemetria ya haya rotado.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


def _jsonb(valor):
    from psycopg.types.json import Jsonb

    return Jsonb(valor)


def guardar_diagnostico(conexion, anomalia_id: int, *, texto: str, evidencia_citada: list,
                        modelo: str, tokens_entrada: int, tokens_salida: int,
                        evidencia_recuperada: dict, generado: Optional[datetime] = None) -> int:
    """Inserta el diagnostico y pasa la anomalia de `detectada` a `diagnosticada`.

    Solo cambia el estado si seguia en `detectada`: una anomalia ya confirmada,
    descartada o cerrada conserva su estado aunque reciba una hipotesis nueva.
    Devuelve el id del diagnostico.
    """
    generado = generado or datetime.now(timezone.utc)
    with conexion.cursor() as cur:
        cur.execute(
            "INSERT INTO diagnosticos (anomalia_id, texto, evidencia_citada, modelo, tokens_entrada, "
            "tokens_salida, generado, evidencia_recuperada) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (anomalia_id, texto, _jsonb(evidencia_citada), modelo, int(tokens_entrada), int(tokens_salida),
             generado, _jsonb(evidencia_recuperada)),
        )
        (id_,) = cur.fetchone()
        cur.execute("UPDATE anomalias SET estado = 'diagnosticada' WHERE id = %s AND estado = 'detectada'",
                    (anomalia_id,))
    conexion.commit()
    return id_


def evidencia_guardada(conexion, anomalia_id: int) -> Optional[dict]:
    """La evidencia que se uso en el diagnostico mas reciente, o None."""
    with conexion.cursor() as cur:
        cur.execute("SELECT evidencia_recuperada FROM diagnosticos WHERE anomalia_id = %s "
                    "ORDER BY generado DESC, id DESC LIMIT 1", (anomalia_id,))
        fila = cur.fetchone()
    return fila[0] if fila else None
