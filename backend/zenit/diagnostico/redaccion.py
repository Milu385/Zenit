"""Redaccion de la evidencia antes de enviarla al modelo (H-041, RNF-SEG-08).

Nada que identifique la infraestructura sale de la plataforma: antes de
llamar al proveedor del modelo, H-040 pasa la evidencia por `redactar`, que
reemplaza en cada texto:

- los valores conocidos del catalogo (`conocidos`: {valor: tipo}), con el tipo
  que trae cada uno: ACTIVO (ids de activo), ANFITRION (nombres de anfitrion,
  host.name) e ID (ids nativos);
- lo que se reconoce por su forma, aunque no este en el catalogo: direcciones
  IP v4 y v6 ([IP_n]), ids nativos `proveedor:tipo/id` (azure:vm/..., aws:
  instancia/i-..., digitalocean:droplet/..., proxmox:qemu/...), ids de
  instancia de AWS, GUID y rutas de recurso de Azure ([ID_n]), y nombres de
  anfitrion internos de EC2 como ip-10-0-3-17.ec2.internal ([ANFITRION_n]).

Reglas:
- El mismo valor recibe siempre el mismo marcador dentro de una redaccion, y
  dos valores distintos nunca comparten marcador.
- Si dos valores conocidos se solapan, gana el mas largo (activo-aws-nodo-01
  antes que aws-nodo).
- Un valor conocido solo se reemplaza como palabra completa: "zenit-nodo-do"
  no toca "zenit-nodo-do-viejo" (que, si es conocido, tiene su propio marcador).
- Los numeros, las fechas y las claves de los diccionarios no se tocan; los
  nombres de metrica y las dimensiones tampoco, salvo que contengan un
  identificador.

`redactar` devuelve una copia redactada y el mapa {marcador: original};
`restaurar` usa ese mapa para mostrar la hipotesis con los nombres reales.
"""
from __future__ import annotations

import ipaddress
import re
from typing import Any

TIPOS = ("ACTIVO", "ANFITRION", "ID", "IP")
_MARCADOR = re.compile(r"\[(?:ACTIVO|ANFITRION|ID|IP)_\d+\]")

# Lo que se reconoce por su forma. El orden importa solo cuando dos patrones
# empiezan en el mismo lugar: el primero que coincide gana.
_PATRONES = [
    ("ID", r"(?:aws|azure|digitalocean|proxmox|onprem|gcp):[A-Za-z][\w-]*/[\w./-]*[\w]"),   # clave nativa
    ("ID", r"/subscriptions/[0-9a-fA-F-]{36}(?:/[\w.()-]+)*"),                            # recurso de Azure
    ("ID", r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"),  # GUID
    ("ID", r"i-[0-9a-f]{8}(?:[0-9a-f]{9})?"),                                               # instancia EC2
    ("ANFITRION", r"ip-\d{1,3}-\d{1,3}-\d{1,3}-\d{1,3}(?:\.[A-Za-z0-9-]+)*"),               # anfitrion de EC2
    ("IP6", r"[0-9A-Fa-f]{0,4}(?::[0-9A-Fa-f]{0,4}){2,7}(?:%[\w]+)?"),                     # candidata a IPv6
    ("IP4", r"(?:\d{1,3}\.){3}\d{1,3}"),
]
# Bordes de palabra para los identificadores: no pueden venir pegados a letras,
# digitos, guiones o puntos que sigan siendo parte de un nombre.
_ANTES = r"(?<![\w.-])"
_DESPUES = r"(?![\w-]|\.\w)"


class _Redactor:
    def __init__(self, conocidos: dict[str, str]):
        self.tipo_de: dict[str, str] = {}
        for valor, tipo in (conocidos or {}).items():
            valor = str(valor or "").strip()
            if not valor or valor.isdigit():   # un numero suelto no se reemplaza: "los numeros no se tocan"
                continue
            tipo = str(tipo).upper()
            self.tipo_de[valor.lower()] = tipo if tipo in TIPOS else "ID"
        self.mapa: dict[str, str] = {}          # marcador -> original
        self._marcador_de: dict[str, str] = {}  # valor normalizado -> marcador
        self._cuenta: dict[str, int] = {}
        partes = []
        if self.tipo_de:
            # el mas largo primero: en una alternancia gana la primera opcion que coincide
            conocidos_re = "|".join(re.escape(v) for v in sorted(self.tipo_de, key=len, reverse=True))
            partes.append(f"(?P<CONOCIDO>{_ANTES}(?:{conocidos_re}){_DESPUES})")
        for i, (tipo, patron) in enumerate(_PATRONES):
            partes.append(f"(?P<{tipo}_{i}>{_ANTES}{patron}{_DESPUES})")
        self._re = re.compile("|".join(partes), re.IGNORECASE)

    def marcador(self, original: str, tipo: str) -> str:
        clave = original.lower()
        m = self._marcador_de.get(clave)
        if m is None:
            self._cuenta[tipo] = self._cuenta.get(tipo, 0) + 1
            m = f"[{tipo}_{self._cuenta[tipo]}]"
            self._marcador_de[clave] = m
            self.mapa[m] = original
        return m

    def _reemplazo(self, coincidencia: re.Match) -> str:
        texto = coincidencia.group(0)
        grupo = coincidencia.lastgroup or ""
        if grupo == "CONOCIDO":
            return self.marcador(texto, self.tipo_de[texto.lower()])
        tipo = grupo.rsplit("_", 1)[0]
        if tipo == "IP6":
            # la forma deja pasar horas (12:30:45) y otras cosas: solo vale si es una IPv6 de verdad
            try:
                ipaddress.IPv6Address(texto.split("%")[0])
            except ValueError:
                return texto
            return self.marcador(texto, "IP")
        if tipo == "IP4":
            try:
                ipaddress.IPv4Address(texto)
            except ValueError:
                return texto      # 300.1.2.3 o una version con ceros a la izquierda: no es IP
            return self.marcador(texto, "IP")
        return self.marcador(texto, tipo)

    def texto(self, s: str) -> str:
        # los marcadores que ya estan (por ejemplo, de una redaccion anterior) no se vuelven a tocar
        trozos = _MARCADOR.split(s)
        marcas = _MARCADOR.findall(s)
        salida = [self._re.sub(self._reemplazo, t) for t in trozos]
        return "".join(x for par in zip(salida, marcas + [""]) for x in par)

    def recorrer(self, o: Any) -> Any:
        if isinstance(o, str):
            return self.texto(o)
        if isinstance(o, dict):
            return {k: self.recorrer(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [self.recorrer(v) for v in o]
        return o   # numeros, booleanos, None y fechas ya serializadas como texto se tratan arriba


def redactar(evidencia: Any, conocidos: dict[str, str]) -> tuple[Any, dict[str, str]]:
    """Copia de la evidencia sin identificadores, y el mapa {marcador: original}.

    `conocidos` es {valor: tipo} con tipo ACTIVO, ANFITRION o ID (H-040 lo
    arma con `conocidos_del_catalogo`). La evidencia original no se modifica.
    """
    r = _Redactor(conocidos)
    return r.recorrer(evidencia), dict(r.mapa)


def restaurar(texto: str, mapa: dict[str, str]) -> str:
    """Devuelve los nombres reales a un texto del modelo. Un marcador que no
    esta en el mapa (el modelo lo invento) se deja como vino."""
    if not texto or not mapa:
        return texto
    return _MARCADOR.sub(lambda m: mapa.get(m.group(0), m.group(0)), texto)
