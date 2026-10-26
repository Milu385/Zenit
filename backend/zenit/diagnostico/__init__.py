"""Diagnostico explicable (epica 4).

- `evidencia`: reune lo que paso en metricas, registros y trazas en la
  ventana de un incidente (H-039).
- `persistencia`: guarda el diagnostico junto con la evidencia que se uso
  (H-039, tercer criterio; lo llama H-040).

H-040 (la hipotesis), H-041 (la redaccion) y H-042 (version del modelo y
consumo) agregan sus modulos en este mismo paquete.

- `modelo_configurado()` (H-042): la version fija del modelo, de
  `ZENIT_MODELO_DIAGNOSTICO`; rechaza un alias que cambie solo.
- `consumo`: diagnosticos y tokens por dia y por modelo (H-042).
"""
from zenit.diagnostico.modelo import SinConfiguracion, modelo_configurado

__all__ = ["modelo_configurado", "SinConfiguracion"]
