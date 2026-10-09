-- Registros y trazas (H-016, H-017, ADR-007). Un solo motor analitico, dos
-- tablas, la misma clave de activo. El esquema se administra desde aqui y no
-- lo crea nadie automaticamente: lo aplica el servicio clickhouse-esquema en
-- cada arranque, y todo es IF NOT EXISTS.
--
-- Orden de las tablas: (zenit_asset_id, service_name, momento). Las consultas
-- son siempre "este activo en esta ventana", y ese orden las resuelve leyendo
-- solo los bloques del activo.
--
-- Retencion: 7 dias, como el nivel crudo de las metricas en el diseno. Se
-- borran particiones enteras (ttl_only_drop_parts), que es barato.

CREATE DATABASE IF NOT EXISTS zenit;

CREATE TABLE IF NOT EXISTS zenit.registros
(
    momento                DateTime64(9, 'UTC'),       -- del registro; si no trae, el de observacion
    zenit_asset_id         LowCardinality(String),
    host_name              LowCardinality(String),
    service_name           LowCardinality(String),
    cloud_provider         LowCardinality(String),
    cloud_region           LowCardinality(String),
    deployment_environment LowCardinality(String),
    severidad              LowCardinality(String),     -- severity_text; vacio si el emisor no la manda
    severidad_num          UInt8,                      -- severity_number de OTLP (1 a 24)
    cuerpo                 String,
    traza_id               String,                     -- hexadecimal; vacio si el registro no viene de una traza
    span_id                String,
    atributos              Map(LowCardinality(String), String),
    reasignado             UInt8 DEFAULT 0,            -- 1 si salio de una huerfana asociada despues (H-011)
    INDEX por_traza traza_id TYPE bloom_filter(0.01) GRANULARITY 4,
    INDEX por_texto cuerpo TYPE tokenbf_v1(32768, 3, 0) GRANULARITY 4
)
ENGINE = MergeTree
PARTITION BY toDate(momento)
ORDER BY (zenit_asset_id, service_name, momento)
TTL toDateTime(momento) + INTERVAL 7 DAY
SETTINGS ttl_only_drop_parts = 1;

CREATE TABLE IF NOT EXISTS zenit.trazas
(
    inicio                 DateTime64(9, 'UTC'),
    fin                    DateTime64(9, 'UTC'),
    duracion_ns            UInt64,
    traza_id               String,
    span_id                String,
    padre_id               String,                     -- vacio en el span raiz
    nombre                 LowCardinality(String),
    tipo                   LowCardinality(String),     -- servidor, cliente, interno, productor, consumidor
    estado                 LowCardinality(String),     -- ok, error o sin_definir
    estado_mensaje         String,
    zenit_asset_id         LowCardinality(String),
    host_name              LowCardinality(String),
    service_name           LowCardinality(String),
    cloud_provider         LowCardinality(String),
    cloud_region           LowCardinality(String),
    deployment_environment LowCardinality(String),
    atributos              Map(LowCardinality(String), String),
    reasignado             UInt8 DEFAULT 0,
    INDEX por_traza traza_id TYPE bloom_filter(0.01) GRANULARITY 4
)
ENGINE = MergeTree
PARTITION BY toDate(inicio)
ORDER BY (zenit_asset_id, service_name, inicio)
TTL toDateTime(inicio) + INTERVAL 7 DAY
SETTINGS ttl_only_drop_parts = 1;
