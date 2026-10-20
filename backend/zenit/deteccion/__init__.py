"""Deteccion de anomalias (H-032, epica 3).

Cuatro archivos, en orden de lectura:

- `senales.py`: que series se vigilan, con que dimensiones y si hay que
  convertirlas a tasa. Es el puente entre las columnas ya preparadas del
  CSV del cuaderno y las 26 tablas crudas de la plataforma.
- `detector.py`: el metodo (linea base por mediana y MAD, puntaje z, cortes
  1.5 / 3.5, regla de persistencia), la severidad y el trabajo de un ciclo.
- `cierre.py` (H-033): cuando una anomalia abierta pasa a `cerrada`. Lee la
  explicacion de su encabezado antes de cambiar nada ahi: el cierre se mide
  contra la linea base congelada al abrir, y el motivo esta medido.
- `__main__.py`: el punto de entrada del servicio.

El metodo viene del cuaderno `Modelos/Modelo criticidad/clasificacion_estado_zenit.ipynb`.
Lo que agrega este paquete sobre el cuaderno:

- la deteccion es por (activo, metrica, dimensiones) para encajar con la
  tabla `anomalias` (una fila por serie que se sale de rango), no un estado
  combinado por fila;
- la regla de persistencia (H-032, criterio 3): una anomalia se abre con
  varios puntos seguidos fuera de rango, nunca con un pico aislado;
- las dimensiones y los contadores acumulados, que en el CSV venian
  resueltos y en la plataforma no (ver `senales.py`).
"""
