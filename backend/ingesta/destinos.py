"""Destinos de escritura del punto de entrada.

Dos implementaciones con la misma forma:

- DestinoInflux: el almacen real (H-005). Escribe por lotes desde un hilo
  propio y reintenta si el almacen no responde.
- DestinoJsonl: archivo de lineas JSON. Sirve para correr el punto de entrada
  sin almacen y es el destino provisional de trazas y registros hasta la
  epica 1.

El servidor no sabe cual de los dos tiene. Cambiar de destino es cambiar una
variable de entorno.
"""
import json
import logging
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable, Optional

from zenit.influx import ClienteInflux, ErrorInflux

log = logging.getLogger("ingesta.destinos")

NO_REINTENTABLES = {400, 413, 422}
REINTENTO_SIN_LIMITE = {401, 403, 404, 408, 429, 502, 503, 504}


class Lote:
    """Lo que produce una peticion OTLP: sus lineas y cuantos puntos son."""

    __slots__ = ("lineas", "puntos")

    def __init__(self, lineas: list, puntos: int):
        self.lineas = lineas
        self.puntos = puntos


class DestinoInflux:
    """Escritura por lotes con contrapresion.

    Si la cola esta llena, aceptar() devuelve False y el servidor responde
    UNAVAILABLE al agente. El Collector trata ese codigo como reintentable y
    guarda los datos en su propia cola persistente.

    Politica ante errores del almacen:

    - caida, 401, 403, 404, 408, 429, 502, 503, 504: problema de
      disponibilidad o de configuracion, no de los datos. Se reintenta sin
      limite y la cola se llena hasta devolver UNAVAILABLE a los agentes.
    - 400, 413, 422: algo en el lote es invalido. Se reintenta peticion por
      peticion para que una linea mala de un nodo no arrastre a los demas, y
      la peticion que siga fallando va al archivo de rechazos.
    - cualquier otro 5xx: cinco intentos y luego lo mismo que un 400.

    Lo rechazado se escribe en carpeta_rechazos y se cuenta en
    rechazadas_por_almacen. No hay perdida silenciosa.

    Ventana de perdida conocida: un punto aceptado vive en memoria hasta que
    se escribe (normalmente un segundo). Si el proceso muere de golpe en ese
    lapso, se pierde; el agente ya lo dio por entregado. Un apagado ordenado
    vacia la cola antes de salir (stop_grace_period en el compose).
    """

    def __init__(
        self,
        cliente: ClienteInflux,
        bd: str,
        al_escribir: Callable[[int], None],
        al_rechazar: Callable[[int], None],
        capacidad_lineas: int = 200_000,
        lineas_por_lote: int = 5_000,
        espera_lote_s: float = 1.0,
        carpeta_rechazos: Optional[Path] = None,
        intentos_5xx: int = 5,
    ):
        self._cliente = cliente
        self._bd = bd
        self._al_escribir = al_escribir
        self._al_rechazar = al_rechazar
        self._capacidad = capacidad_lineas
        self._por_lote = lineas_por_lote
        self._espera = espera_lote_s
        self._rechazos = Path(carpeta_rechazos) if carpeta_rechazos else None
        self._intentos_5xx = intentos_5xx

        self._cola: deque[Lote] = deque()
        self._lineas_en_cola = 0
        self._candado = threading.Condition()
        self._detener = False
        self.ultimo_error: Optional[str] = None
        self.reintentos = 0

        self._hilo = threading.Thread(target=self._bucle, name="escritor-influx", daemon=True)
        self._hilo.start()

    # ---------------------------------------------------------------- entrada
    def aceptar(self, lote: Lote) -> bool:
        if not lote.lineas:
            return True
        with self._candado:
            lleno = self._lineas_en_cola + len(lote.lineas) > self._capacidad
            # una peticion mas grande que toda la cola entra si la cola esta vacia
            if lleno and self._lineas_en_cola > 0:
                return False
            self._cola.append(lote)
            self._lineas_en_cola += len(lote.lineas)
            self._candado.notify()
        return True

    @property
    def pendientes(self) -> int:
        with self._candado:
            return self._lineas_en_cola

    # ---------------------------------------------------------------- salida
    def _tomar(self) -> list:
        """Espera hasta tener un lote lleno o hasta que pase el tiempo."""
        with self._candado:
            limite = time.monotonic() + self._espera
            while not self._detener and self._lineas_en_cola < self._por_lote:
                restante = limite - time.monotonic()
                if restante <= 0:
                    break
                self._candado.wait(restante)
            tomados, lineas = [], 0
            while self._cola and lineas < self._por_lote:
                lote = self._cola.popleft()
                tomados.append(lote)
                lineas += len(lote.lineas)
            return tomados

    def _dormir(self, segundos: float) -> None:
        """Espera interrumpible: detener() la corta."""
        with self._candado:
            if not self._detener:
                self._candado.wait(segundos)

    @staticmethod
    def _clase(e: ErrorInflux) -> str:
        if e.estado is None or e.estado in REINTENTO_SIN_LIMITE:
            return "esperar"
        if e.estado in NO_REINTENTABLES:
            return "invalido"
        return "servidor"

    def _escribir(self, lotes: list) -> str:
        """Intenta escribir; devuelve 'ok', 'detenido' o 'partir'."""
        espera, fallos_5xx = 1.0, 0
        cuerpo = "\n".join(l for lote in lotes for l in lote.lineas)
        while True:
            try:
                self._cliente.escribir(self._bd, cuerpo)
                self.ultimo_error = None
                return "ok"
            except ErrorInflux as e:
                self.ultimo_error = str(e)
                clase = self._clase(e)
                if clase == "invalido":
                    return "partir"
                if clase == "servidor":
                    fallos_5xx += 1
                    if fallos_5xx >= self._intentos_5xx:
                        return "partir"
                self.reintentos += 1
                log.warning("almacen no disponible, reintento en %.0fs: %s", espera, e)
            if self._detener:
                return "detenido"
            self._dormir(espera)
            if self._detener:
                return "detenido"
            espera = min(espera * 2, 30.0)

    def _rechazar(self, lote: Lote) -> None:
        log.error("peticion rechazada por el almacen (%d puntos): %s", lote.puntos, self.ultimo_error)
        if self._rechazos is not None:
            try:
                self._rechazos.mkdir(parents=True, exist_ok=True)
                archivo = self._rechazos / f"metricas-{time.strftime('%Y%m%d')}.lp"
                with archivo.open("a", encoding="utf-8") as f:
                    f.write(f"# {time.strftime('%Y-%m-%dT%H:%M:%S')} {self.ultimo_error}\n")
                    f.write("\n".join(lote.lineas) + "\n")
            except OSError:
                log.exception("no se pudo guardar el rechazo")
        self._al_rechazar(lote.puntos)

    def _bucle(self) -> None:
        while not self._detener or self.pendientes:
            tomados = self._tomar()
            if not tomados:
                if self._detener:
                    break
                continue
            n_lineas = sum(len(lote.lineas) for lote in tomados)
            try:
                resultado = self._escribir(tomados)
                if resultado == "ok":
                    self._al_escribir(sum(lote.puntos for lote in tomados))
                elif resultado == "partir":
                    # peticion por peticion: la mala no arrastra a las buenas
                    for lote in tomados:
                        r = self._escribir([lote])
                        if r == "ok":
                            self._al_escribir(lote.puntos)
                        elif r == "partir":
                            self._rechazar(lote)
                        else:  # detenido a mitad de camino: queda contado en cola
                            break
                # "detenido": no se cuenta como emitido; queda en en_cola
            except Exception:  # el hilo escritor no puede morir en silencio
                log.exception("error inesperado escribiendo un lote")
                self.ultimo_error = "error inesperado, ver registros"
                for lote in tomados:
                    self._rechazar(lote)
            finally:
                with self._candado:
                    self._lineas_en_cola -= n_lineas
            if self._detener and not self._cola:
                break

    def detener(self, esperar_s: float = 5.0) -> None:
        """Vacia lo que pueda antes de salir, sin pasar de esperar_s."""
        fin = time.monotonic() + esperar_s
        while self.pendientes and time.monotonic() < fin:
            time.sleep(0.1)
        self._detener = True
        with self._candado:
            self._candado.notify_all()
        self._hilo.join(timeout=2)


class DestinoJsonl:
    """Un archivo por senal y por dia. Escritura sincrona."""

    def __init__(self, carpeta: Path, senal: str, al_escribir: Callable[[int], None]):
        self._carpeta = Path(carpeta)
        self._carpeta.mkdir(parents=True, exist_ok=True)
        self._senal = senal
        self._al_escribir = al_escribir
        self._candado = threading.Lock()
        self.ultimo_error: Optional[str] = None
        self.reintentos = 0
        self.pendientes = 0

    def aceptar_registros(self, registros: list, puntos: int) -> bool:
        archivo = self._carpeta / f"{self._senal}-{time.strftime('%Y%m%d')}.jsonl"
        with self._candado, archivo.open("a") as f:
            for r in registros:
                f.write(json.dumps(r, default=str, ensure_ascii=False) + "\n")
        self._al_escribir(puntos)
        return True

    def detener(self, esperar_s: float = 0) -> None:
        pass
