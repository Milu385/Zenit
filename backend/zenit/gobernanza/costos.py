"""Reporte de costos (RF-GOB-05, RF-GOB-07).

Se importa la factura que exporta el proveedor y cada linea se atribuye a un
activo con el mismo catalogo que usa la ingesta (claves nativas -> activo). Lo
que no se puede atribuir no se reparte a ojo: se reporta aparte con su motivo
(P-GOB-05).

DigitalOcean exporta la factura en CSV con estas columnas:
    product, group_description, description, hours, start, end, USD,
    project_name, category
El proyecto no identifica el recurso, asi que no se usa para atribuir.
La descripcion de un droplet trae su nombre, p. ej. "zenit-nodo-do (s-1vcpu-1gb)".
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Optional, Protocol

PROVEEDORES = ("digitalocean",)
MAXIMO_BYTES = 2 * 1024 * 1024
MAXIMO_FILAS = 20_000


class FacturaInvalida(ValueError):
    pass


@dataclass(frozen=True)
class Cargo:
    proveedor: str
    producto: str
    concepto: str
    monto: Decimal
    moneda: str
    desde: Optional[date]
    hasta: Optional[date]
    activo: Optional[str]  # None: no asignable
    motivo: str = ""  # por que no se asigno
    fuente: str = ""  # de que factura salio, p. ej. "factura exportada 2026-09"


@dataclass(frozen=True)
class Factura:
    huella: str  # sha256 del archivo: importar dos veces la misma no duplica
    proveedor: str
    importada_en: datetime
    importada_por: str
    cargos: tuple[Cargo, ...]


def _columna(cabecera: list[str], *nombres: str) -> Optional[int]:
    normal = [c.strip().lower() for c in cabecera]
    for n in nombres:
        if n in normal:
            return normal.index(n)
    return None


def _fecha(texto: str) -> Optional[date]:
    texto = (texto or "").strip()
    if not texto:
        return None
    for formato in ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(texto.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _palabras(texto: str) -> set[str]:
    # los nombres de recursos llevan guiones y puntos: son parte de la palabra
    return set(re.findall(r"[a-z0-9][a-z0-9._-]*", texto.lower()))


def atribuir(textos: list[str], catalogo: dict[str, str]) -> tuple[Optional[str], str]:
    """Busca en los textos de la linea una clave nativa del catalogo o un id de
    activo. Devuelve (activo, motivo si no se pudo)."""
    palabras = set().union(*(_palabras(t) for t in textos))
    claves = {k.lower(): a for k, a in catalogo.items()}
    claves.update({a.lower(): a for a in catalogo.values()})
    encontrados = {claves[p] for p in palabras if p in claves}
    if len(encontrados) == 1:
        return encontrados.pop(), ""
    if len(encontrados) > 1:
        return None, f"coincide con varios activos: {', '.join(sorted(encontrados))}"
    return None, "ningun recurso del catalogo aparece en la linea"


def leer_digitalocean(contenido: bytes, catalogo: dict[str, str]) -> list[Cargo]:
    if len(contenido) > MAXIMO_BYTES:
        raise FacturaInvalida("la factura pasa de 2 MB")
    try:
        texto = contenido.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise FacturaInvalida("la factura no es UTF-8")
    filas = list(csv.reader(io.StringIO(texto)))
    if not filas:
        raise FacturaInvalida("la factura esta vacia")
    cab = filas[0]
    i_monto = _columna(cab, "usd", "amount", "cost")
    i_desc = _columna(cab, "description")
    if i_monto is None or i_desc is None:
        raise FacturaInvalida("no parece una factura de DigitalOcean: faltan las columnas description y USD")
    i_prod = _columna(cab, "product")
    i_grupo = _columna(cab, "group_description")
    i_ini, i_fin = _columna(cab, "start"), _columna(cab, "end")
    if len(filas) - 1 > MAXIMO_FILAS:
        raise FacturaInvalida(f"la factura pasa de {MAXIMO_FILAS} lineas")

    def celda(fila: list[str], i: Optional[int]) -> str:
        return fila[i].strip() if i is not None and i < len(fila) else ""

    cargos = []
    for n, fila in enumerate(filas[1:], start=2):
        if not any(c.strip() for c in fila):
            continue
        bruto = celda(fila, i_monto).replace("$", "").replace(",", "")
        try:
            monto = Decimal(bruto)
        except InvalidOperation:
            raise FacturaInvalida(f"linea {n}: monto {bruto!r} no es un numero")
        producto, grupo, desc = celda(fila, i_prod), celda(fila, i_grupo), celda(fila, i_desc)
        activo, motivo = atribuir([desc, grupo], catalogo)
        cargos.append(Cargo(
            proveedor="digitalocean",
            producto=producto,
            concepto=" · ".join(x for x in (grupo, desc) if x) or producto or f"linea {n}",
            monto=monto,
            moneda="USD",
            desde=_fecha(celda(fila, i_ini)),
            hasta=_fecha(celda(fila, i_fin)),
            activo=activo,
            motivo=motivo if not activo else "",
        ))
    return cargos


def importar(contenido: bytes, proveedor: str, catalogo: dict[str, str], usuario: str,
             ahora: Optional[datetime] = None) -> Factura:
    if proveedor not in PROVEEDORES:
        raise FacturaInvalida(f"proveedor no soportado {proveedor!r}; soportados: {', '.join(PROVEEDORES)}")
    cargos = leer_digitalocean(contenido, catalogo)
    meses = Counter(c.desde.strftime("%Y-%m") for c in cargos if c.desde)
    fuente = f"factura exportada {meses.most_common(1)[0][0]}" if meses else "factura exportada"
    cargos = [replace(c, fuente=fuente) for c in cargos]
    return Factura(
        huella=hashlib.sha256(contenido).hexdigest(),
        proveedor=proveedor,
        importada_en=ahora or datetime.now().astimezone(),
        importada_por=usuario,
        cargos=tuple(cargos),
    )


def _dinero(d: Decimal) -> float:
    return float(d.quantize(Decimal("0.01")))


def periodo_por_defecto(cargos: list[Cargo], hoy: Optional[date] = None) -> tuple[date, date]:
    """Sin desde ni hasta: todo lo importado; sin nada importado, el mes en curso."""
    fechas = [c.desde for c in cargos if c.desde] + [c.hasta for c in cargos if c.hasta]
    if fechas:
        return min(fechas), max(fechas)
    hoy = hoy or date.today()
    siguiente = (hoy.replace(day=28) + timedelta(days=4)).replace(day=1)
    return hoy.replace(day=1), siguiente - timedelta(days=1)


def reporte(cargos: list[Cargo], desde: date, hasta: date) -> dict:
    """GET /api/gobernanza/costos (contrato, seccion 3). Un cargo entra al
    periodo si empieza dentro de el."""
    dentro = [c for c in cargos if c.desde and desde <= c.desde <= hasta]
    grupos: dict[tuple[str, str], list[Cargo]] = defaultdict(list)
    sin_asignar = Decimal(0)
    for c in dentro:
        if c.activo:
            grupos[(c.activo, c.proveedor)].append(c)
        else:
            sin_asignar += c.monto
    por_activo = [
        {
            "activo": activo,
            "proveedor": proveedor,
            "costo": _dinero(sum((c.monto for c in g), Decimal(0))),
            "fuente": ", ".join(sorted({c.fuente for c in g if c.fuente})),
        }
        for (activo, proveedor), g in grupos.items()
    ]
    return {
        "moneda": "USD",
        "periodo": {"desde": desde.isoformat(), "hasta": hasta.isoformat()},
        "por_activo": sorted(por_activo, key=lambda x: (-x["costo"], x["activo"])),
        "sin_asignar": _dinero(sin_asignar),
    }


# ------------------------------------------------------------- almacenamiento

class RepositorioCostos(Protocol):
    def guardar(self, f: Factura) -> bool: ...  # False si ya estaba
    def cargos(self) -> list[Cargo]: ...


class CostosMemoria:
    def __init__(self):
        self._facturas: dict[str, Factura] = {}

    def guardar(self, f):
        if f.huella in self._facturas:
            return False
        self._facturas[f.huella] = f
        return True

    def cargos(self):
        return [c for f in self._facturas.values() for c in f.cargos]


class CostosPg:
    def __init__(self, url: str):
        self._url = url

    def _conexion(self):
        import psycopg

        return psycopg.connect(self._url)

    def guardar(self, f):
        with self._conexion() as c:
            fila = c.execute(
                "INSERT INTO facturas (huella, proveedor, importada_en, importada_por) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (huella) DO NOTHING RETURNING id",
                (f.huella, f.proveedor, f.importada_en, f.importada_por),
            ).fetchone()
            if not fila:
                return False
            with c.cursor() as cur:
                cur.executemany(
                    "INSERT INTO cargos (factura_id, proveedor, producto, concepto, monto, moneda, desde, hasta, activo, motivo, fuente) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    [(fila[0], x.proveedor, x.producto, x.concepto, x.monto, x.moneda, x.desde, x.hasta, x.activo, x.motivo, x.fuente)
                     for x in f.cargos],
                )
        return True

    def cargos(self):
        with self._conexion() as c:
            filas = c.execute(
                "SELECT proveedor, producto, concepto, monto, moneda, desde, hasta, activo, motivo, fuente FROM cargos"
            ).fetchall()
        return [Cargo(*f) for f in filas]


def resumen_importacion(f: Factura, nueva: bool) -> dict:
    asignados = [c for c in f.cargos if c.activo]
    return {
        "huella": f.huella,
        "proveedor": f.proveedor,
        "nueva": nueva,
        "lineas": len(f.cargos),
        "asignadas": len(asignados),
        "sin_asignar": len(f.cargos) - len(asignados),
        "total": _dinero(sum((c.monto for c in f.cargos), Decimal(0))),
        "lineas_sin_asignar": [
            {"concepto": c.concepto, "producto": c.producto, "monto": _dinero(c.monto), "motivo": c.motivo}
            for c in f.cargos if not c.activo
        ],
    }
