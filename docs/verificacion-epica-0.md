# Verificación de la épica 0

Plataforma en AWS (t3.small, 30 GB, IP elástica) y nodo observado `zenit-nodo-aws` (activo `activo-aws-nodo-01`), rama `epica-0-ruta-critica`, imágenes 0.2.0.

Fecha de la verificación: 3 de octubre de 2026. Las horas están en UTC.

## Resultados

| Paso | Caso | Criterio | Resultado | Evidencia | Fecha | Verificó |
|---|---|---|---|---|---|---|
| 8 | | H-001 c1, H-005 c1 | Pasa | `docker compose ps -a`: los seis servicios arriba y `influxdb-bases` terminó con código 0 | 2026-10-03 | Juan José |
| 10 | P-ING-04 | H-004 c4 | Pasa | `/metricas`: 133 732 recibidas = 133 732 emitidas; 0 sin valor, 0 errores, 0 rechazadas por el almacén, 0 en cola; destino `influx` sin error | 2026-10-03 | Juan José |
| 11 | P-ING-02 | H-004 c3, H-005 c3 y c4 | Pasa | `crear-bases`: las tres bases ya existían y se ajustó su retención (7 d, 30 d y 180 d). `verificar`: 26 tablas en `zenit_raw` en OK, `zenit_1m` y `zenit_1h` vacías, "todo en orden" | 2026-10-03 | Juan José |
| 12 | P-ING-01 | H-003 c1 | Pasa | 26 métricas, entre ellas cpu, memoria, disco y red. Las últimas once diferencias entre puntos son todas de 10 s | 2026-10-03 | Juan José |
| 13 | P-ING-03 | H-004 c4 | Pasa | Huérfanas: 0, luego 912 con la llave cambiada, y 1140 estables en cuatro consultas tras restaurarla. El catálogo se releyó sin reiniciar | 2026-10-03 | Juan José |
| 14 | P-SEG-01 | H-001 c3 | Pasa | Desde fuera, 8181, 8000, 8080 y 5432 cerrados; 443 responde 200 desde la IP del equipo; 4317 cerrado desde el PC. Desde el nodo, TLS con `Verify return code: 0 (ok)` | 2026-10-03 | Juan José |
| 15 | P-ALM-01 | H-001 c2, H-005 c5 | Pasa | Filas de `system_cpu_utilization`: 10 112 → 10 176 al reiniciar InfluxDB, y 10 192 → 10 304 al reiniciar la instancia. Los servicios arrancaron solos | 2026-10-03 | Juan José |
| 16 | P-ING-06 | H-003 c4 | Pasa | Borde detenido 5 min desde las 18:14:25. `huecos` entre 18:09:08 y 18:21:08: 71 muestras, 0 huecos de más de 20 s | 2026-10-03 | Juan José |
| 17 | P-ALM-02 | H-006 c2, c3 y c4 | Pasa (falta la revisión cruzada) | Captura de `/api/docs` con los cinco endpoints. El `grep` de `SELECT`, sin `.venv`, solo encuentra `zenit/repositorio/influx.py` y `zenit/admin.py` | 2026-10-03 | Juan José; revisión pendiente de Daniel |
| 18 | | H-007 c1 a c4 | Pasa | Carga iniciada a las 18:38:41 y vista en la API a las 18:39:00: **19 s** de latencia de extremo a extremo (límite de 30 s, RNF-REN-01). Capturas del escalón y de los estados de error (502 con la API detenida), sin datos y cargando | 2026-10-03 | Juan José |
| 13b | P-ING-03 | H-004 c4 | Pasa | La serie de `sin_resolver` entre 17:44 y 17:48 tiene 5 puntos guardados: los huérfanos se almacenan, no se descartan | 2026-10-03 | Juan José |
| — | P-SEG-02 | H-008 c3 | Pasa | `git log --all -p` sin tokens `apiv3_` ni llaves privadas en el historial | 2026-10-03 | Juan José |
| 19 | P-REP-01 | H-008 c2 | Semana 15 | | | María Lucía |

## Observaciones

**Cómo se midió la latencia del paso 18.** Las horas del eje de la gráfica son la hora de la muestra en el nodo, no la de llegada. Por eso se midió con el reloj de pared: un script en la plataforma consultó la API cada 2 s y registró cuándo apareció por primera vez el valor de `state=user` por encima del 50 %. Las dos máquinas sincronizan su reloj con AWS. Los 19 s incluyen el muestreo de 10 s del agente, el envío, la escritura en InfluxDB y hasta 2 s del sondeo.

**Huecos de la primera corrida del paso 16.** En esa corrida se pararon y arrancaron el borde en el mismo segundo, y `huecos` encontró dos:

- **60 s entre 17:45:40 y 17:46:40.** Es la consecuencia esperada del paso 13: durante ese minuto los puntos llegaron como huérfanos y no se guardaron bajo el activo.
- **30 s entre 17:52:50 y 17:53:20.** Ocurrió justo después del reinicio de la instancia. No se repitió en la prueba válida, con un corte de 5 minutos. Queda como observado y no reproducido, y se vigila en las corridas largas del laboratorio.

**El reinicio de la instancia no dejó hueco** (paso 15). La cola persistente del agente entregó lo acumulado durante ese corte de cerca de un minuto.

**Problemas del despliegue y su corrección:**

| Problema | Causa | Corrección |
|---|---|---|
| El borde no arrancaba: `servidor.crt` y `servidor.key` eran directorios | Docker Compose crea un directorio cuando falta el archivo de origen, también con la sintaxis larga | `bind: create_host_path: false` en las montadas del borde, para que un archivo faltante detenga el arranque con un error claro |
| Al construir las imágenes, "no space left on device" | Disco raíz de 8 GB: `02-plataforma.sh` amplía `/dev/xvda` en lugar de `/dev/sda1` | Volumen ampliado a 30 GB. `02-plataforma.sh` y `03-nodo.sh` ya leen el dispositivo raíz de la AMI |
| El agente daba `i/o timeout` hacia el 4317 | La IP pública del nodo cambió con la nueva sesión del laboratorio y el grupo de seguridad solo admitía la anterior | Se admitió la IP nueva con `admitir-nodo.sh`. `03-nodo.sh` ahora le asigna IP elástica al nodo; el README explica cómo hacerlo con el nodo existente |

## Pendiente para dar la épica por cerrada formalmente

- Segundo entorno observado, el nodo on-premise en el Proxmox reportando junto al de AWS (H-002 c6).
- Revisión del paso 17 por Daniel o María Lucía, que no escribieron el código, y verificación de los demás pasos por Daniel según el plan de pruebas.
- Paso 19, en la semana 15, con María Lucía: reproducción desde cero usando solo el README. El README ya explica lo de la tabla anterior (sección "Problemas conocidos").
