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

Reentrenamiento y version (H-036, RF-DET-05)
--------------------------------------------

**Linea base (este paquete).** No hay modelo persistido que reentrenar: la
mediana y la escala se recalculan en cada ciclo, para cada serie, sobre la
ventana movil de los ultimos 60 minutos. El "reentrenamiento" es continuo y
es parte del metodo. La unica linea base que se guarda es la del momento de
abrir cada anomalia (`evidencia.linea_base`), y no es un modelo: es la
referencia contra la que se decide el cierre (`cierre.py`).

Lo que se versiona es el metodo y sus parametros, en `version_modelo`, que
va en cada fila de `anomalias`: `mad-v2 W=60 z=3.5 m=3` (ventana, corte
critico, persistencia; el piso relativo se agrega al final si se activa).
Cambiar un parametro cambia la cadena, y cambiar el metodo cambia el
prefijo: de `mad-v1` a `mad-v2` cambiaron la historia exigida y el piso de
la escala (`detector.py`). El catalogo de senales (`senales.py`) es parte
del metodo; un cambio que altere que series se vigilan o con que piso se
anota en el encabezado de `detector.py` y sube el prefijo. Con eso, cada
anomalia dice con que version se abrio, y la evaluacion (H-029) separa las
de cada version sin adivinar por fechas.

**KMeans (H-034).** Corre por lotes, fuera de la plataforma, sobre la
exportacion de metricas de Azure (`python -m zenit.admin exportar`). Se
entrena una vez con los datos anteriores a la ventana de evaluacion y no se
reentrena durante ella, para que la comparacion con la linea base sea
justa: los dos se miden sobre el mismo periodo con un modelo fijo.
Reentrenar es volver a correr el lote sobre una exportacion nueva, y la
version dice con que se entreno: por ejemplo `kmeans-v1 k=4
entrenado=2026-10-16/2026-10-22`. El modelo ajustado no se guarda en el
repositorio (igual que los `.pkl` de los cuadernos): se regenera con el
lote y la exportacion.

**En cada evaluacion.** El reporte de H-029 declara `version_modelo`, la
ventana de tiempo evaluada, el conjunto de inyecciones (solo las
`completada`) y la fecha de corte.
"""
