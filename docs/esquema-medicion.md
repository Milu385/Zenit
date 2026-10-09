# Esquema de medición en InfluxDB 3

Versión 0.3.0 · 2026-10-09 · H-005, criterio 3 · H-018 · H-020 · H-021 · RF-ALM-01 a 04

Este documento describe el esquema **tal como quedó implementado**. El diseño original, con su justificación completa, está en el documento de esquema de medición del proyecto (versión 1, 11 de septiembre de 2026); al final se listan las diferencias entre ese diseño y lo implementado.

El código que aplica estas reglas está en `backend/zenit/esquema.py` (nombres), `backend/ingesta/extraccion.py` (de OTLP a puntos) y `backend/zenit/protocolo_linea.py` (escritura). La comprobación está en `python -m zenit.admin verificar`.

---

## 1. Qué es cada cosa

| Elemento | Qué es | Regla |
|---|---|---|
| **Tabla** (medición) | Una por métrica | Su nombre es el de la métrica de OpenTelemetry con los puntos cambiados por guiones bajos |
| **Etiqueta** | Columna de texto que dice de dónde viene la observación | Se usa para filtrar y agrupar. Solo texto |
| **Campo** | El valor medido | Un solo campo numérico, `value` |
| **Tiempo** | `time`, en nanosegundos | El del punto de datos, no el de llegada |

La regla para decidir: si responde "de dónde viene", es etiqueta; si responde "cuánto vale", es campo.

## 2. Bases y retención

Una base por nivel de resolución, porque la retención se configura por base:

| Base | Contenido | Retención | Quién escribe |
|---|---|---|---|
| `zenit_raw` | Observaciones a 10 s | 30 días mientras dure la campaña del laboratorio (7 en el diseño) | La ingesta |
| `zenit_1m` | Resúmenes por minuto | 30 días | El agregador (H-018) |
| `zenit_1h` | Resúmenes por hora | 180 días | El agregador (H-018) |
| `zenit_interno` | Métricas de la propia plataforma | 7 días | La ingesta y el agregador (H-021) |

Las crea `python -m zenit.admin crear-bases`, que también corre como servicio de un solo uso (`influxdb-bases`) en cada arranque. Si una base ya existe, le ajusta la retención: desde la versión 3.4, InfluxDB 3 Core permite cambiarla después de crear la base. Las retenciones se cambian con `RETENCION_RAW`, `RETENCION_1M`, `RETENCION_1H` y `RETENCION_INTERNA` en `deploy/.env`.

Core admite cinco bases. Con `zenit_interno` quedan cuatro en uso y **una libre**.

**Cómo se verifica la retención (H-020).** En Core la retención se aplica al consultar: lo que pasó la ventana deja de aparecer aunque el archivo se borre después. `python -m zenit.admin retencion` consulta, en cada base, una hora justo fuera de la ventana (no debe haber nada) y una justo dentro. `python -m zenit.admin espacio` dice cuántos archivos y cuántos MiB ocupa cada base, desde `system.parquet_files`; si esa tabla no responde, `scripts/influx/espacio.sh` mide los directorios.

## 3. Nombres

Se convierten en el punto de entrada, una sola vez, para que ninguna consulta necesite comillas:

| En OpenTelemetry | En InfluxDB |
|---|---|
| `system.cpu.utilization` | `system_cpu_utilization` |
| `service.name` | `service_name` |
| `zenit.asset.id` | `zenit_asset_id` |

Todo nombre queda en minúsculas, con solo letras, números y guiones bajos, empezando por letra y de 63 caracteres como máximo. Si coincide con una palabra reservada de SQL (`time`, `value`, `select`…), se le agrega `_x`.

## 4. Etiquetas canónicas

Las seis van en **toda** fila de **toda** tabla y nunca quedan vacías. Si el agente no envía un atributo, se escribe `desconocido`, porque el protocolo de línea no admite etiquetas vacías y una etiqueta omitida deja nulos en la tabla.

| Etiqueta | De dónde sale | Ejemplo |
|---|---|---|
| `zenit_asset_id` | Del catálogo en PostgreSQL, por la identidad del nodo | `activo-aws-nodo-01` |
| `host_name` | `host.name` del recurso | `zenit-nodo-aws` |
| `service_name` | `service.name`; si no viene, `SERVICIO_POR_DEFECTO` | `host` para las métricas del anfitrión |
| `cloud_provider` | `cloud.provider` | `aws`, `proxmox` |
| `cloud_region` | `cloud.region` | `us-east-1`, `local` |
| `deployment_environment` | `deployment.environment` | `lab` |

**Huérfanas.** Un punto cuyo nodo no está en el catálogo se guarda igual, con `zenit_asset_id = sin_resolver`, y su origen queda en `host_name`. Ninguna señal se descarta: el contador `huerfanas` de `/metricas` las cuenta y la tabla `huerfanas` de PostgreSQL las lista por origen (H-011). Cuando el activo entra al catálogo, sus filas se reescriben con el identificador correcto; las de `sin_resolver` quedan, porque Core no borra filas, y la vista de huérfanas las muestra como asociadas.

**De dónde sale `zenit_asset_id` (H-010).** Del catálogo en PostgreSQL (`activos` y `claves_nativas`, `deploy/postgres/02-catalogo.sql`), que mantiene el descubrimiento. La ingesta lo carga cada 30 s y resuelve en memoria. `deploy/catalogo/catalogo.json` queda como respaldo si PostgreSQL nunca respondió, y su contenido se importa al catálogo al arrancar.

## 5. Dimensiones propias de cada métrica

Son los atributos del punto de datos que trae el agente, convertidos con la regla de la sección 3. Las que aparecen hoy:

| Familia | Dimensiones |
|---|---|
| CPU | `cpu`, `state` |
| Memoria | `state` |
| Disco | `device`, `direction` |
| Sistema de archivos | `device`, `mountpoint`, `type`, `mode`, `state` |
| Red | `device`, `direction`; las conexiones, `protocol` y `state` |
| Paginación | `device`, `direction`, `state`, `type` |
| Procesos | `status` |

Una dimensión que no viene en un punto se omite y queda nula en esa fila. `verificar` lo informa como aviso, no como falla, porque no rompe ninguna consulta. Nulos en una etiqueta canónica sí son una falla.

## 6. Campos

| Base | Campos |
|---|---|
| `zenit_raw` | `value` (decimal) |
| `zenit_1m`, `zenit_1h` | `value_avg`, `value_min`, `value_max`, `value_count`, todos decimales |
| `zenit_interno` | `value` |

**Los resúmenes (H-018).** Cada fila de `zenit_1m` resume un minuto de una serie de `zenit_raw` (la misma tabla y las mismas etiquetas, con la marca de tiempo del inicio del minuto). `zenit_1h` resume las filas de `zenit_1m`: el promedio se pondera por `value_count`, así que es el promedio exacto de las observaciones crudas. A 10 s, un minuto completo tiene `value_count = 6` y una hora, 360. **Un intervalo con menos observaciones tuvo pérdida y queda con su cuenta real**: no se rellena ni se descarta, y la API lo informa en `intervalos_incompletos`.

El agregador reescribe cada resumen con la misma serie y el mismo instante, así que repetirlo lo reemplaza y no lo duplica. Escribe cada minuto una vez (y rehace los dos últimos) y cada hora una sola vez, porque Core no compacta y cada reescritura es un archivo más que las consultas largas tienen que abrir.

**Las tablas de `zenit_interno`.** `zenit_fuente_recibidas` y `zenit_fuente_errores` (etiquetas `zenit_asset_id`, `host_name`, `senal`, `cloud_provider`; una fila cada 10 s por fuente con lo recibido en ese lapso), `zenit_ingesta_*` (memoria, líneas pendientes, contrapresión y rechazos de la ingesta, etiqueta `servicio`) y `zenit_agregacion_*` (retraso y duración de cada ciclo, etiqueta `nivel`). No llevan las seis etiquetas canónicas porque no describen a un activo observado; `verificar` no revisa esta base.

Qué valor se guarda según el tipo de métrica de OpenTelemetry:

| Tipo | Qué se escribe |
|---|---|
| Gauge y suma | Una fila con `value` |
| Histograma, histograma exponencial y resumen | Dos tablas: `<métrica>_count` y `<métrica>_sum` (la suma solo si el agente la envía) |
| Punto marcado sin valor (`NO_RECORDED_VALUE`), valor NaN o infinito, o tipo no soportado | Nada; se cuenta en `sin_valor` |
| Marca de tiempo fuera del rango de 64 bits | Nada; se cuenta en `errores` |

Ningún punto desaparece sin quedar contado: en `/metricas` se cumple recibidas = emitidas + sin_valor + errores + rechazadas_por_almacen + en_cola.

## 7. Ejemplo

`system_cpu_utilization` en `zenit_raw`, 10 columnas:

| Columna | Tipo | Valor |
|---|---|---|
| `time` | tiempo | 2026-10-03T18:24:30Z |
| `zenit_asset_id` | etiqueta | `activo-aws-nodo-01` |
| `host_name` | etiqueta | `zenit-nodo-aws` |
| `service_name` | etiqueta | `host` |
| `cloud_provider` | etiqueta | `aws` |
| `cloud_region` | etiqueta | `us-east-1` |
| `deployment_environment` | etiqueta | `lab` |
| `cpu` | etiqueta | `cpu0` |
| `state` | etiqueta | `user` |
| `value` | campo | 0.343 |

## 8. Cómo se verifica

```bash
docker compose exec api python -m zenit.admin verificar
```

Comprueba, tabla por tabla:

- que las seis etiquetas canónicas existen y no tienen nulos (falla si no);
- que ninguna tabla pasa del 80 % del límite de columnas, 500 por defecto (falla si pasa);
- cuántas filas hay en las últimas 24 horas.

Desde H-018, `verificar` revisa también `zenit_1m` y `zenit_1h`: las mismas seis etiquetas canónicas, sin nulos, en los tres niveles.

Resultado del 3 de octubre de 2026: 26 tablas en `zenit_raw`, todas en OK, entre 8 y 13 columnas cada una. Ver `docs/verificacion-epica-0.md`.

## 9. Diferencias con el diseño original

| Diseño (v1) | Implementado | Motivo |
|---|---|---|
| Retención de `zenit_1h`: 6 meses | 180 días | InfluxDB expresa la retención en días |
| Retención de `zenit_raw`: 7 días | 30 días durante la campaña del laboratorio | La evaluación necesita las semanas completas aunque falle una exportación diaria |
| La retención no se puede cambiar después de crear la base | Se puede cambiar (InfluxDB 3.4 o posterior) | Comprobado en el código fuente del motor |
| Una dimensión opcional se escribe como `desconocido` | Solo las canónicas usan `desconocido`; las dimensiones propias pueden quedar nulas | Rellenarlas obligaría a conocer de antemano las dimensiones de cada métrica |
| Métricas internas de Zenit en una base propia | `zenit_interno` desde H-021; `/metricas` de la ingesta sigue para la verificación rápida | Como en el diseño |
| Una consulta larga sobre los resúmenes | Partida en tramos de 1 día (`zenit_1m`) y 10 días (`zenit_1h`), en paralelo | Core falla las consultas que abren más de 432 archivos (`--query-file-limit`) |
