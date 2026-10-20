"""Deteccion de anomalias (H-032, epica 3).

El metodo viene del cuaderno `Modelos/Modelo criticidad/clasificacion_estado_zenit.ipynb`:
linea base por activo (mediana + MAD) y puntaje z, con los cortes de
Iglewicz & Hoaglin (1.5 / 3.5). Lo que agrega este paquete sobre el cuaderno:

- la deteccion es por (activo, metrica) para encajar con la tabla `anomalias`
  (una fila por serie que se sale de rango), no un estado combinado por fila;
- la regla de persistencia (H-032, criterio 3): una anomalia se abre con
  varios puntos seguidos fuera de rango, nunca con un pico aislado.
"""
