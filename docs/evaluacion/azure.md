# Evaluación en Azure: H-029, H-034 y H-035 (cifras provisionales)

**Estado.** Cifras con la exportación del martes, `zenit-export-2026-10-27` (corte
provisional: 27 de octubre, 23:00 UTC). Las finales salen de la exportación del
viernes 30 con el mismo comando, y este documento se actualiza con ellas:

```bash
bash scripts/deteccion/evaluar_azure.sh <exportación descomprimida> <despliegue de mad-v2> <corte>
```

**Hora del despliegue de mad-v2: pendiente de confirmar con Juan José** (la anotó
con `date -u` el 26). Aquí se usa **2026-10-26 14:01 UTC**, deducida de la tabla
`anomalias`: la última anomalía de mad-v1 se detectó a las 13:19:31, el cierre
manual de las 14 que no cerraban se hizo a las 14:01:04 y el primer ciclo de
mad-v2 abrió la 518 a las 14:01:36. Si la hora real es otra, solo cambia el
segundo argumento.

La exportación tiene las métricas de **un solo activo**, el nodo de laboratorio
(`activo-azure-zenit-nodo-azure`); la tabla `anomalias` trae los dos. Por eso
todo lo que necesita las series (el piso, KMeans, la proyección) es solo del
nodo de laboratorio, y Kaderno solo aparece a través de las anomalías que abrió.

---

## H-029 · Evaluación por evento, en dos tramos

Emparejamiento de siempre: mismo activo, la anomalía empieza entre el inicio de
la inyección y su fin más 5 minutos. Solo inyecciones `completada`.

| Tramo | Ventana (UTC) | Inyecciones | Detectadas | Exhaustividad | Correctas | Falsos positivos | Precisión | F1 | Retardo medio |
|---|---|---|---|---|---|---|---|---|---|
| mad-v1 | 21 oct 13:13 a 26 oct 14:01 | 72 | 33 | 45,8 % | 113 | 356 | 24,1 % | 31,6 % | 3,0 min |
| mad-v1, solo nodo de laboratorio | igual | 72 | 33 | 45,8 % | 113 | 33 | 77,4 % | 57,6 % | 3,0 min |
| mad-v2 | 26 oct 14:01 a 27 oct 23:00 | 20 | 18 | 90,0 % | 29 | 64 | 31,2 % | 46,3 % | 1,6 min |
| mad-v2, solo nodo de laboratorio | igual | 20 | 18 | 90,0 % | 29 | 5 | 85,3 % | 87,6 % | 1,6 min |

Por tipo, mad-v2 detecta las 5 de memoria, las 4 de disco gradual y las 6 de disco
abrupto, y 3 de 5 caídas. En su ventana no hubo inyecciones de CPU. Los informes
completos (por tipo, por nodo, por métrica, no detectadas) están en
`h029-mad-v1/informe.md` y `h029-mad-v2/informe.md` de la salida del script.

**Por qué se dan dos precisiones.** Kaderno es un servidor real sin inyecciones:
todo lo que se abre ahí cuenta como falso positivo. Es correcto contarlo (es
ruido real), pero mezclado con el laboratorio la precisión dice más de Kaderno
que del método: 323 de los 356 falsos positivos de mad-v1 y 59 de los 64 de
mad-v2 son suyos (43 por día con mad-v2). La fila "solo nodo de laboratorio"
mide el método donde hay verdad de referencia; la otra, lo que vería un operador.

### Lo que se declara en el informe

- **Inicio de la campaña:** 16 de octubre de 2026. Hay un hueco de telemetría del
  nodo del 16 a las 02:55 al 17 a las 15:51 UTC (`huecos.csv`); no toca ninguna
  ventana de H-029.
- **Detector mad-v1:** desde el 21 de octubre a las 13:13 UTC.
- **Reposición del catálogo:** desde el 21 de octubre a las 15:17 UTC.
- **mad-v2:** desde el 26 de octubre a las 14:01 UTC (por confirmar, ver arriba).
- **Fecha de corte:** la de la exportación final (aquí, provisional, 27 de
  octubre a las 23:00 UTC).
- **Borde del despliegue.** El primer ciclo de mad-v2 abrió tres anomalías cuyo
  inicio es de antes del despliegue, porque mira 30 minutos hacia atrás
  (`MAX_ANTIGUEDAD`): la 518 y la 519 (memoria y swap, sobre la inyección de
  memoria de las 13:34) y la 520 de Kaderno. Con la ventana empezando en el
  despliegue quedan fuera de los dos tramos. Si se empezara a las 13:20, mad-v2
  daría 19 de 21 y 86,1 % de precisión en el laboratorio: no cambia la lectura.
- **Series bloqueadas en mad-v1.** Ver la sección siguiente. Es la causa de casi
  toda la diferencia de exhaustividad entre mad-v1 y mad-v2.
- **Cierres manuales del 26.** Las 14 anomalías de
  `abiertas-mad-v1-2026-10-26.csv` tienen `cierre_manual = t` y `fin` =
  2026-10-26 14:01 UTC. Ese `fin` es la hora del cierre manual, no el final de la
  desviación: **su duración no se usa** en ninguna cifra. La evaluación por
  evento no usa la duración, así que los dos informes no cambian.
- **Lo que el método no ve.**
  - *Latencia:* la única inyección de latencia completada (21 de octubre, 15:17)
    queda fuera de los totales, como limitación declarada (`senales.py`).
  - *Caídas:* la línea base solo mira desviaciones hacia arriba. mad-v2 detecta 3
    de 5 caídas, y solo porque otras señales suben al caer el servicio. Las 2 que
    pierde (27 de octubre, 02:05 y 12:42) sí las ve KMeans (H-034), como bajada
    de las llamadas.
- **Falsos positivos de Kaderno.** La **520** (`system_network_io`, `eth0`
  transmit, 26 de octubre 13:55) la marcó Juan José como falso positivo: ráfagas
  de red normales de un servidor real que el método lee como desviación. Es el
  ejemplo de ruido en producción. Queda justo antes del despliegue (ver "Borde")
  y no entra en las cuentas de mad-v2; dentro de la ventana, el operador marcó
  la 576 (disco de Kaderno), y la marca fue correcta. Con mad-v2 el ruido de
  Kaderno se reparte entre paginación (16), escrituras de disco (15), swap (10),
  carga (7), red (7) y sistema de archivos (4).

### Series bloqueadas por anomalías de mad-v1 que no cerraban

Mientras una anomalía sigue abierta, el detector no abre otra en esa serie. Con
mad-v1 varias no cerraban nunca (escala congelada demasiado estrecha; arreglado en
el PR #26) y quedaron abiertas hasta el cierre manual del 26.
`scripts/deteccion/series_bloqueadas.py` cuenta, por cada una, cuántas inyecciones
cayeron en su serie mientras estuvo bloqueada, y cuántas de ellas habría visto esa
misma serie con el cierre arreglado (detección de mad-v1 corrida fuera de línea
sobre la exportación, con el bloqueo normal mientras cada anomalía nueva sigue
abierta).

| Id | Serie (nodo de laboratorio) | Debe ver | Bloqueada desde | Días | Inyecciones de su tipo en el bloqueo | Perdidas en H-029 | La serie habría visto |
|---|---|---|---|---|---|---|---|
| 3 | `system_cpu_utilization` state=wait | disco | 21 oct 08:21 | 5,2 | 32 | 28 | 0 |
| 42 | `system_filesystem_usage` / | disco | 21 oct 15:03 | 5,0 | 30 | 28 | 30 (28 de las perdidas) |
| 110 | `system_memory_utilization` used | memoria | 22 oct 06:37 | 4,3 | 9 | 6 | 9 (6 de las perdidas) |
| 304 | `traces_span_metrics_calls` UNSET | caída, latencia | 23 oct 11:45 | 3,1 | 9 | 1 | 0 |
| 305 | `system_paging_usage` /swapfile | memoria | 24 oct 12:05 | 2,1 | 5 | 5 | 2 (2 de las perdidas) |

Las otras nueve del respaldo no cuentan: siete son de Kaderno (sin inyecciones) y
dos son de red del laboratorio, que no está asociada a ningún tipo de inyección.

**Lo que dice:**

- **De las 39 inyecciones que mad-v1 no detectó, 35 cayeron en una serie bloqueada
  del tipo que esa serie debe ver**, y **34 las habría detectado la propia serie**
  con el cierre arreglado. Con ellas la exhaustividad de mad-v1 sería 67 de 72
  (93,1 %) en vez de 33 de 72 (45,8 %).
- Lo peor fue el disco: la 42 (sistema de archivos, abierta el 21 a las 15:03
  sobre una inyección de disco **fallida**) dejó la serie de disco ciega cinco días.
  Por eso mad-v1 tiene 14,3 % en disco gradual y 5,9 % en disco abrupto.
- La CPU en espera (la 3) tampoco habría ayudado: con el bloqueo quitado no
  detecta ninguna inyección de disco de ese tramo. Ahí el bloqueo no costó nada.
- Así que la diferencia entre mad-v1 y mad-v2 no viene de la detección (son el
  mismo método salvo la historia completa y el piso de las fracciones), sino del
  **cierre**. Hay que decirlo así y no venderlo como una mejora del método.
- Lo que el script **no** puede decir: Kaderno no está en la exportación, pero
  allí no hay inyecciones, así que su bloqueo no cuesta exhaustividad.

---

## H-034 · KMeans en Azure

`scripts/deteccion/no_supervisada.py`, entrenado con todo lo anterior a mad-v2
(16 de octubre a 26 de octubre, 14:01; incluye el hueco de telemetría) y detectando
en la ventana de mad-v2. Evaluado con el mismo programa, la misma ventana y las
mismas 20 inyecciones (huella `0020f62a485b`). Como KMeans solo corre sobre el
nodo de laboratorio, la comparación justa es con la fila de laboratorio de mad-v2.

| Método | Detectadas | Falsos positivos | Precisión | F1 | Retardo medio | Disco gradual | Caídas |
|---|---|---|---|---|---|---|---|
| Línea base (mad-v2), nodo de laboratorio | 18 de 20 | 5 | 85,3 % | 87,6 % | 1,6 min | 1,5 min | 3 de 5 |
| KMeans (`kmeans-v1 k=6 entrenado=2026-10-16/2026-10-26`) | 20 de 20 | 0 | 100 % | 100 % | 2,5 min | 4,5 min | 5 de 5 |

El modelo aprende 6 grupos y uno solo es normal. Los otros cinco corresponden a
los tipos de fallo de la campaña: disco, memoria, CPU, caída (todas las tasas
abajo) y caída del catálogo.

Cómo leerlo, sin venderlo de más:

- **Mejora a la línea base en esta ventana**, en las dos cosas que importan: ve
  las dos caídas que la línea base no puede ver (mira hacia abajo) y no abre
  ninguno de sus 5 falsos positivos (3 de carga, 1 de disco y 1 de llamadas), porque no usa
  esas variables.
- **Es optimista.** Se entrena y se detecta en el mismo nodo, con la misma
  campaña y los mismos tipos de fallo: los grupos de fallo que aprende antes del 26
  son exactamente los que vuelven a aparecer después. Un fallo de un tipo nuevo
  solo lo atraparía la regla de distancia (percentil 99,5).
- **Es poca evidencia.** 20 inyecciones en 33 horas y ninguna de CPU. No alcanza
  para afirmar que un método es mejor en general, solo que en esta ventana lo fue.
- **Tarda más en el disco gradual** (4,5 frente a 1,5 min), como ya se había
  visto en `data.csv`: un llenado lento tarda en salir del grupo normal.
- **No se midió en Kaderno**, que es donde la línea base hace casi todo su ruido.
  Es la medición que falta para comparar los métodos en producción.

---

## H-035 · Proyección de saturación con los discos graduales de Azure

Sección 4b del cuaderno `Modelos/Modelos SARIMA/modelos_sarima.ipynb`: el mismo
método y el mismo umbral (0,70) de la sección 4, sobre las 30 inyecciones de disco
gradual de la campaña.

- 4 cayeron en el hueco de telemetría del 16 y 17 y no tienen datos.
- **Las 26 con datos cruzaron el umbral y las 26 se avisaron antes**, con 7,9
  minutos de anticipación media (de 3 a 13) y un error del instante estimado de 0
  o 1 minuto. Ningún aviso sin cruce.
- El llenado del inyector es lineal (R² ≥ 0,996), así que la banda al 95 % sale
  de 0 a 3 minutos. Eso no caracteriza el intervalo con un crecimiento real.
- La memoria sigue sin poder validarse: su inyección es un salto, no una rampa.

**Desviación de resolución (escrita en el cuaderno).** RF-DET-04 sitúa la
proyección sobre el resumen horario, y H-035 se valida a 1 minuto. Razón medida en
Azure: las inyecciones graduales duran 20 minutos; por minuto el disco llega a
0,85, por hora a 0,39, y ninguna hora cruza el umbral. A resolución horaria no hay
saturación que proyectar. El código es el mismo; cambia la entrada. La resolución
horaria sigue siendo la adecuada para el crecimiento lento de un recurso real.

---

## Piso del swap y del disco (mejora propuesta mad-v3)

Medido sin desplegar con `scripts/deteccion/medir_piso.py`. La tabla y la
recomendación están en `backend/zenit/deteccion/senales.py`, sección "Piso de la
escala". En corto: en Azure el swap no hace ruido (15 de sus 16 anomalías caen en
inyecciones de memoria que `system_memory_utilization` ya detecta), su ruido está
en Kaderno, y un piso de 8 MiB lo quitaría sin perder ninguna detección. En el
disco, cualquier piso deja sin severidad `alta` a todos los llenados a cambio de
quitar 3 eventos de ruido en once días. Se recomienda 8 MiB para el swap, pendiente
de medir con una exportación de Kaderno, y ningún piso para el disco.
