"""Reporte de configuraciones (RF-GOB-03, RF-GOB-04).

Cada nodo envia una instantanea de su configuracion, que toma
scripts/instantanea-nodo.sh. Las reglas comparan la ultima instantanea de
cada nodo contra lo esperado y entre entornos, y producen hallazgos con la
regla, los activos, los entornos y la severidad (P-GOB-04).
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Protocol

SEVERIDADES = ("baja", "media", "alta")


class InstantaneaInvalida(ValueError):
    pass


@dataclass(frozen=True)
class Puerto:
    puerto: int
    protocolo: str  # tcp | udp
    direccion: str  # 0.0.0.0, ::, 127.0.0.1...
    proceso: str = ""


@dataclass(frozen=True)
class Contenedor:
    nombre: str
    imagen: str
    version: str
    usuario: str  # "" o "root" o "0" es root


@dataclass(frozen=True)
class Instantanea:
    nodo: str
    entorno: str
    tomada_en: datetime
    version_agente: str
    intervalo_muestreo_s: int
    puertos: tuple[Puerto, ...]
    contenedores: tuple[Contenedor, ...]
    activo: Optional[str] = None  # zenit_asset_id resuelto con el catalogo

    def a_dict(self) -> dict:
        d = asdict(self)
        d["tomada_en"] = self.tomada_en.isoformat()
        d["puertos"] = [asdict(p) for p in self.puertos]
        d["contenedores"] = [asdict(c) for c in self.contenedores]
        return d


def _texto(d: dict, clave: str, maximo: int = 200, obligatorio: bool = True) -> str:
    v = d.get(clave, "")
    if v is None:
        v = ""
    if not isinstance(v, str):
        raise InstantaneaInvalida(f"{clave} debe ser texto")
    if obligatorio and not v:
        raise InstantaneaInvalida(f"falta {clave}")
    if len(v) > maximo:
        raise InstantaneaInvalida(f"{clave} es demasiado largo")
    return v


def leer_instantanea(d: dict, catalogo: dict[str, str] | None = None) -> Instantanea:
    if not isinstance(d, dict):
        raise InstantaneaInvalida("se espera un objeto JSON")
    try:
        tomada = datetime.fromisoformat(_texto(d, "tomada_en").replace("Z", "+00:00"))
    except ValueError:
        raise InstantaneaInvalida("tomada_en no es ISO 8601")
    if tomada.tzinfo is None:
        tomada = tomada.replace(tzinfo=timezone.utc)
    intervalo = d.get("intervalo_muestreo_s")
    if not isinstance(intervalo, int) or isinstance(intervalo, bool) or not 1 <= intervalo <= 3600:
        raise InstantaneaInvalida("intervalo_muestreo_s debe ser un entero entre 1 y 3600")
    puertos, contenedores = d.get("puertos", []), d.get("contenedores", [])
    if not isinstance(puertos, list) or not isinstance(contenedores, list):
        raise InstantaneaInvalida("puertos y contenedores deben ser listas")
    if len(puertos) > 2000 or len(contenedores) > 500:
        raise InstantaneaInvalida("instantanea demasiado grande")
    try:
        ps = tuple(
            Puerto(int(p["puerto"]), _texto(p, "protocolo", 8), _texto(p, "direccion", 64),
                   _texto(p, "proceso", 120, False))
            for p in puertos
        )
        cs = tuple(
            Contenedor(_texto(c, "nombre"), _texto(c, "imagen", 300), _texto(c, "version", 128, False),
                       _texto(c, "usuario", 64, False))
            for c in contenedores
        )
    except (KeyError, TypeError, ValueError) as e:
        if isinstance(e, InstantaneaInvalida):
            raise
        raise InstantaneaInvalida(f"puerto o contenedor mal formado: {e}")
    if any(not 0 < p.puerto < 65536 for p in ps):
        raise InstantaneaInvalida("puerto fuera de rango")
    nodo = _texto(d, "nodo", 120)
    return Instantanea(
        nodo=nodo,
        entorno=_texto(d, "entorno", 60),
        tomada_en=tomada,
        version_agente=_texto(d, "version_agente", 60, False),
        intervalo_muestreo_s=intervalo,
        puertos=ps,
        contenedores=cs,
        activo=(catalogo or {}).get(nodo),
    )


# ------------------------------------------------------------------ reglas

@dataclass(frozen=True)
class Politica:
    """Lo esperado. Se carga de deploy/gobernanza/politica.json."""

    # puertos que pueden escuchar fuera de loopback, por entorno; "*" vale para todos
    puertos_esperados: dict[str, list[int]] = field(default_factory=lambda: {"*": [22]})
    # puertos que nunca deben quedar expuestos (P-SEG-01): un hallazgo sobre
    # ellos es de severidad alta
    puertos_sensibles: list[int] = field(default_factory=lambda: [5432, 8000, 8080, 8181])
    # contenedores que corren como root a proposito, con su motivo
    root_permitido: dict[str, str] = field(default_factory=dict)
    # si esta vacia, la referencia es la version mas comun entre los nodos
    version_agente_esperada: str = ""

    @classmethod
    def desde_archivo(cls, ruta: Path | None) -> "Politica":
        if not ruta or not Path(ruta).exists():
            return cls()
        datos = json.loads(Path(ruta).read_text())
        return cls(**{k: v for k, v in datos.items() if k in cls.__dataclass_fields__})


@dataclass(frozen=True)
class Hallazgo:
    """La forma de GET /api/gobernanza/configuraciones (contrato, seccion 3)."""

    regla: str
    severidad: str
    activos: list[str]
    entornos: list[str]
    detalle: str
    detectado: str  # ISO 8601: cuando se tomo la instantanea mas reciente que lo muestra


_LOOPBACK = ("127.", "::1", "localhost")


def _expuesto(p: Puerto) -> bool:
    return not p.direccion.startswith(_LOOPBACK)


def _activo(i: Instantanea) -> str:
    return i.activo or i.nodo


def _es_root(c: Contenedor) -> bool:
    usuario = c.usuario.split(":")[0].strip()
    return usuario in ("", "root", "0")


def _cuando(grupo: list[Instantanea]) -> str:
    return max(i.tomada_en for i in grupo).isoformat()


def evaluar(instantaneas: list[Instantanea], politica: Politica) -> list[Hallazgo]:
    hallazgos: list[Hallazgo] = []

    def agregar(regla: str, severidad: str, grupo: list[Instantanea], detalle: str) -> None:
        hallazgos.append(Hallazgo(
            regla=regla, severidad=severidad,
            activos=sorted({_activo(i) for i in grupo}),
            entornos=sorted({i.entorno for i in grupo}),
            detalle=detalle, detectado=_cuando(grupo),
        ))

    # 1. puerto expuesto que no se esperaba
    for i in instantaneas:
        esperados = set(politica.puertos_esperados.get("*", [])) | set(politica.puertos_esperados.get(i.entorno, []))
        vistos = sorted({(p.puerto, p.protocolo, p.direccion, p.proceso) for p in i.puertos
                         if _expuesto(p) and p.puerto not in esperados})
        for puerto, protocolo, direccion, proceso in vistos:
            sensible = puerto in politica.puertos_sensibles
            agregar("puerto-expuesto-no-esperado", "alta" if sensible else "media", [i],
                    f"El puerto {puerto}/{protocolo} escucha en {direccion} en {i.nodo}"
                    + (f" ({proceso})" if proceso else "")
                    + f" y no esta entre los esperados del entorno {i.entorno}."
                    + (" Es uno de los que nunca deben quedar expuestos." if sensible else ""))

    # 2. version del agente distinta entre nodos
    versiones = Counter(i.version_agente for i in instantaneas if i.version_agente)
    referencia = politica.version_agente_esperada or (versiones.most_common(1)[0][0] if versiones else "")
    if referencia:
        distintos: dict[str, list[Instantanea]] = defaultdict(list)
        for i in instantaneas:
            if i.version_agente != referencia:
                distintos[i.version_agente or "desconocida"].append(i)
        for version, grupo in sorted(distintos.items()):
            agregar("version-agente-distinta", "media", grupo,
                    f"{', '.join(sorted(i.nodo for i in grupo))} corre el agente {version} y el resto {referencia}. "
                    "La comparacion entre entornos solo vale si todos miden con la misma version.")

    # 3. intervalo de muestreo distinto entre entornos
    intervalos = Counter(i.intervalo_muestreo_s for i in instantaneas)
    if len(intervalos) > 1:
        comun = intervalos.most_common(1)[0][0]
        grupo = [i for i in instantaneas if i.intervalo_muestreo_s != comun]
        agregar("intervalo-muestreo-distinto", "media", grupo,
                "Muestrean distinto del comun (" + f"{comun} s): "
                + "; ".join(f"{i.nodo} cada {i.intervalo_muestreo_s} s" for i in grupo) + ".")

    # 4. contenedor corriendo como root
    for i in instantaneas:
        for c in i.contenedores:
            if _es_root(c) and c.nombre not in politica.root_permitido:
                agregar("contenedor-como-root", "alta", [i],
                        f"El contenedor {c.nombre} ({c.imagen}:{c.version or '?'}) corre como root en {i.nodo}.")

    # 5. misma imagen en versiones distintas entre entornos
    por_imagen: dict[str, dict[str, list[Instantanea]]] = defaultdict(lambda: defaultdict(list))
    for i in instantaneas:
        for c in i.contenedores:
            por_imagen[c.imagen][c.version].append(i)
    for imagen, versiones_img in sorted(por_imagen.items()):
        if len(versiones_img) < 2:
            continue
        todos = [i for g in versiones_img.values() for i in g]
        agregar("imagen-version-distinta", "baja", todos,
                f"{imagen} corre en {len(versiones_img)} versiones: "
                + "; ".join(f"{v or '?'} en {', '.join(sorted({i.nodo for i in g}))}" for v, g in sorted(versiones_img.items()))
                + ".")

    orden = {s: n for n, s in enumerate(reversed(SEVERIDADES))}
    return sorted(hallazgos, key=lambda h: (orden[h.severidad], h.regla, h.detalle))


# ------------------------------------------------------------- almacenamiento

class RepositorioInstantaneas(Protocol):
    def guardar(self, i: Instantanea) -> None: ...
    def ultimas(self) -> list[Instantanea]: ...


class InstantaneasMemoria:
    def __init__(self):
        self._datos: list[Instantanea] = []

    def guardar(self, i):
        self._datos.append(i)

    def ultimas(self):
        por_nodo: dict[str, Instantanea] = {}
        for i in self._datos:
            if i.nodo not in por_nodo or i.tomada_en >= por_nodo[i.nodo].tomada_en:
                por_nodo[i.nodo] = i
        return sorted(por_nodo.values(), key=lambda i: i.nodo)


class InstantaneasPg:
    def __init__(self, url: str):
        self._url = url

    def _conexion(self):
        import psycopg

        return psycopg.connect(self._url, autocommit=True)

    def guardar(self, i):
        from psycopg.types.json import Jsonb

        with self._conexion() as c:
            c.execute(
                "INSERT INTO instantaneas (nodo, activo, entorno, tomada_en, datos) VALUES (%s, %s, %s, %s, %s)",
                (i.nodo, i.activo, i.entorno, i.tomada_en, Jsonb(i.a_dict())),
            )

    def ultimas(self):
        with self._conexion() as c:
            filas = c.execute(
                "SELECT DISTINCT ON (nodo) datos, activo FROM instantaneas ORDER BY nodo, tomada_en DESC"
            ).fetchall()
        salida = []
        for datos, activo in filas:
            i = leer_instantanea(datos)
            salida.append(Instantanea(**{**i.__dict__, "activo": activo}))
        return salida
