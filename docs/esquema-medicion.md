# Esquema de medición en InfluxDB 3

Versión 0.2.0 · 2026-10-03 · H-005, criterio 3 · RF-ALM-01

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

| Base | Contenido | Retención | Estado |
|---|---|---|---|
| `zenit_raw` | Observaciones a 10 s | 7 días | En uso |
| `zenit_1m` | Resúmenes por minuto | 30 días | Creada, vacía hasta H-018 |
| `zenit_1h` | Resúmenes por hora | 180 días | Creada, vacía hasta H-018 |

Las crea `python -m zenit.admin crear-bases`, que también corre como servicio de un solo uso (`influxdb-bases`) en cada arranque. Si una base ya existe, le ajusta la retención: desde la versión 3.4, InfluxDB 3 Core permite cambiarla después de crear la base. Las retenciones se cambian con `RETENCION_RAW`, `RETENCION_1M` y `RETENCION_1H` en `deploy/.env`.

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
| `zenit_asset_id` | Del catálogo (`deploy/catalogo/catalogo.json`), por la identidad del nodo | `activo-aws-nodo-01` |
| `host_name` | `host.name` del recurso | `zenit-nodo-aws` |
| `service_name` | `service.name`; si no viene, `SERVICIO_POR_DEFECTO` | `host` para las métricas del anfitrión |
| `cloud_provider` | `cloud.provider` | `aws`, `proxmox` |
| `cloud_region` | `cloud.region` | `us-east-1`, `local` |
| `deployment_environment` | `deployment.environment` | `lab` |

**Huérfanas.** Un punto cuyo nodo no está en el catálogo se guarda igual, con `zenit_asset_id = sin_resolver`, y su origen queda en `host_name`. Ninguna señal se descarta: el contador `huerfanas` de `/metricas` las cuenta.

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
| `zenit_1m`, `zenit_1h` | `value_avg`, `value_min`, `value_max`, `value_count`, cuando exista H-018 |

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

Que los tres niveles compartan etiquetas se comprueba sobre `zenit_raw` y se repite al cerrar H-018, cuando `zenit_1m` y `zenit_1h` tengan tablas.

Resultado del 3 de octubre de 2026: 26 tablas en `zenit_raw`, todas en OK, entre 8 y 13 columnas cada una. Ver `docs/verificacion-epica-0.md`.

## 9. Diferencias con el diseño original

| Diseño (v1) | Implementado | Motivo |
|---|---|---|
| Retención de `zenit_1h`: 6 meses | 180 días | InfluxDB expresa la retención en días |
| La retención no se puede cambiar después de crear la base | Se puede cambiar (InfluxDB 3.4 o posterior) | Comprobado en el código fuente del motor |
| Una dimensión opcional se escribe como `desconocido` | Solo las canónicas usan `desconocido`; las dimensiones propias pueden quedar nulas | Rellenarlas obligaría a conocer de antemano las dimensiones de cada métrica |
| Métricas internas de Zenit en una base propia | Por ahora en `/metricas` de la ingesta, en JSON | Suficiente para la épica 0; se revisa con H-021 |
