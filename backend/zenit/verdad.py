"""Cargador de la verdad de referencia (H-024), del lado de la plataforma.

El inyector de cada nodo escribe un JSONL local; el agente lo envia como
registros OTLP; la ingesta guarda los registros en /datos/salida con las
etiquetas canonicas. Este modulo lee esos archivos y deja una fila por
inyeccion en la tabla `inyecciones` de PostgreSQL.

Cada pasada relee todo y hace upsert por uid, asi que es idempotente: no
importa si una linea llego dos veces, ni en que orden llegaron el inicio y el
fin (el agente envia con varios consumidores y el orden no esta garantizado).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator, Optional

from zenit.esquema import SIN_RESOLVER

log = logging.getLogger("zenit.verdad")

TIPOS = ("cpu", "memoria", "disco", "caida", "latencia")
ESTADOS = ("en_curso", "completada", "fallida")


@dataclass
class Inyeccion:
    uid: str
    tipo: str
    nodo: str
    activo: Optional[str]
    inicio: datetime
    fin: Optional[datetime]
    intensidad: str
    parametros: dict
    estado: str
    causa_en: Optional[str] = None
    manifiesta_en: Optional[str] = None


def _fecha(texto) -> datetime:
    momento = datetime.fromisoformat(str(texto).replace("Z", "+00:00"))
    if momento.tzinfo is None:
        raise ValueError(f"fecha sin zona horaria: {texto}")
    return momento


def leer_eventos(carpeta: Path, patron: str = "registros-*.jsonl") -> Iterator[dict]:
    """Eventos de verdad dentro de los registros que guardo la ingesta.

    Cada registro de la ingesta trae las etiquetas canonicas y el cuerpo del
    log. El cuerpo es la linea que escribio el inyector, como texto. Los
    registros que no son de verdad de referencia se ignoran.
    """
    for archivo in sorted(Path(carpeta).glob(patron)):
        with archivo.open(encoding="utf-8") as f:
            for linea in f:
                try:
                    registro = json.loads(linea)
                    cuerpo = registro.get("cuerpo")
                    evento = json.loads(cuerpo) if isinstance(cuerpo, str) else cuerpo
                except (json.JSONDecodeError, AttributeError, TypeError):
                    continue
                if not isinstance(evento, dict) or "uid" not in evento or evento.get("evento") not in ("inicio", "fin"):
                    continue
                activo = registro.get("zenit_asset_id")
                evento["_activo"] = None if activo in (None, "", SIN_RESOLVER) else activo
                yield evento


def leer_crudo(archivo: Path, activo: Optional[str]) -> Iterator[dict]:
    """Eventos del verdad.jsonl de un nodo, copiado a mano.

    Es el camino de reparacion: si una linea no llego por el agente (la
    plataforma estuvo caida mas de lo que aguanto la cola, un disco lleno),
    el archivo del nodo sigue teniendo todo y se puede cargar directo.
    """
    with Path(archivo).open(encoding="utf-8") as f:
        for linea in f:
            try:
                evento = json.loads(linea)
            except json.JSONDecodeError:
                continue
            if isinstance(evento, dict) and "uid" in evento and evento.get("evento") in ("inicio", "fin"):
                evento["_activo"] = activo or None
                yield evento


def combinar(eventos: Iterable[dict]) -> tuple[list[Inyeccion], int]:
    """Una fila por uid. Devuelve (filas, eventos descartados por invalidos)."""
    inicios: dict = {}
    fines: dict = {}
    descartados = 0
    for e in eventos:
        (inicios if e["evento"] == "inicio" else fines)[e["uid"]] = e
    filas = []
    for uid, ini in inicios.items():
        fin = fines.get(uid)
        try:
            if ini.get("tipo") not in TIPOS:
                raise ValueError(f"tipo {ini.get('tipo')!r}")
            parametros = dict(ini.get("parametros") or {})
            estado = "en_curso"
            momento_fin = None
            if fin is not None:
                estado = fin.get("estado")
                if estado not in ("completada", "fallida"):
                    raise ValueError(f"estado {estado!r}")
                momento_fin = _fecha(fin["fin"])
                if fin.get("motivo"):
                    parametros["motivo"] = fin["motivo"]
                if fin.get("cierre"):
                    parametros["cierre"] = fin["cierre"]
            filas.append(Inyeccion(
                uid=uid, tipo=ini["tipo"], nodo=str(ini["nodo"]), activo=ini.get("_activo"),
                inicio=_fecha(ini["inicio"]), fin=momento_fin, intensidad=str(ini.get("intensidad", "")),
                parametros=parametros, estado=estado,
                causa_en=ini.get("causa_en"), manifiesta_en=ini.get("manifiesta_en"),
            ))
        except (KeyError, ValueError, TypeError) as ex:
            descartados += 1
            log.warning("evento de verdad invalido %s: %s", uid, ex)
    huerfanos = set(fines) - set(inicios)
    if huerfanos:  # el fin llego y el inicio todavia no: se cargan en la siguiente pasada
        log.info("%d fines sin su inicio todavia", len(huerfanos))
    return filas, descartados


# Un estado final no vuelve a en_curso, y una fila igual no se reescribe.
_UPSERT = """
INSERT INTO inyecciones (uid, tipo, nodo, activo, inicio, fin, intensidad, parametros, estado, causa_en, manifiesta_en)
VALUES (%(uid)s, %(tipo)s, %(nodo)s, %(activo)s, %(inicio)s, %(fin)s, %(intensidad)s, %(parametros)s, %(estado)s,
        %(causa_en)s, %(manifiesta_en)s)
ON CONFLICT (uid) DO UPDATE SET
  activo     = COALESCE(EXCLUDED.activo, inyecciones.activo),
  fin        = COALESCE(EXCLUDED.fin, inyecciones.fin),
  parametros = inyecciones.parametros || EXCLUDED.parametros,
  estado     = CASE WHEN inyecciones.estado <> 'en_curso' AND EXCLUDED.estado = 'en_curso'
                    THEN inyecciones.estado ELSE EXCLUDED.estado END
WHERE (inyecciones.activo, inyecciones.fin, inyecciones.parametros, inyecciones.estado)
      IS DISTINCT FROM (COALESCE(EXCLUDED.activo, inyecciones.activo), COALESCE(EXCLUDED.fin, inyecciones.fin),
                        inyecciones.parametros || EXCLUDED.parametros, EXCLUDED.estado)
"""


def cargar(conexion, filas: list[Inyeccion]) -> int:
    """Upsert por uid. Devuelve cuantas filas cambiaron."""
    from psycopg.types.json import Jsonb

    cambiadas = 0
    with conexion.cursor() as cur:
        for f in filas:
            cur.execute(_UPSERT, {**f.__dict__, "parametros": Jsonb(f.parametros)})
            cambiadas += cur.rowcount
    conexion.commit()
    return cambiadas


def pasada(conexion, carpeta: Path, crudo: Optional[Path] = None,
           activo: Optional[str] = None) -> tuple[int, int, int]:
    """Una lectura completa y su carga. Devuelve (filas, cambiadas, descartadas)."""
    eventos = leer_crudo(crudo, activo) if crudo else leer_eventos(carpeta)
    filas, descartados = combinar(eventos)
    return len(filas), cargar(conexion, filas), descartados
