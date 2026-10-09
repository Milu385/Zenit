"""Catalogo canonico de activos en PostgreSQL (H-009a, H-010, H-011).

Una sola fuente de verdad para la identidad: el descubrimiento escribe aqui,
la ingesta resuelve contra lo que hay aqui y la API lo lista. Las tablas estan
en deploy/postgres/02-catalogo.sql.

Decisiones que estan en docs/Decisiones.md y no se cambian a la ligera:

- El identificador canonico se ASIGNA una vez y se guarda (activo-do-nodo-01);
  no se deriva del identificador nativo. Sobrevive a que el proveedor cambie su
  formato o a que el recurso se recree con otro id nativo y el mismo nombre.
- Un activo que deja de verse se retira despues de RETIRO_TRAS ejecuciones
  exitosas seguidas sin verlo. Una ejecucion con error no cuenta: una API
  caida no retira nada.
- Retirar es marcar, nunca borrar: la telemetria historica sigue apuntando a
  un id que existe.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

log = logging.getLogger("zenit.catalogo")

RETIRO_TRAS = 3

# Prefijo del id canonico por proveedor. Coincide con los ids que ya se
# usaban a mano en la epica 0 y la 2 (activo-do-nodo-01, activo-onprem-nodo-01).
PREFIJOS = {"digitalocean": "do", "proxmox": "onprem", "aws": "aws", "azure": "azure"}


@dataclass(frozen=True)
class Recurso:
    """Un recurso tal como lo lista un proveedor, ya en forma comun."""

    proveedor: str              # digitalocean | proxmox | aws
    id_nativo: str              # droplet/412345678, qemu/105, instancia/i-0abc
    nombre: str                 # nombre del recurso; en el laboratorio coincide con ZENIT_NODO
    region: Optional[str] = None
    entorno: Optional[str] = None
    atributos: dict = field(default_factory=dict)

    @property
    def clave(self) -> str:
        return f"{self.proveedor}:{self.id_nativo}"


@dataclass
class Resultado:
    vistos: int = 0
    nuevos: int = 0
    retirados: int = 0
    reactivados: int = 0
    conflictos: list = field(default_factory=list)


# ------------------------------------------------------------------ conexion
@contextmanager
def conectar(url: str, autocommit: bool = False):
    import psycopg

    conexion = psycopg.connect(url, connect_timeout=5, autocommit=autocommit)
    try:
        yield conexion
    finally:
        conexion.close()


def _slug(texto: str) -> str:
    limpio = re.sub(r"[^a-z0-9]+", "-", texto.lower()).strip("-")
    return limpio[:40] or "sin-nombre"


def nuevo_id(cur, proveedor: str, nombre: str) -> str:
    base = f"activo-{PREFIJOS.get(proveedor, _slug(proveedor))}-{_slug(nombre)}"
    candidato, n = base, 1
    while True:
        cur.execute("SELECT 1 FROM activos WHERE id = %s", (candidato,))
        if cur.fetchone() is None:
            return candidato
        n += 1
        candidato = f"{base}-{n}"


# ------------------------------------------------------------------ lectura
def mapa_claves(conexion) -> dict:
    """{clave: activo} con todas las claves, tambien las de activos retirados:
    un activo retirado que vuelve a emitir se resuelve igual, y el siguiente
    descubrimiento lo reactiva."""
    with conexion.cursor() as cur:
        cur.execute("SELECT clave, activo_id FROM claves_nativas")
        return {clave: activo for clave, activo in cur.fetchall()}


_COLUMNAS = ("id", "nombre", "proveedor", "region", "entorno", "origen", "estado",
             "ausencias", "creado", "visto", "retirado_en", "atributos")


def listar_activos(conexion, incluir_retirados: bool = True) -> list:
    with conexion.cursor() as cur:
        cur.execute(
            f"SELECT {', '.join('a.' + c for c in _COLUMNAS)}, "
            "COALESCE(array_agg(k.clave ORDER BY k.clave) FILTER (WHERE k.clave IS NOT NULL), '{}') "
            "FROM activos a LEFT JOIN claves_nativas k ON k.activo_id = a.id "
            + ("" if incluir_retirados else "WHERE a.estado = 'activo' ")
            + "GROUP BY a.id ORDER BY a.id"
        )
        return [{**dict(zip(_COLUMNAS, f[:-1])), "claves": list(f[-1])} for f in cur.fetchall()]


# ------------------------------------------------------------------ escritura
def agregar_manual(conexion, clave: str, activo: str, proveedor: str = "desconocido",
                   nombre: Optional[str] = None, region: Optional[str] = None,
                   entorno: Optional[str] = None) -> bool:
    """Admite un nodo a mano: crea el activo si no existe y le asocia la
    clave host.name. Es lo que reemplaza editar catalogo.json, y el camino para
    un entorno cuyo descubrimiento no esta disponible. Devuelve True si cambio algo.
    """
    with conexion.cursor() as cur:
        cur.execute(
            "INSERT INTO activos (id, nombre, proveedor, region, entorno, origen) "
            "VALUES (%s, %s, %s, %s, %s, 'manual') ON CONFLICT (id) DO NOTHING",
            (activo, nombre or clave, proveedor, region, entorno),
        )
        creado = cur.rowcount
        cur.execute("SELECT activo_id FROM claves_nativas WHERE clave = %s", (clave,))
        fila = cur.fetchone()
        if fila and fila[0] != activo:
            conexion.rollback()
            raise ValueError(f"la clave {clave!r} ya pertenece a {fila[0]}")
        cur.execute(
            "INSERT INTO claves_nativas (clave, activo_id, tipo, proveedor) VALUES (%s, %s, 'host.name', %s) "
            "ON CONFLICT (clave) DO NOTHING",
            (clave, activo, proveedor),
        )
        creado += cur.rowcount
    conexion.commit()
    return bool(creado)


def importar_archivo(conexion, ruta: Path) -> int:
    """Pasa deploy/catalogo/catalogo.json (epicas 0 y 2) al catalogo, con
    origen manual. Conserva los ids que ya tiene la telemetria historica.
    Idempotente: lo que ya esta no se toca."""
    ruta = Path(ruta)
    if not ruta.exists():
        return 0
    datos = json.loads(ruta.read_text())
    if not isinstance(datos, dict):
        raise ValueError(f"{ruta} debe ser un objeto JSON clave -> activo")
    cambios = 0
    for clave, activo in datos.items():
        proveedor = next((p for p, pref in PREFIJOS.items() if str(activo).startswith(f"activo-{pref}-")), "desconocido")
        try:
            cambios += agregar_manual(conexion, str(clave), str(activo), proveedor)
        except ValueError as e:
            log.warning("no se importa %s: %s", clave, e)
    return cambios


def reconciliar(conexion, proveedor: str, recursos: Iterable[Recurso],
                retiro_tras: int = RETIRO_TRAS) -> Resultado:
    """Aplica una ejecucion EXITOSA del descubrimiento de un proveedor.

    Para cada recurso: se reconoce por su clave nativa; si no, por su nombre
    (un activo que se admitio a mano antes de que existiera el descubrimiento
    conserva su id); si no, es nuevo. Despues, los activos de ese proveedor que
    no se vieron suman una ausencia y se retiran al llegar a retiro_tras.

    Corre en una sola transaccion con un candado por proveedor: dos
    descubridores a la vez no pueden crear el mismo activo dos veces.
    """
    from psycopg.types.json import Jsonb

    r = Resultado()
    ahora = datetime.now(timezone.utc)
    vistos_ids = set()
    with conexion.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"descubrimiento:{proveedor}",))
        for rec in recursos:
            r.vistos += 1
            cur.execute("SELECT activo_id FROM claves_nativas WHERE clave = %s", (rec.clave,))
            fila = cur.fetchone()
            if fila is None:
                # Por nombre solo se adopta un activo que nadie mas descubrio
                # (admitido a mano) o que ya era de este proveedor (el recurso
                # se recreo con otro id nativo). Uno de otro proveedor con el
                # mismo nombre es otro activo.
                cur.execute(
                    "SELECT k.activo_id FROM claves_nativas k WHERE k.clave = %s AND k.tipo = 'host.name' "
                    "AND NOT EXISTS (SELECT 1 FROM claves_nativas n WHERE n.activo_id = k.activo_id "
                    "AND n.tipo = 'nativo' AND n.proveedor <> %s)",
                    (rec.nombre, proveedor),
                )
                fila = cur.fetchone()
            if fila is None:
                activo = nuevo_id(cur, proveedor, rec.nombre)
                cur.execute(
                    "INSERT INTO activos (id, nombre, proveedor, region, entorno, origen) "
                    "VALUES (%s, %s, %s, %s, %s, 'descubrimiento')",
                    (activo, rec.nombre, proveedor, rec.region, rec.entorno),
                )
                r.nuevos += 1
            else:
                activo = fila[0]
            vistos_ids.add(activo)
            cur.execute(
                "INSERT INTO claves_nativas (clave, activo_id, tipo, proveedor) VALUES (%s, %s, 'nativo', %s) "
                "ON CONFLICT (clave) DO NOTHING",
                (rec.clave, activo, proveedor),
            )
            # el nombre tambien es clave: es lo que llega en host.name
            cur.execute("SELECT activo_id FROM claves_nativas WHERE clave = %s", (rec.nombre,))
            dueno = cur.fetchone()
            if dueno is None:
                cur.execute(
                    "INSERT INTO claves_nativas (clave, activo_id, tipo, proveedor) VALUES (%s, %s, 'host.name', %s)",
                    (rec.nombre, activo, proveedor),
                )
            elif dueno[0] != activo:
                # dos recursos con el mismo nombre: no se le quita la clave a nadie
                r.conflictos.append(f"{rec.nombre} ya resuelve a {dueno[0]}; {rec.clave} queda en {activo}")
            cur.execute("SELECT estado FROM activos WHERE id = %s FOR UPDATE", (activo,))
            if cur.fetchone()[0] == "retirado":
                r.reactivados += 1
            cur.execute(
                "UPDATE activos SET nombre = %s, proveedor = %s, region = COALESCE(%s, region), "
                "entorno = COALESCE(%s, entorno), atributos = %s, visto = %s, ausencias = 0, "
                "estado = 'activo', retirado_en = NULL WHERE id = %s",
                (rec.nombre, proveedor, rec.region, rec.entorno, Jsonb(rec.atributos), ahora, activo),
            )

        # Los de este proveedor que no aparecieron. "De este proveedor" quiere
        # decir que alguna vez lo vio este descubrimiento (tiene clave nativa
        # suya): un activo admitido a mano y nunca descubierto no se retira solo.
        cur.execute(
            "SELECT DISTINCT a.id FROM activos a JOIN claves_nativas k ON k.activo_id = a.id "
            "WHERE k.tipo = 'nativo' AND k.proveedor = %s AND a.estado = 'activo'",
            (proveedor,),
        )
        for (activo,) in cur.fetchall():
            if activo in vistos_ids:
                continue
            cur.execute(
                "UPDATE activos SET ausencias = ausencias + 1, "
                "estado = CASE WHEN ausencias + 1 >= %s THEN 'retirado' ELSE estado END, "
                "retirado_en = CASE WHEN ausencias + 1 >= %s THEN %s ELSE retirado_en END "
                "WHERE id = %s RETURNING estado",
                (retiro_tras, retiro_tras, ahora, activo),
            )
            if cur.fetchone()[0] == "retirado":
                r.retirados += 1
    conexion.commit()
    for c in r.conflictos:
        log.warning("conflicto de nombre en %s: %s", proveedor, c)
    return r


# ------------------------------------------------------------------ ejecuciones
def abrir_ejecucion(conexion, proveedor: str) -> int:
    with conexion.cursor() as cur:
        cur.execute("INSERT INTO descubrimientos (proveedor, estado) VALUES (%s, 'en_curso') RETURNING id",
                    (proveedor,))
        (n,) = cur.fetchone()
    conexion.commit()
    return n


def cerrar_ejecucion(conexion, n: int, resultado: Optional[Resultado] = None, error: Optional[str] = None) -> None:
    r = resultado or Resultado()
    with conexion.cursor() as cur:
        cur.execute(
            "UPDATE descubrimientos SET fin = now(), estado = %s, vistos = %s, nuevos = %s, retirados = %s, "
            "reactivados = %s, error = %s WHERE id = %s",
            ("error" if error else "ok", r.vistos, r.nuevos, r.retirados, r.reactivados,
             (error or "")[:500] or None, n),
        )
    conexion.commit()


def ejecuciones(conexion, limite: int = 20) -> dict:
    """La ultima de cada proveedor y las mas recientes en general."""
    columnas = ("id", "proveedor", "inicio", "fin", "estado", "vistos", "nuevos", "retirados", "reactivados", "error")
    with conexion.cursor() as cur:
        cur.execute(f"SELECT DISTINCT ON (proveedor) {', '.join(columnas)} FROM descubrimientos "
                    "ORDER BY proveedor, inicio DESC")
        ultimas = [dict(zip(columnas, f)) for f in cur.fetchall()]
        cur.execute(f"SELECT {', '.join(columnas)} FROM descubrimientos ORDER BY inicio DESC LIMIT %s", (limite,))
        recientes = [dict(zip(columnas, f)) for f in cur.fetchall()]
    return {"ultimas": ultimas, "recientes": recientes}


# ------------------------------------------------------------------ huerfanas
def sumar_huerfanas(conexion, deltas: dict) -> None:
    """deltas: {clave: {tipo_clave, proveedor, metricas, registros, trazas, primera, ultima}}."""
    if not deltas:
        return
    with conexion.cursor() as cur:
        for clave, d in deltas.items():
            cur.execute(
                "INSERT INTO huerfanas (clave, tipo_clave, proveedor, metricas, registros, trazas, primera, ultima) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (clave) DO UPDATE SET "
                "metricas = huerfanas.metricas + EXCLUDED.metricas, "
                "registros = huerfanas.registros + EXCLUDED.registros, "
                "trazas = huerfanas.trazas + EXCLUDED.trazas, "
                "proveedor = COALESCE(EXCLUDED.proveedor, huerfanas.proveedor), "
                "ultima = GREATEST(huerfanas.ultima, EXCLUDED.ultima), "
                # vuelve a llegar sin resolver despues de asociada: hay algo nuevo que asociar
                "asociada_a = NULL, asociada_en = NULL",
                (clave, d["tipo_clave"], d.get("proveedor"), d.get("metricas", 0), d.get("registros", 0),
                 d.get("trazas", 0), d["primera"], d["ultima"]),
            )
    conexion.commit()


def listar_huerfanas(conexion) -> list:
    columnas = ("clave", "tipo_clave", "proveedor", "metricas", "registros", "trazas", "primera", "ultima",
                "asociada_a", "asociada_en")
    with conexion.cursor() as cur:
        cur.execute(
            f"SELECT {', '.join('h.' + c for c in columnas)}, k.activo_id FROM huerfanas h "
            "LEFT JOIN claves_nativas k ON k.clave = h.clave ORDER BY h.ultima DESC"
        )
        salida = []
        for f in cur.fetchall():
            d = dict(zip(columnas, f[:-1]))
            d["resuelve_a"] = f[-1]   # ya existe en el catalogo: asociable
            salida.append(d)
        return salida


def pendientes_de_asociar(conexion) -> list:
    """[(clave, activo, tipo_clave, primera, ultima)] de las huerfanas cuya clave ya esta en el catalogo."""
    with conexion.cursor() as cur:
        cur.execute(
            "SELECT h.clave, k.activo_id, h.tipo_clave, h.primera, h.ultima FROM huerfanas h "
            "JOIN claves_nativas k ON k.clave = h.clave WHERE h.asociada_a IS NULL ORDER BY h.primera"
        )
        return cur.fetchall()


def marcar_asociada(conexion, clave: str, activo: str) -> None:
    with conexion.cursor() as cur:
        cur.execute("UPDATE huerfanas SET asociada_a = %s, asociada_en = now() WHERE clave = %s", (activo, clave))
        # la verdad de referencia de ese nodo, si llego huerfana
        cur.execute("UPDATE inyecciones SET activo = %s WHERE nodo = %s AND activo IS NULL", (activo, clave))
    conexion.commit()


def retroceder_agregacion(conexion, desde: datetime) -> None:
    """Obliga al agregador a rehacer los resumenes desde `desde`."""
    with conexion.cursor() as cur:
        cur.execute("UPDATE agregacion_progreso SET hasta = LEAST(hasta, %s), actualizado = now()", (desde,))
    conexion.commit()


# ------------------------------------------------------------------ para la ingesta
class CatalogoPG:
    """La memoria intermedia del catalogo dentro de la ingesta (H-010).

    Una consulta a PostgreSQL por senal incumpliria RNF-REN-01; aqui se trae la
    tabla completa cada `cada_s` segundos (30 por defecto, muy por debajo de
    los 15 minutos del descubrimiento) y se resuelve en memoria. La recarga
    corre en su propio hilo: una PostgreSQL lenta no frena la recepcion.

    Si PostgreSQL no responde se conserva el ultimo catalogo bueno. Si nunca
    respondio, se usa `respaldo` (el catalogo de archivo de las epicas 0 y 2).
    """

    def __init__(self, url: str, cada_s: float = 30.0, respaldo=None, arrancar: bool = True):
        self._url = url
        self._cada = cada_s
        self._respaldo = respaldo
        self._datos: Optional[dict] = None
        self.cargado: Optional[str] = None
        self.errores = 0
        self.ultimo_error: Optional[str] = None
        self._parar = threading.Event()
        self.recargar()
        if arrancar:
            threading.Thread(target=self._bucle, name="catalogo-pg", daemon=True).start()

    def recargar(self) -> bool:
        try:
            with conectar(self._url) as c:
                datos = mapa_claves(c)
            self._datos = datos   # reemplazo atomico de la referencia
            self.cargado = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self.ultimo_error = None
            return True
        except Exception as e:  # noqa: BLE001 - cualquier fallo conserva el anterior
            self.errores += 1
            self.ultimo_error = f"{type(e).__name__}: {e}"[:300]
            log.warning("no se pudo recargar el catalogo, se conserva el anterior: %s", self.ultimo_error)
            return False

    def _bucle(self) -> None:
        while not self._parar.wait(self._cada):
            self.recargar()

    def detener(self) -> None:
        self._parar.set()

    def actual(self) -> dict:
        if self._datos is not None:
            return self._datos
        return self._respaldo.actual() if self._respaldo is not None else {}

    def estado(self) -> dict:
        return {"fuente": "postgres" if self._datos is not None else "archivo",
                "claves": len(self.actual()), "cargado": self.cargado,
                "errores": self.errores, "ultimo_error": self.ultimo_error}


class AcumuladorHuerfanas:
    """Cuenta en memoria lo que llega sin resolver y lo vuelca a PostgreSQL
    cada tanto (no una escritura por peticion)."""

    def __init__(self):
        self._candado = threading.Lock()
        self._deltas: dict = {}

    def sumar(self, clave: Optional[str], tipo_clave: Optional[str], proveedor: Optional[str],
              senal: str, n: int) -> None:
        if not n:
            return
        ahora = datetime.now(timezone.utc)
        clave = clave or "(sin clave nativa)"
        with self._candado:
            d = self._deltas.get(clave)
            if d is None:
                d = self._deltas[clave] = {"tipo_clave": tipo_clave or "ninguna", "proveedor": proveedor,
                                           "metricas": 0, "registros": 0, "trazas": 0,
                                           "primera": ahora, "ultima": ahora}
            d[senal] += n
            d["ultima"] = ahora

    def tomar(self) -> dict:
        with self._candado:
            deltas, self._deltas = self._deltas, {}
        return deltas

    def devolver(self, deltas: dict) -> None:
        """Si el volcado fallo, se suma de vuelta para el siguiente intento."""
        with self._candado:
            for clave, d in deltas.items():
                actual = self._deltas.get(clave)
                if actual is None:
                    self._deltas[clave] = d
                    continue
                for s in ("metricas", "registros", "trazas"):
                    actual[s] += d[s]
                actual["primera"] = min(actual["primera"], d["primera"])

    def volcar(self, url: str) -> int:
        deltas = self.tomar()
        if not deltas:
            return 0
        try:
            with conectar(url) as c:
                sumar_huerfanas(c, deltas)
        except Exception:
            self.devolver(deltas)
            raise
        return len(deltas)


def esperar_pg(url: str, intentos: int = 30, pausa: float = 2.0) -> None:
    for i in range(intentos):
        try:
            with conectar(url) as c, c.cursor() as cur:
                cur.execute("SELECT 1")
            return
        except Exception:
            if i == intentos - 1:
                raise
            time.sleep(pausa)
