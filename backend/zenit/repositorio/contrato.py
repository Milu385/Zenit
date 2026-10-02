"""Contrato de la capa de repositorio (H-006, RNF-MAN-01).

Toda consulta a la serie temporal pasa por aqui. La API, los tableros y los
trabajadores dependen de este contrato, nunca del cliente del motor. Si el
benchmark del M2 (H-022) cambia de motor, cambia la implementacion y nada mas.
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Mapping, Optional, Protocol


class Resolucion(str, Enum):
    CRUDO = "10s"
    MINUTO = "1m"
    HORA = "1h"


@dataclass(frozen=True)
class Serie:
    """Una linea de la grafica: los puntos de una combinacion de dimensiones."""

    etiquetas: Mapping[str, str]
    # (milisegundos desde la epoca, valor promedio en el intervalo)
    puntos: list


@dataclass(frozen=True)
class Respuesta:
    activo: str
    metrica: str
    resolucion: Resolucion
    desde: datetime
    hasta: datetime
    agrupar: Optional[str]
    series: list = field(default_factory=list)
    # Intervalos de la resolucion dentro del rango que no tienen ningun dato.
    # Sirve para ver huecos: un corte de 5 minutos a 10 s son 30 intervalos.
    intervalos_esperados: int = 0
    intervalos_vacios: int = 0
    # Si la agrupacion produce demasiadas lineas, se devuelven las primeras y
    # aqui se dice cuantas quedaron fuera.
    series_omitidas: int = 0


class ErrorConsulta(ValueError):
    """La consulta es invalida (metrica o dimension inexistente, rango
    demasiado largo). Es un error de quien pregunta, no del motor."""


class RepositorioSeries(Protocol):
    def metricas_de(self, activo: str) -> list[str]:
        """Metricas con datos recientes de este activo."""

    def dimensiones(self, activo: str, metrica: str, desde: datetime, hasta: datetime) -> dict:
        """Dimensiones propias de la metrica y sus valores en el rango."""

    def serie(
        self,
        activo: str,
        metrica: str,
        desde: datetime,
        hasta: datetime,
        *,
        agrupar: Optional[str] = None,
        filtros: Optional[Mapping[str, str]] = None,
    ) -> Respuesta:
        """La serie de una metrica para un activo y un rango."""

    def disponible(self) -> bool:
        """True si el motor responde."""
