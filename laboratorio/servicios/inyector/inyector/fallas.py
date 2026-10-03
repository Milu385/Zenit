"""Los fallos que el inyector sabe provocar (H-025, H-026, H-028).

Cada fallo tiene tres pasos: iniciar, mantener hasta su fin y restaurar.
Restaurar se llama siempre, tambien si iniciar o mantener fallaron, y es
idempotente: llamarlo dos veces no hace dano.

Topes de seguridad. Un fallo inyectado no puede tumbar lo que se esta
midiendo: si el agente muere, la inyeccion queda sin datos y no sirve. Por
eso la CPU no pasa del 90 %, la memoria ocupada no pasa del 85 % del total y
el disco no pasa del 85 % del sistema de archivos.
"""
from __future__ import annotations

import errno
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .verdad import ahora, iso

MIB = 1024 * 1024
TOPE_CPU = 90          # %
TOPE_MEMORIA = 0.85    # fraccion del total ocupada al final
TOPE_DISCO = 0.85      # fraccion del sistema de archivos ocupada al final
PASO_DISCO_S = 30      # cada cuanto crece la saturacion gradual


class ErrorFalla(Exception):
    """El fallo no se pudo provocar o se interrumpio: la inyeccion es fallida."""


class SinMargen(ErrorFalla):
    """El nodo ya esta por encima del tope: inyectar lo pondria en riesgo."""


@dataclass
class Falla:
    tipo: str = ""
    intensidad: str = ""
    duracion_s: int = 300
    parametros: dict = field(default_factory=dict)
    causa_en: Optional[str] = None
    manifiesta_en: Optional[str] = None
    # lo que solo se sabe al iniciar (bytes reservados, ocupacion previa); va en la linea de fin
    detalle: dict = field(default_factory=dict)

    def iniciar(self) -> None:
        raise NotImplementedError

    def mantener(self, hasta: float, parar: threading.Event) -> None:
        """Espera hasta el instante `hasta` (time.monotonic) o hasta que pidan parar."""
        while not parar.is_set():
            restante = hasta - time.monotonic()
            if restante <= 0:
                return
            parar.wait(min(1.0, restante))
            self.revisar()

    def revisar(self) -> None:
        """Se llama cada segundo mientras dura. Lanza ErrorFalla si el efecto se perdio."""

    def restaurar(self) -> None:
        raise NotImplementedError

    def extra_cierre(self) -> dict:
        return dict(self.detalle)


# ------------------------------------------------------------------ procesos
def _detener_grupo(proc: Optional[subprocess.Popen]) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=5)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=5)


@dataclass
class _ConStress(Falla):
    _proc: Optional[subprocess.Popen] = field(default=None, repr=False)
    _hasta: float = field(default=0.0, repr=False)

    def comando(self) -> list[str]:
        raise NotImplementedError

    def iniciar(self) -> None:
        try:
            self._proc = subprocess.Popen(
                self.comando(), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                start_new_session=True,  # para poder detener a stress-ng y a sus hijos juntos
            )
        except FileNotFoundError as e:
            raise ErrorFalla("stress-ng no esta instalado") from e
        self._hasta = time.monotonic() + self.duracion_s
        time.sleep(0.5)
        if self._proc.poll() not in (None, 0):
            raise ErrorFalla(f"stress-ng termino al arrancar: {self._proc.stderr.read().decode()[:200]}")

    def revisar(self) -> None:
        # stress-ng tiene su propio --timeout; si termina antes de tiempo, el efecto se perdio
        if self._proc is not None and self._proc.poll() is not None and time.monotonic() < self._hasta - 3:
            raise ErrorFalla(f"stress-ng termino antes de tiempo (codigo {self._proc.returncode})")

    def restaurar(self) -> None:
        _detener_grupo(self._proc)


@dataclass
class Cpu(_ConStress):
    """Agotamiento de CPU en todos los nucleos, a una carga fija (H-025)."""
    carga: int = 80

    def __post_init__(self):
        self.tipo = "cpu"
        self.carga = max(10, min(int(self.carga), TOPE_CPU))
        self.intensidad = f"{self.carga}%"
        self.parametros = {**self.parametros, "duracion_s": self.duracion_s, "carga": self.carga}

    def comando(self) -> list[str]:
        return ["stress-ng", "--cpu", "0", "--cpu-load", str(self.carga),
                "--timeout", f"{self.duracion_s + 5}s", "--quiet"]


def leer_meminfo(ruta: str = "/proc/meminfo") -> tuple[int, int]:
    """(total, disponible) en bytes. Dentro del contenedor, /proc/meminfo es el del anfitrion."""
    valores = {}
    with open(ruta) as f:
        for linea in f:
            clave, _, resto = linea.partition(":")
            valores[clave] = int(resto.split()[0]) * 1024
    return valores["MemTotal"], valores["MemAvailable"]


@dataclass
class Memoria(_ConStress):
    """Ocupa memoria hasta una fraccion objetivo del total del nodo (H-025)."""
    objetivo: float = 0.75
    leer: Callable[[], tuple[int, int]] = field(default=leer_meminfo, repr=False)
    _bytes: int = field(default=0, repr=False)

    def __post_init__(self):
        self.tipo = "memoria"
        self.objetivo = min(float(self.objetivo), TOPE_MEMORIA)
        self.intensidad = f"{round(self.objetivo * 100)}%"
        self.parametros = {**self.parametros, "duracion_s": self.duracion_s, "objetivo": self.objetivo}

    def iniciar(self) -> None:
        total, disponible = self.leer()
        ocupado = total - disponible
        self._bytes = int(self.objetivo * total - ocupado)
        self.detalle = {"bytes": max(self._bytes, 0), "ocupado_antes": round(ocupado / total, 3)}
        if self._bytes < 64 * MIB:
            raise SinMargen(f"memoria ya ocupada al {ocupado / total:.0%}, objetivo {self.objetivo:.0%}")
        super().iniciar()

    def comando(self) -> list[str]:
        # --vm-keep y --vm-hang 0: reserva y toca la memoria una vez y la retiene sin gastar CPU
        return ["stress-ng", "--vm", "1", "--vm-bytes", str(self._bytes), "--vm-keep",
                "--vm-hang", "0", "--timeout", f"{self.duracion_s + 5}s", "--quiet"]


# ------------------------------------------------------------------ disco
def uso_disco(carpeta: Path) -> tuple[int, int]:
    """(total, ocupado) en bytes del sistema de archivos que contiene la carpeta."""
    s = os.statvfs(carpeta)
    total = s.f_blocks * s.f_frsize
    return total, total - s.f_bfree * s.f_frsize


def _reservar(fd: int, desde: int, cuanto: int) -> None:
    try:
        os.posix_fallocate(fd, desde, cuanto)
    except OSError as e:
        if e.errno not in (errno.EOPNOTSUPP, errno.EINVAL):
            raise
        os.lseek(fd, desde, os.SEEK_SET)  # sistema de archivos sin fallocate: escribir ceros
        bloque = b"\0" * (4 * MIB)
        quedan = cuanto
        while quedan > 0:
            quedan -= os.write(fd, bloque[: min(len(bloque), quedan)])
    os.fsync(fd)


@dataclass
class Disco(Falla):
    """Llena el sistema de archivos del nodo hasta una fraccion objetivo (H-026).

    gradual: crece cada 30 s durante el 80 % de la duracion y se sostiene.
    abrupto: llega al objetivo de una vez y se sostiene.
    """
    carpeta: Path = Path("/datos/relleno")
    objetivo: float = 0.80
    modo: str = "gradual"
    uso: Callable[[Path], tuple[int, int]] = field(default=uso_disco, repr=False)
    minimo: int = 256 * MIB   # por debajo de esto no hay saturacion que valga la pena
    _archivo: Optional[Path] = field(default=None, repr=False)
    _pendiente: list = field(default_factory=list, repr=False)
    _escrito: int = field(default=0, repr=False)

    def __post_init__(self):
        if self.modo not in ("gradual", "abrupto"):
            raise ValueError(f"modo de disco desconocido: {self.modo}")
        self.tipo = "disco"
        self.carpeta = Path(self.carpeta)
        self.objetivo = min(float(self.objetivo), TOPE_DISCO)
        self.intensidad = f"{round(self.objetivo * 100)}%"
        self.parametros = {**self.parametros, "duracion_s": self.duracion_s, "modo": self.modo,
                           "objetivo": self.objetivo}

    def iniciar(self) -> None:
        self.carpeta.mkdir(parents=True, exist_ok=True)
        total, ocupado = self.uso(self.carpeta)
        faltan = int(self.objetivo * total - ocupado)
        self.detalle = {"bytes": max(faltan, 0), "ocupado_antes": round(ocupado / total, 3)}
        if faltan < self.minimo:
            raise SinMargen(f"disco ya ocupado al {ocupado / total:.0%}, objetivo {self.objetivo:.0%}")
        self._archivo = self.carpeta / f"relleno-{int(time.time())}.bin"
        if self.modo == "abrupto":
            pasos = [faltan]
        else:
            n = max(1, int(self.duracion_s * 0.8 / PASO_DISCO_S))
            pasos = [faltan // n] * n
            pasos[-1] += faltan - sum(pasos)
        self._pendiente = pasos
        self._crecer()

    def _crecer(self) -> None:
        cuanto = self._pendiente.pop(0)
        fd = os.open(self._archivo, os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            _reservar(fd, self._escrito, cuanto)
        finally:
            os.close(fd)
        self._escrito += cuanto

    def mantener(self, hasta: float, parar: threading.Event) -> None:
        paso = PASO_DISCO_S
        siguiente = time.monotonic() + paso
        while not parar.is_set():
            momento = time.monotonic()
            if momento >= hasta:
                return
            if self._pendiente and momento >= siguiente:
                self._crecer()
                siguiente += paso
            parar.wait(min(1.0, paso, hasta - momento))

    def restaurar(self) -> None:
        if self._archivo is not None:
            try:
                self._archivo.unlink()
            except FileNotFoundError:
                pass


def limpiar_relleno(carpeta: Path) -> int:
    """Borra rellenos de disco que una interrupcion haya dejado. Devuelve cuantos."""
    n = 0
    for archivo in Path(carpeta).glob("relleno-*.bin"):
        archivo.unlink(missing_ok=True)
        n += 1
    return n


# ------------------------------------------------------------------ caida de servicio
def contenedor_de(cliente, proyecto: str, servicio: str):
    encontrados = cliente.containers.list(all=True, filters={"label": [
        f"com.docker.compose.project={proyecto}", f"com.docker.compose.service={servicio}"]})
    if not encontrados:
        raise ErrorFalla(f"no hay contenedor del servicio {servicio} en el proyecto {proyecto}")
    return encontrados[0]


def levantar(contenedor, espera_s: int = 120) -> str:
    """Arranca el contenedor si esta detenido y espera a que este en marcha y sano."""
    contenedor.reload()
    if contenedor.status != "running":
        contenedor.start()
    limite = time.monotonic() + espera_s
    while time.monotonic() < limite:
        contenedor.reload()
        salud = (contenedor.attrs.get("State", {}).get("Health") or {}).get("Status")
        if contenedor.status == "running" and salud in (None, "healthy"):
            return iso(ahora())
        time.sleep(2)
    raise ErrorFalla(f"{contenedor.name} no quedo sano en {espera_s} s")


@dataclass
class Caida(Falla):
    """Detiene un servicio de la aplicacion y lo vuelve a levantar (H-028)."""
    servicio: str = "catalogo"
    proyecto: str = "zenit-laboratorio"
    cliente: object = field(default=None, repr=False)
    _contenedor: object = field(default=None, repr=False)
    _restablecido: Optional[str] = field(default=None, repr=False)

    def __post_init__(self):
        self.tipo = "caida"
        self.intensidad = "total"
        self.parametros = {**self.parametros, "duracion_s": self.duracion_s, "servicio": self.servicio}

    def iniciar(self) -> None:
        if self.cliente is None:
            import docker  # solo hace falta dentro del contenedor del inyector
            self.cliente = docker.from_env()
        self._contenedor = contenedor_de(self.cliente, self.proyecto, self.servicio)
        self._contenedor.reload()
        if self._contenedor.status != "running":
            raise ErrorFalla(f"{self.servicio} ya estaba detenido: no seria un fallo inyectado")
        self._contenedor.stop(timeout=10)

    def revisar(self) -> None:
        # si algo lo levanta (un reinicio del nodo, una persona), el fallo dejo de existir
        self._contenedor.reload()
        if self._contenedor.status == "running":
            raise ErrorFalla(f"{self.servicio} volvio a arrancar antes de tiempo")

    def restaurar(self) -> None:
        if self._contenedor is None:
            return
        # Docker puede estar ocupado o reiniciandose. Todo cabe en ~100 s, dentro
        # del stop_grace_period del contenedor: si no, Docker lo mata antes de
        # escribir el cierre. Si aun asi no sube, la campana lo vuelve a
        # intentar antes de la siguiente inyeccion (asegurar_servicios).
        ultimo = None
        for pausa in (0, 5, 10):
            time.sleep(pausa)
            try:
                self._restablecido = levantar(self._contenedor, espera_s=25)
                return
            except Exception as e:
                ultimo = e
        raise ErrorFalla(f"{self.servicio} no se pudo levantar tras 3 intentos: {ultimo}")

    def extra_cierre(self) -> dict:
        return {"restablecido": self._restablecido} if self._restablecido else {}
