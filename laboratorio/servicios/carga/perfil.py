"""Perfil de carga con variacion por hora y por dia (H-023).

tasa(t) = base x factor de la hora x factor del dia x ruido

- factor de la hora: 24 valores, uno por hora local, interpolados en linea
  recta para que no haya escalones a las horas en punto;
- factor del dia: los sabados y domingos se multiplica por `fin_de_semana`;
- ruido: un factor por minuto, sacado de una semilla. El mismo minuto da
  siempre el mismo ruido, asi que el perfil es reproducible.

Los perfiles viven en laboratorio/perfiles/*.json y estan versionados.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


@dataclass(frozen=True)
class Perfil:
    base_rps: float
    horas: tuple
    utc_offset_h: float = 0.0
    fin_de_semana: float = 1.0
    ruido: float = 0.0
    semilla: int = 1

    def __post_init__(self):
        if len(self.horas) != 24:
            raise ValueError("el perfil necesita 24 factores, uno por hora")
        if min(self.horas) <= 0:
            raise ValueError("los factores tienen que ser positivos")

    @classmethod
    def cargar(cls, ruta: Path) -> "Perfil":
        d = json.loads(Path(ruta).read_text())
        return cls(base_rps=float(d["base_rps"]), horas=tuple(float(x) for x in d["horas"]),
                   utc_offset_h=float(d.get("utc_offset_h", 0)), fin_de_semana=float(d.get("fin_de_semana", 1)),
                   ruido=float(d.get("ruido", 0)), semilla=int(d.get("semilla", 1)))

    def amplitud(self) -> float:
        """Pico entre valle de un dia laboral, sin ruido. H-023 pide al menos 3."""
        return max(self.horas) / min(self.horas)

    def factor_hora(self, momento: datetime) -> float:
        local = momento.astimezone(timezone.utc) + timedelta(hours=self.utc_offset_h)
        h = local.hour + local.minute / 60 + local.second / 3600
        i = int(h) % 24
        frac = h - int(h)
        return self.horas[i] * (1 - frac) + self.horas[(i + 1) % 24] * frac

    def factor_dia(self, momento: datetime) -> float:
        local = momento.astimezone(timezone.utc) + timedelta(hours=self.utc_offset_h)
        return self.fin_de_semana if local.weekday() >= 5 else 1.0

    def factor_ruido(self, momento: datetime) -> float:
        if self.ruido <= 0:
            return 1.0
        minuto = int(momento.timestamp() // 60)
        return random.Random(self.semilla * 10_000_019 + minuto).uniform(1 - self.ruido, 1 + self.ruido)

    def tasa(self, momento: datetime) -> float:
        return self.base_rps * self.factor_hora(momento) * self.factor_dia(momento) * self.factor_ruido(momento)
