"""Ejecucion de inyecciones y campana desatendida (H-024, H-030).

ejecutar() es el unico camino para inyectar, a mano o desde la campana, y
garantiza el orden que exige la verdad de referencia:

    1. registrar el inicio (en disco)  2. inyectar  3. restaurar  4. registrar el fin

La campana sigue un calendario generado con una semilla. La configuracion con
la que nacio (semilla, intervalos, tipos, calentamiento) queda guardada en
campana.json, asi que con el mismo archivo las inyecciones son las mismas
aunque alguien cambie el .env despues. Si el nodo se reinicia, el calendario
se regenera igual y la campana sigue con la siguiente inyeccion futura; las
que cayeron mientras estaba apagado no se recuperan.

Cada cierre dice que paso con el efecto, no solo si la inyeccion termino:
efecto = ninguno (no se llego a provocar), parcial (se corto) o completo, con
los instantes reales en que empezo y termino. Solo las completadas cuentan
como verdad de referencia, pero la evaluacion puede usar las parciales para
no castigar al detector por algo que si ocurrio.
"""
from __future__ import annotations

import fcntl
import json
import os
import random
import threading
import time
import zlib
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterator, Optional

from .fallas import Caida, Cpu, Disco, ErrorFalla, Falla, Memoria, contenedor_de, levantar, limpiar_relleno
from .verdad import Registro, ahora, iso


# ------------------------------------------------------------------ configuracion
TIPOS_CAMPANA = ("cpu", "memoria", "disco_gradual", "disco_abrupto", "caida")
GUARDADOS = ("semilla_efectiva", "calentamiento_h", "intervalo_min", "intervalo_max", "normal_min",
             "tipos", "servicios_caida")


class ConfigInvalida(SystemExit):
    pass


@dataclass
class Config:
    nodo: str
    ruta_verdad: Path = Path("/var/lib/zenit-lab/verdad.jsonl")
    carpeta_relleno: Path = Path("/datos/relleno")
    proyecto: str = "zenit-laboratorio"
    servicios_caida: tuple = ("catalogo", "pedidos")
    semilla: int = 1
    calentamiento_h: float = 12.0
    intervalo_min: float = 60.0     # minutos entre el inicio de una inyeccion y el de la siguiente
    intervalo_max: float = 150.0
    normal_min: float = 45.0        # minutos de normalidad minimos entre el fin de una y la siguiente
    tipos: tuple = TIPOS_CAMPANA
    semilla_efectiva: Optional[int] = None  # la de campana.json, si ya existe

    @classmethod
    def desde_entorno(cls) -> "Config":
        e = os.environ
        nodo = e.get("ZENIT_NODO")
        if not nodo:
            raise ConfigInvalida("falta ZENIT_NODO")
        try:
            return cls(
                nodo=nodo,
                ruta_verdad=Path(e.get("RUTA_VERDAD", "/var/lib/zenit-lab/verdad.jsonl")),
                carpeta_relleno=Path(e.get("CARPETA_RELLENO", "/datos/relleno")),
                proyecto=e.get("PROYECTO", "zenit-laboratorio"),
                servicios_caida=tuple(x.strip() for x in e.get("SERVICIOS_CAIDA", "catalogo,pedidos").split(",") if x.strip()),
                semilla=int(e.get("CAMPANA_SEMILLA", "1")),
                calentamiento_h=float(e.get("CALENTAMIENTO_H", "12")),
                intervalo_min=float(e.get("INTERVALO_MIN", "60")),
                intervalo_max=float(e.get("INTERVALO_MAX", "150")),
                normal_min=float(e.get("NORMAL_MIN", "45")),
                tipos=tuple(t.strip() for t in e.get("CAMPANA_TIPOS", ",".join(TIPOS_CAMPANA)).split(",") if t.strip()),
            )
        except ValueError as ex:
            raise ConfigInvalida(f"configuracion de la campana no valida: {ex}") from ex

    def validar(self) -> "Config":
        errores = []
        malos = [t for t in self.tipos if t not in TIPOS_CAMPANA]
        if malos:
            errores.append(f"CAMPANA_TIPOS tiene tipos que la campana no inyecta: {', '.join(malos)} "
                           f"(validos: {', '.join(TIPOS_CAMPANA)})")
        if not self.tipos:
            errores.append("CAMPANA_TIPOS esta vacio")
        if "caida" in self.tipos and not self.servicios_caida:
            errores.append("hay caidas en CAMPANA_TIPOS pero SERVICIOS_CAIDA esta vacio")
        if not 0 < self.intervalo_min <= self.intervalo_max:
            errores.append("hace falta 0 < INTERVALO_MIN <= INTERVALO_MAX")
        if self.normal_min < 0 or self.calentamiento_h < 0:
            errores.append("NORMAL_MIN y CALENTAMIENTO_H no pueden ser negativos")
        if errores:
            raise ConfigInvalida("; ".join(errores))
        return self

    def semilla_del_nodo(self) -> int:
        if self.semilla_efectiva is not None:
            return self.semilla_efectiva
        # dos nodos con la misma semilla inyectarian a la misma hora; se mezcla con el nombre
        return self.semilla * 1_000_003 + zlib.crc32(self.nodo.encode())

    def guardables(self) -> dict:
        d = {k: getattr(self, k) for k in GUARDADOS if k != "semilla_efectiva"}
        d["semilla_efectiva"] = self.semilla_del_nodo()
        d["tipos"], d["servicios_caida"] = list(self.tipos), list(self.servicios_caida)
        return d

    def con_guardados(self, datos: dict) -> "Config":
        """La configuracion con la que nacio la campana manda sobre la del entorno."""
        cambios = {k: datos[k] for k in GUARDADOS if k in datos}
        for k in ("tipos", "servicios_caida"):
            if k in cambios:
                cambios[k] = tuple(cambios[k])
        return replace(self, **cambios)


# ------------------------------------------------------------------ especificaciones
def fabricar(spec: dict, cfg: Config, cliente_docker=None) -> Falla:
    """De una especificacion (diccionario serializable) a un fallo listo para iniciar."""
    tipo, dur = spec["tipo"], int(spec["duracion_s"])
    base = {k: spec[k] for k in ("campana", "indice", "semilla") if k in spec}
    if tipo == "cpu":
        return Cpu(duracion_s=dur, carga=spec["carga"], parametros=base)
    if tipo == "memoria":
        return Memoria(duracion_s=dur, objetivo=spec["objetivo"], parametros=base)
    if tipo in ("disco_gradual", "disco_abrupto", "disco"):
        modo = spec.get("modo") or tipo.split("_")[1]
        return Disco(duracion_s=dur, objetivo=spec["objetivo"], modo=modo,
                     carpeta=cfg.carpeta_relleno, parametros=base)
    if tipo == "caida":
        servicio = spec["servicio"]
        return Caida(duracion_s=dur, servicio=servicio, proyecto=cfg.proyecto,
                     cliente=cliente_docker, parametros=base)
    raise ValueError(f"tipo de inyeccion desconocido: {tipo}")


def sortear(rng: random.Random, tipo: str, cfg: Config) -> dict:
    """Parametros al azar dentro de los rangos de cada tipo."""
    if tipo == "cpu":
        return {"tipo": tipo, "carga": rng.randint(50, 90), "duracion_s": rng.randint(5, 15) * 60}
    if tipo == "memoria":
        return {"tipo": tipo, "objetivo": round(rng.uniform(0.65, 0.85), 2), "duracion_s": rng.randint(5, 15) * 60}
    if tipo == "disco_gradual":
        return {"tipo": tipo, "objetivo": round(rng.uniform(0.70, 0.85), 2), "duracion_s": rng.randint(10, 20) * 60}
    if tipo == "disco_abrupto":
        return {"tipo": tipo, "objetivo": round(rng.uniform(0.70, 0.85), 2), "duracion_s": rng.randint(5, 15) * 60}
    if tipo == "caida":
        return {"tipo": tipo, "servicio": rng.choice(list(cfg.servicios_caida)), "duracion_s": rng.randint(3, 10) * 60}
    raise ValueError(f"tipo de inyeccion desconocido: {tipo}")


def calendario(cfg: Config, inicio: datetime, campana: str) -> Iterator[dict]:
    """Secuencia infinita y reproducible de inyecciones planeadas."""
    semilla = cfg.semilla_del_nodo()
    rng = random.Random(semilla)
    momento = inicio + timedelta(hours=cfg.calentamiento_h)
    fin_anterior: Optional[datetime] = None
    indice = 0
    while True:
        if indice > 0:
            momento = momento + timedelta(minutes=rng.uniform(cfg.intervalo_min, cfg.intervalo_max))
            minimo = fin_anterior + timedelta(minutes=cfg.normal_min)
            momento = max(momento, minimo)
        spec = sortear(rng, rng.choice(list(cfg.tipos)), cfg)
        spec.update({"indice": indice, "momento": momento, "campana": campana, "semilla": semilla})
        yield spec
        fin_anterior = momento + timedelta(seconds=spec["duracion_s"])
        indice += 1


# ------------------------------------------------------------------ ejecucion
class Ocupado(Exception):
    """Ya hay una inyeccion en curso en este nodo."""


@contextmanager
def candado_del_nodo(ruta_verdad: Path, esperar: bool):
    """Nunca dos inyecciones a la vez en el mismo nodo, tampoco una manual y la campana."""
    ruta = Path(ruta_verdad).with_name("inyector.lock")
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with open(ruta, "w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | (0 if esperar else fcntl.LOCK_NB))
        except BlockingIOError as e:
            raise Ocupado("ya hay una inyeccion en curso en este nodo") from e
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _escribir_atomico(ruta: Path, datos: dict) -> None:
    tmp = ruta.with_suffix(ruta.suffix + ".tmp")
    tmp.write_text(json.dumps(datos, indent=2, ensure_ascii=False))
    os.replace(tmp, ruta)


LATIDO_S = 15


class Latido:
    """Mientras dura una inyeccion, deja cada 15 s la hora en un archivo.

    Si el nodo se apaga a mitad, al volver ese archivo dice hasta cuando duro
    el efecto, y el cierre usa esa hora en vez de la del reinicio.
    """

    def __init__(self, ruta: Path, uid: str):
        self.ruta, self.uid = ruta, uid
        self._fin = threading.Event()
        self._hilo = threading.Thread(target=self._latir, daemon=True)

    def _latir(self):
        while True:
            try:
                _escribir_atomico(self.ruta, {"uid": self.uid, "momento": iso(ahora())})
            except OSError:
                pass
            if self._fin.wait(LATIDO_S):
                return

    def __enter__(self):
        self._hilo.start()
        return self

    def __exit__(self, *_):
        self._fin.set()
        self._hilo.join(timeout=5)
        self.ruta.unlink(missing_ok=True)


def ejecutar(falla: Falla, registro: Registro, parar: threading.Event,
             avisar: Callable[[str], None] = print) -> tuple[str, str, Optional[str]]:
    """Registra, inyecta, restaura y cierra. Devuelve (uid, estado, motivo)."""
    uid = registro.abrir(falla.tipo, falla.intensidad, falla.parametros,
                         causa_en=falla.causa_en, manifiesta_en=falla.manifiesta_en)
    avisar(f"inicio {uid} intensidad={falla.intensidad} duracion={falla.duracion_s}s")
    estado, motivo, efecto = "completada", None, "ninguno"
    efecto_inicio = efecto_fin = None
    latido = Latido(registro.ruta.with_name("latido.json"), uid)
    try:
        with latido:
            try:
                falla.iniciar()
                efecto, efecto_inicio = "parcial", ahora()
                falla.mantener(time.monotonic() + falla.duracion_s, parar)
                if parar.is_set():
                    estado, motivo = "fallida", "interrumpida"
                else:
                    efecto = "completo"
            except ErrorFalla as e:
                estado, motivo = "fallida", str(e)
            except Exception as e:  # un error inesperado tambien deja la inyeccion cerrada y el nodo restaurado
                estado, motivo = "fallida", f"error inesperado: {type(e).__name__}: {e}"
            finally:
                if efecto != "ninguno":
                    efecto_fin = ahora()
                try:
                    falla.restaurar()
                except Exception as e:
                    estado = "fallida"
                    motivo = f"{motivo + '; ' if motivo else ''}no se pudo restaurar: {e}"
    finally:
        extra = {**falla.extra_cierre(), "efecto": efecto}
        if efecto_inicio:
            extra["efecto_inicio"] = iso(efecto_inicio)
        if efecto_fin:
            extra["efecto_fin"] = iso(efecto_fin)
        registro.cerrar(uid, estado, motivo, extra=extra)
        avisar(f"fin {uid} {estado} efecto={efecto}{' (' + motivo + ')' if motivo else ''}")
    return uid, estado, motivo


def _cliente(cliente_docker):
    if cliente_docker is None:
        import docker
        cliente_docker = docker.from_env()
    return cliente_docker


def asegurar_servicios(cfg: Config, cliente_docker=None, avisar: Callable[[str], None] = print):
    """Todo servicio que la campana puede tumbar tiene que estar arriba antes de seguir.

    Cubre el caso en que una restauracion fallo (por ejemplo, Docker apagandose
    durante un reinicio del nodo) y el servicio quedo detenido: Docker no lo
    vuelve a arrancar solo porque lo detuvo la API.
    """
    if not cfg.servicios_caida:
        return cliente_docker
    try:
        cliente_docker = _cliente(cliente_docker)
    except Exception as e:
        avisar(f"sin acceso a Docker, no se revisan los servicios: {e}")
        return cliente_docker
    for servicio in cfg.servicios_caida:
        try:
            c = contenedor_de(cliente_docker, cfg.proyecto, servicio)
            c.reload()
            if c.status != "running":
                avisar(f"{servicio} estaba detenido fuera de una inyeccion: se levanta")
                levantar(c)
        except Exception as e:
            avisar(f"no se pudo revisar {servicio}: {e}")
    return cliente_docker


def recuperar(registro: Registro, cfg: Config, cliente_docker=None,
              avisar: Callable[[str], None] = print) -> int:
    """Cierra lo que una interrupcion dejo abierto y devuelve el nodo a la normalidad."""
    ruta_latido = registro.ruta.with_name("latido.json")
    latido = {}
    if ruta_latido.exists():
        try:
            latido = json.loads(ruta_latido.read_text())
        except (OSError, json.JSONDecodeError):
            latido = {}
    # Primero se restaura y despues se cierra: un servicio detenido o un disco
    # lleno siguen afectando al nodo hasta este momento, no hasta el apagado.
    n = limpiar_relleno(cfg.carpeta_relleno)
    if n:
        avisar(f"borrados {n} rellenos de disco")
    asegurar_servicios(cfg, cliente_docker, avisar)
    restaurado = ahora()
    abiertas = registro.abiertas()
    for e in abiertas:
        if e.get("tipo") in ("cpu", "memoria"):
            # el efecto murio con el contenedor: duro, como mucho, hasta el ultimo latido
            momento = None
            if latido.get("uid") == e["uid"]:
                momento = datetime.fromisoformat(latido["momento"].replace("Z", "+00:00")) + timedelta(seconds=LATIDO_S)
        else:
            momento = restaurado  # disco y caida duraron hasta que se restauraron recien
        extra = {"efecto": "parcial", "fin_estimado": True, "efecto_fin": iso(momento or ahora())}
        registro.cerrar(e["uid"], "fallida", "interrumpida: el inyector se detuvo a mitad",
                        extra=extra, momento=momento)
        avisar(f"cerrada como fallida {e['uid']}")
    ruta_latido.unlink(missing_ok=True)
    return len(abiertas)


def estado_campana(cfg: Config, momento: Optional[datetime] = None, crear: bool = True,
                   avisar: Callable[[str], None] = print) -> dict:
    """La campana nace una vez por nodo; reiniciar el contenedor no reinicia el calendario.

    Con crear=False (la orden plan) no se guarda nada: mirar el plan no arranca el reloj.
    """
    ruta = Path(cfg.ruta_verdad).with_name("campana.json")
    if ruta.exists():
        try:
            return json.loads(ruta.read_text())
        except json.JSONDecodeError:
            danado = ruta.with_name(f"campana.json.danado-{int(time.time())}")
            os.replace(ruta, danado)
            avisar(f"campana.json no se podia leer; quedo como {danado.name} y se crea uno nuevo")
    momento = momento or ahora()
    datos = {"campana": f"c-{cfg.nodo}-{momento.strftime('%Y%m%d')}", "inicio": iso(momento),
             **cfg.guardables()}
    if crear:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        _escribir_atomico(ruta, datos)
    return datos


def _fecha(texto: str) -> datetime:
    return datetime.fromisoformat(texto.replace("Z", "+00:00"))


def correr(cfg: Config, parar: threading.Event, cliente_docker=None,
           avisar: Callable[[str], None] = print, reloj: Callable[[], datetime] = ahora) -> None:
    registro = Registro(cfg.ruta_verdad, cfg.nodo)
    with candado_del_nodo(cfg.ruta_verdad, esperar=True):
        recuperar(registro, cfg, cliente_docker, avisar)  # aunque el entorno tenga un error, el nodo queda sano
    if not Path(cfg.ruta_verdad).with_name("campana.json").exists():
        cfg.validar()  # una campana nueva nace del entorno: tiene que ser valido
    est = estado_campana(cfg, avisar=avisar)
    plan = cfg.con_guardados(est).validar()
    distintos = [k for k in GUARDADOS if k != "semilla_efectiva" and getattr(plan, k) != getattr(cfg, k)]
    if distintos:
        avisar(f"aviso: el entorno cambio {', '.join(distintos)}; la campana sigue con lo de campana.json")
    avisar(f"campana {est['campana']} desde {est['inicio']}, calentamiento {plan.calentamiento_h} h")
    ultimo_fin: Optional[datetime] = None
    for spec in calendario(plan, _fecha(est["inicio"]), est["campana"]):
        if parar.is_set():
            return
        if (spec["momento"] - reloj()).total_seconds() < -60:
            continue  # cayo mientras el nodo estaba apagado: no se inyecta tarde
        objetivo = spec["momento"]
        if ultimo_fin is not None:  # la normalidad se cuenta desde el fin real, no desde el planeado
            objetivo = max(objetivo, ultimo_fin + timedelta(minutes=plan.normal_min))
        anunciado = False
        while True:  # se mira el reloj de pared cada minuto: una VM suspendida no corre los temporizadores
            espera = (objetivo - reloj()).total_seconds()
            if espera <= 0:
                break
            if not anunciado:
                avisar(f"siguiente: #{spec['indice']} {spec['tipo']} a las {iso(objetivo)}")
                anunciado = True
            if parar.wait(min(espera, 60)):
                return
        if (reloj() - objetivo).total_seconds() > 120:
            continue  # la VM estuvo suspendida durante la espera: tampoco se inyecta tarde
        with candado_del_nodo(cfg.ruta_verdad, esperar=True):
            cliente_docker = asegurar_servicios(plan, cliente_docker, avisar)
            ejecutar(fabricar(spec, plan, cliente_docker), registro, parar, avisar)
        ultimo_fin = reloj()
