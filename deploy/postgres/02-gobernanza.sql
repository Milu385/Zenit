-- Gobernanza (objetivo 3): instantaneas de configuracion y facturas importadas.
-- En una base nueva lo ejecuta PostgreSQL al primer arranque, despues de
-- 01-esquema.sql. En la plataforma que ya esta arriba se aplica a mano:
--   docker compose exec -T postgres psql -U zenit -d zenit < postgres/02-gobernanza.sql

CREATE TABLE IF NOT EXISTS instantaneas (   -- escribe la API (POST /api/gobernanza/configuraciones/instantaneas)
  id          bigserial PRIMARY KEY,
  nodo        text NOT NULL,                -- ZENIT_NODO
  activo      text,                         -- zenit_asset_id; NULL si el nodo no esta en el catalogo
  entorno     text NOT NULL,
  tomada_en   timestamptz NOT NULL,
  recibida_en timestamptz NOT NULL DEFAULT now(),
  datos       jsonb NOT NULL                -- puertos, contenedores, version del agente, intervalo
);
CREATE INDEX IF NOT EXISTS instantaneas_por_nodo ON instantaneas (nodo, tomada_en DESC);

CREATE TABLE IF NOT EXISTS facturas (       -- una por archivo importado
  id            bigserial PRIMARY KEY,
  huella        text UNIQUE NOT NULL,       -- sha256 del archivo: importar dos veces no duplica
  proveedor     text NOT NULL,
  importada_en  timestamptz NOT NULL,
  importada_por text NOT NULL
);

CREATE TABLE IF NOT EXISTS cargos (         -- lineas de la factura, atribuidas o no a un activo
  id         bigserial PRIMARY KEY,
  factura_id bigint NOT NULL REFERENCES facturas(id) ON DELETE CASCADE,
  proveedor  text NOT NULL,
  producto   text NOT NULL,
  concepto   text NOT NULL,
  monto      numeric(14, 4) NOT NULL,
  moneda     text NOT NULL,
  desde      date,
  hasta      date,
  activo     text,                          -- NULL: no asignable (RF-GOB-07)
  motivo     text NOT NULL DEFAULT '',      -- por que no se asigno
  fuente     text NOT NULL DEFAULT ''       -- p. ej. 'factura exportada 2026-09'
);
CREATE INDEX IF NOT EXISTS cargos_por_activo ON cargos (activo, desde);
