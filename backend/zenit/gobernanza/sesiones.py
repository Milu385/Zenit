"""Sesiones firmadas (sin estado en el servidor).

El token es `cuerpo.firma`, con el cuerpo en base64url y la firma HMAC-SHA256
del cuerpo con ZENIT_SECRETO_SESION. Lo entrega POST /api/sesion y viaja como
`Authorization: Bearer` (contrato, seccion 1). Solo lleva el usuario y la
caducidad: la API vuelve a leer el usuario en cada peticion, asi que uno
borrado o con otro rol pierde lo que ya no le toca sin esperar a que caduque.
"""
import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

DURACION_S = 8 * 3600


@dataclass(frozen=True)
class Sesion:
    usuario: str
    expira: int  # segundos desde la epoca


def _b64(datos: bytes) -> str:
    return base64.urlsafe_b64encode(datos).rstrip(b"=").decode()


def _desb64(texto: str) -> bytes:
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


class Sesiones:
    def __init__(self, secreto: bytes):
        if len(secreto) < 32:
            raise ValueError("el secreto de sesion debe tener al menos 32 bytes")
        self._secreto = secreto

    def _firmar(self, cuerpo: str) -> str:
        return _b64(hmac.new(self._secreto, cuerpo.encode(), hashlib.sha256).digest())

    def emitir(self, usuario: str, ahora: float | None = None) -> tuple[str, Sesion]:
        ahora = time.time() if ahora is None else ahora
        sesion = Sesion(usuario, int(ahora) + DURACION_S)
        cuerpo = _b64(json.dumps({"u": sesion.usuario, "e": sesion.expira}, separators=(",", ":")).encode())
        return f"{cuerpo}.{self._firmar(cuerpo)}", sesion

    def leer(self, ficha: str | None, ahora: float | None = None) -> Sesion | None:
        if not ficha or ficha.count(".") != 1:
            return None
        cuerpo, firma = ficha.split(".")
        if not hmac.compare_digest(firma, self._firmar(cuerpo)):
            return None
        try:
            datos = json.loads(_desb64(cuerpo))
            sesion = Sesion(str(datos["u"]), int(datos["e"]))
        except (ValueError, KeyError, TypeError):
            return None
        if sesion.expira <= (time.time() if ahora is None else ahora):
            return None
        return sesion


class Intentos:
    """Freno a la fuerza bruta: tras MAXIMO fallos seguidos de un usuario, se
    bloquea BLOQUEO_S segundos. Vive en memoria: con una sola API basta."""

    MAXIMO = 5
    BLOQUEO_S = 300

    def __init__(self):
        self._fallos: dict[str, tuple[int, float]] = {}

    def bloqueado(self, usuario: str, ahora: float | None = None) -> bool:
        ahora = time.time() if ahora is None else ahora
        n, desde = self._fallos.get(usuario, (0, 0.0))
        if n >= self.MAXIMO and ahora - desde < self.BLOQUEO_S:
            return True
        if n >= self.MAXIMO:
            self._fallos.pop(usuario, None)
        return False

    def fallo(self, usuario: str, ahora: float | None = None) -> None:
        ahora = time.time() if ahora is None else ahora
        n, _ = self._fallos.get(usuario, (0, 0.0))
        self._fallos[usuario] = (n + 1, ahora)

    def exito(self, usuario: str) -> None:
        self._fallos.pop(usuario, None)
