"""El proveedor del modelo de lenguaje (H-040; version fija por RNF-REP-01).

La version del modelo se fija en `ZENIT_MODELO_DIAGNOSTICO` y se guarda con
cada diagnostico. No se acepta un alias que cambie solo (`...-latest`): un
diagnostico tiene que poder repetirse con el mismo modelo. Desde la generacion
4.6, los ids sin fecha (por ejemplo `claude-sonnet-5-5`) ya son versiones
fijas.

Sobre los parametros, segun la documentacion de los modelos 5.5:

- `temperature` distinto del valor por defecto devuelve 400, asi que no se
  envia. La reproducibilidad sale de la version fija, del prompt y de que el
  diagnostico guarda lo que se envio.
- El pensamiento esta activo por defecto. Con `thinking: between_tools` y sin
  herramientas, el modelo responde sin pensar antes: mas rapido y mas barato,
  dentro de los 60 s de RNF-REN-06.
- `output_config.effort` baja el tamano de toda la respuesta; por defecto
  `low`, configurable con `ZENIT_DIAGNOSTICO_ESFUERZO`.

El cliente usa la API de mensajes por HTTP directo (urllib), sin SDK: una
dependencia menos en la imagen de la API, y en las pruebas se reemplaza por
un doble con el mismo metodo `generar`.
"""
from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass

URL_MENSAJES = "https://api.anthropic.com/v1/messages"
VERSION_API = "2023-06-01"
TIEMPO_LIMITE_S = 40          # deja margen dentro de los 60 s de RNF-REN-06
TOKENS_SALIDA = 2000
ESFUERZOS = ("low", "medium", "high")   # xhigh y max no admiten between_tools


class ErrorModelo(Exception):
    """El proveedor no respondio o respondio algo que no sirve."""


class SinConfiguracion(ErrorModelo):
    """Falta la version del modelo o la clave del proveedor."""


def modelo_configurado() -> str:
    """La version fija del modelo, de `ZENIT_MODELO_DIAGNOSTICO`."""
    modelo = os.environ.get("ZENIT_MODELO_DIAGNOSTICO", "").strip()
    if not modelo:
        raise SinConfiguracion("falta ZENIT_MODELO_DIAGNOSTICO (una version fija del modelo)")
    if modelo.endswith("-latest"):
        raise SinConfiguracion(f"{modelo!r} es un alias que cambia solo; usa una version fija")
    return modelo


@dataclass(frozen=True)
class Respuesta:
    texto: str
    tokens_entrada: int
    tokens_salida: int
    modelo: str


class ClienteAnthropic:
    def __init__(self, clave: str, modelo: str, url: str = URL_MENSAJES, tiempo_limite: float = TIEMPO_LIMITE_S,
                 esfuerzo: str = "low"):
        if esfuerzo not in ESFUERZOS:
            raise SinConfiguracion(f"esfuerzo {esfuerzo!r}: se espera uno de {', '.join(ESFUERZOS)}")
        self.clave, self.modelo, self.url, self.tiempo_limite = clave, modelo, url, tiempo_limite
        self.esfuerzo = esfuerzo

    @classmethod
    def desde_entorno(cls) -> "ClienteAnthropic":
        clave = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not clave:
            raise SinConfiguracion("falta ANTHROPIC_API_KEY")
        return cls(clave, modelo_configurado(),
                   esfuerzo=os.environ.get("ZENIT_DIAGNOSTICO_ESFUERZO", "low").strip() or "low")

    def generar(self, sistema: str, mensaje: str, max_tokens: int = TOKENS_SALIDA) -> Respuesta:
        cuerpo = json.dumps({
            "model": self.modelo,
            "max_tokens": max_tokens,
            "thinking": {"type": "between_tools"},      # sin pensamiento previo: no hay herramientas
            "output_config": {"effort": self.esfuerzo},
            "system": sistema,
            "messages": [{"role": "user", "content": mensaje}],
        }).encode()
        peticion = urllib.request.Request(self.url, data=cuerpo, method="POST", headers={
            "x-api-key": self.clave,
            "anthropic-version": VERSION_API,
            "content-type": "application/json",
        })
        try:
            with urllib.request.urlopen(peticion, timeout=self.tiempo_limite) as r:
                datos = json.load(r)
        except urllib.error.HTTPError as e:
            detalle = e.read()[:300].decode(errors="replace")
            raise ErrorModelo(f"el proveedor respondio {e.code}: {detalle}") from None
        except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
            raise ErrorModelo(f"el proveedor no respondio: {e}") from None
        except ValueError:
            raise ErrorModelo("el proveedor respondio algo que no es JSON") from None
        if datos.get("stop_reason") == "max_tokens":
            raise ErrorModelo("la respuesta del modelo quedo cortada (max_tokens)")
        texto = "".join(b.get("text", "") for b in datos.get("content", []) if b.get("type") == "text")
        uso = datos.get("usage") or {}
        return Respuesta(texto=texto, tokens_entrada=int(uso.get("input_tokens", 0)),
                         tokens_salida=int(uso.get("output_tokens", 0)), modelo=datos.get("model") or self.modelo)
