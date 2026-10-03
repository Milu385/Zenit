"""Construccion de lineas del protocolo de linea de InfluxDB.

Las reglas de escape copian las del constructor oficial del motor
(core/influxdb_line_protocol/src/builder.rs del repositorio de InfluxDB):

- medicion: se escapan la coma, el espacio y la barra invertida;
- claves y valores de etiqueta, y clave de campo: coma, igual, espacio y
  barra invertida.

Cuatro trampas que el constructor oficial no cubre porque asume entradas
limpias, y que aqui si llegan desde los agentes:

1. Una etiqueta con valor vacio invalida la linea entera. Se escribe el valor
   explicito "desconocido".
2. Un salto de linea dentro de un valor parte la linea en dos, y un
   tabulador el motor lo toma como separador y no tiene escape. Todo
   caracter de control se reemplaza por un espacio.
3. NaN e infinito no son flotantes validos en el protocolo. El punto no se
   escribe y quien llama lo contabiliza como punto sin valor.
4. Un entero sin sufijo es flotante, y uno con sufijo "i" es entero. Si la
   misma columna recibe los dos tipos, el motor rechaza la escritura. Todo
   valor se escribe como flotante.
5. El analizador del motor rechaza la linea entera si una medicion, una clave
   o un valor de etiqueta termina en barra invertida, aunque venga escapada
   (regla EndsWithBackslash de core/influxdb_line_protocol/src/lib.rs). Las
   barras invertidas finales se quitan.
6. La marca de tiempo es un entero de 64 bits con signo; OTLP la manda sin
   signo. Una fuera de rango invalida la linea, asi que quien llama recibe
   None y la cuenta como error.
7. Un valor de etiqueta de mas de 64 KiB invalida la linea. Se recorta a
   1024 caracteres, que es mas de lo que cualquier etiqueta util necesita.
"""
import math
import re
from typing import Mapping, Optional

from .esquema import DESCONOCIDO

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_LARGO_MAXIMO = 1024
_TIEMPO_MAXIMO = 2**63 - 1


def _escapar(texto: str, especiales: str) -> str:
    texto = _CONTROL.sub(" ", texto)[:_LARGO_MAXIMO]
    salida = []
    for c in texto:
        if c in especiales or c == "\\":
            salida.append("\\")
        salida.append(c)
    return "".join(salida)


def _sin_barra_final(texto: str) -> str:
    return _CONTROL.sub(" ", texto)[:_LARGO_MAXIMO].rstrip("\\")


def escapar_medicion(texto: str) -> str:
    return _escapar(_sin_barra_final(texto), ", ")


def escapar_clave_o_valor(texto: str) -> str:
    return _escapar(_sin_barra_final(texto), ",= ")


def formatear_flotante(valor: float) -> Optional[str]:
    """Devuelve la representacion del flotante, o None si no es escribible."""
    valor = float(valor)
    if math.isnan(valor) or math.isinf(valor):
        return None
    texto = repr(valor)
    # repr(1.0) da "1.0" y repr(1e20) da "1e+20"; ambos son flotantes validos.
    return texto


def construir_linea(
    medicion: str,
    etiquetas: Mapping[str, str],
    valor: float,
    tiempo_ns: int,
    campo: str = "value",
) -> Optional[str]:
    """Una linea completa, o None si el valor no es representable.

    Las etiquetas se ordenan por clave, que es lo que recomienda el motor para
    escribir mas rapido y deja las lineas comparables en las pruebas.
    """
    texto_valor = formatear_flotante(valor)
    if texto_valor is None:
        return None
    if not 0 <= int(tiempo_ns) <= _TIEMPO_MAXIMO:
        return None

    partes = [escapar_medicion(medicion)]
    for clave in sorted(etiquetas):
        valor_etiqueta = _sin_barra_final(str(etiquetas[clave]).strip()).strip() or DESCONOCIDO
        partes.append(
            f",{escapar_clave_o_valor(clave)}={escapar_clave_o_valor(valor_etiqueta)}"
        )
    partes.append(f" {escapar_clave_o_valor(campo)}={texto_valor} {int(tiempo_ns)}")
    return "".join(partes)
