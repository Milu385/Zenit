"""Registro de verdad de referencia (H-024).

Cada inyeccion deja dos lineas en un archivo JSONL del nodo:

    {"uid": ..., "evento": "inicio", "tipo": ..., "inicio": ..., ...}
    {"uid": ..., "evento": "fin", "fin": ..., "estado": "completada" | "fallida"}

La linea de inicio se escribe y se lleva al disco (fsync) ANTES de inyectar.
Si el nodo se apaga a mitad, la inyeccion queda registrada como abierta y la
campana la cierra como fallida al volver; nunca existe un fallo sin registro.

El agente del nodo lee este archivo con su receptor filelog y lo envia como
registros OTLP por el mismo canal que las metricas. El inyector no conoce la
direccion de la plataforma ni tiene credenciales suyas (RF-LAB-04).
"""
from __future__ import annotations

import json
import os
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

TIPOS = ("cpu", "memoria", "disco", "caida", "latencia")
ESTADOS_FINALES = ("completada", "fallida")


def ahora() -> datetime:
    return datetime.now(timezone.utc)


def iso(momento: datetime) -> str:
    return momento.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Registro:
    def __init__(self, ruta: Path, nodo: str):
        self.ruta = Path(ruta)
        self.nodo = nodo
        self._candado = threading.Lock()
        self.ruta.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ escritura
    def _escribir(self, linea: dict) -> None:
        texto = json.dumps(linea, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        if "\n" in texto:  # json.dumps no produce saltos; se asegura igual
            raise ValueError("una linea de verdad no puede tener saltos de linea")
        nuevo = not self.ruta.exists()
        with self._candado:
            fd = os.open(self.ruta, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o644)
            try:
                # Un apagado a mitad de una escritura puede dejar la ultima linea
                # sin su salto. Sin este arreglo, la linea nueva quedaria pegada
                # a la cortada y las dos se perderian al leer.
                tamano = os.fstat(fd).st_size
                prefijo = b""
                if tamano and os.pread(fd, 1, tamano - 1) != b"\n":
                    prefijo = b"\n"
                os.write(fd, prefijo + (texto + "\n").encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)
            if nuevo:  # que la entrada del directorio tambien llegue al disco
                dfd = os.open(self.ruta.parent, os.O_RDONLY)
                try:
                    os.fsync(dfd)
                finally:
                    os.close(dfd)

    def abrir(self, tipo: str, intensidad: str, parametros: dict,
              causa_en: Optional[str] = None, manifiesta_en: Optional[str] = None,
              momento: Optional[datetime] = None) -> str:
        """Registra el inicio. Devuelve el uid. Se llama ANTES de inyectar."""
        if tipo not in TIPOS:
            raise ValueError(f"tipo desconocido: {tipo}")
        momento = momento or ahora()
        uid = f"{self.nodo}-{momento.strftime('%Y%m%dT%H%M%S')}-{tipo}-{secrets.token_hex(3)}"
        linea = {
            "uid": uid, "evento": "inicio", "tipo": tipo, "nodo": self.nodo,
            "inicio": iso(momento), "intensidad": str(intensidad),
            "parametros": parametros,
        }
        if causa_en:
            linea["causa_en"] = causa_en
        if manifiesta_en:
            linea["manifiesta_en"] = manifiesta_en
        self._escribir(linea)
        return uid

    def cerrar(self, uid: str, estado: str, motivo: Optional[str] = None,
               extra: Optional[dict] = None, momento: Optional[datetime] = None) -> None:
        if estado not in ESTADOS_FINALES:
            raise ValueError(f"estado final no valido: {estado}")
        linea = {"uid": uid, "evento": "fin", "fin": iso(momento or ahora()), "estado": estado}
        if motivo:
            linea["motivo"] = motivo
        if extra:
            linea["cierre"] = extra
        self._escribir(linea)

    # ------------------------------------------------------------ lectura
    def eventos(self) -> list[dict]:
        if not self.ruta.exists():
            return []
        salida = []
        with self.ruta.open(encoding="utf-8") as f:
            for linea in f:
                linea = linea.strip()
                if not linea:
                    continue
                try:
                    salida.append(json.loads(linea))
                except json.JSONDecodeError:
                    continue  # una linea cortada por un apagado no detiene la lectura
        return salida

    def abiertas(self) -> list[dict]:
        """Inicios sin fin: inyecciones que una interrupcion dejo a medias."""
        inicios, cerradas = {}, set()
        for e in self.eventos():
            if e.get("evento") == "inicio":
                inicios[e["uid"]] = e
            elif e.get("evento") == "fin":
                cerradas.add(e.get("uid"))
        return [e for uid, e in inicios.items() if uid not in cerradas]
