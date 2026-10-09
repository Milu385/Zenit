# Registros y trazas

Versión 1 · 2026-10-09 · H-016 · H-017 · RF-ING-02 · RF-ING-03 · RF-ALM-05 · RF-ALM-07 · ADR-007

## Camino

```
aplicacion (SDK de OpenTelemetry) ──registros y trazas──▶ agente del nodo
/var/log/syslog del nodo ─────────filelog──────────────▶   │ cola persistente, mTLS
                                                            ▼
                                       borde ──▶ ingesta ──▶ ClickHouse: zenit.registros, zenit.trazas
                                                     └─ verdad de referencia ──▶ archivo ──▶ cargador ──▶ PostgreSQL
```

La ingesta les aplica el mismo esquema canónico que a las métricas: las seis
etiquetas, `zenit_asset_id` resuelto contra el catálogo y las huérfanas
marcadas `sin_resolver`. Escribe a ClickHouse por lotes desde su propio hilo
(`DestinoPorLotes`, el mismo de las métricas), en JSON por línea.

## Las tablas

`deploy/clickhouse/01-esquema.sql`. El orden de las dos tablas es
`(zenit_asset_id, service_name, momento)`, porque toda consulta es "este
activo en esta ventana". Las etiquetas canónicas son columnas; los atributos
propios de cada registro o span van en un mapa `atributos`. Siete días de
retención, borrando particiones enteras.

El esquema lo aplica el servicio `clickhouse-esquema` en cada arranque, desde
el repositorio; nadie lo crea automáticamente (ADR-007).

## Control de volumen y política de descarte (H-016, criterio 3)

El mecanismo declarado es el mismo en las tres señales:

| Dónde | Qué pasa cuando no da abasto | Se pierde algo |
|---|---|---|
| Ingesta, cola en memoria (`CAPACIDAD_FILAS_ANALITICO`, 100 000 filas por señal) | Responde `UNAVAILABLE` y el agente reintenta desde su cola en disco | No |
| ClickHouse caído o lento | La ingesta reintenta sin límite; la cola se llena y aplica lo anterior | No |
| ClickHouse rechaza un lote por datos inválidos | Se reintenta petición por petición; lo que sigue fallando va a `/datos/salida/rechazadas/registros-*.jsonl` y se cuenta en `rechazadas_por_almacen` | Sí, contado y guardado |
| Agente, cola en disco | Reintenta 15 minutos (`max_elapsed_time: 900s`) | Sí, pasados 15 minutos sin plataforma; queda en los registros del agente |
| Verdad de referencia | Su exportador reintenta sin límite | No |

Nada se descarta en silencio: todo lo que la ingesta recibe termina emitido,
en cola o contado como rechazado (`/metricas`, por señal).

**Capacidad medida.** 500 registros por segundo sostenidos sin pérdida es el
criterio. Se mide con la sonda del laboratorio:

```bash
# en el nodo
docker compose --env-file entornos/<entorno>.env --profile observado --profile medicion \
  run --rm sonda registros --por-segundo 500 --duracion 600
# en la plataforma, con la ventana que imprimio la sonda
docker compose exec api python -m zenit.admin contar-registros --desde <desde> --hasta <hasta> --esperados 300000
```

## Trazas y la recolección nativa (H-017, criterio 2)

El criterio pide aprovechar la recolección nativa del proveedor donde exista.
En el laboratorio no hay ningún servicio gestionado por un proveedor: los
nodos corren la misma aplicación propia en contenedores, instrumentada con el
SDK de OpenTelemetry. No hay trazas nativas que puentear, así que **el
criterio no tiene contenido en esta topología** y se declara así (guía de la
épica 1, sección 6.2). Se reabre el día que el laboratorio incorpore un
servicio gestionado. El destino sigue siendo el almacén propio, que es lo que
pide el ADR-007.

## Consultas

| Endpoint | Qué devuelve |
|---|---|
| `GET /api/activos/{id}/registros?desde&hasta&severidad_minima&texto&servicio&limite` | Registros del activo, del más reciente al más viejo |
| `GET /api/activos/{id}/trazas?desde&hasta&solo_errores&limite` | Una fila por traza: raíz, duración, spans, errores y servicios |
| `GET /api/trazas/{traza_id}` | Todos los spans de una traza, en cualquier activo |
| `GET /api/activos/{id}/correlacion?desde&hasta` | Conteos de la ventana y los registros unidos con el span que los produjo, en una consulta (RF-ALM-07) |

La correlación es una unión en SQL por `traza_id` y `span_id` sobre el mismo
`zenit_asset_id`. Es la evidencia que usará el diagnóstico.
