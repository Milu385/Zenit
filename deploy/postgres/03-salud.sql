-- Estado de las fuentes de telemetria (H-021). Epica 1.
-- Idempotente: lo aplica postgres-esquema en cada arranque.

-- Una fila por origen y senal. La escribe la ingesta cada 10 s con la hora de
-- RECEPCION (no la del dato): una fuente que reenvia su cola despues de un
-- corte trae datos viejos, pero esta reportando. Lo que dice si una fuente se
-- callo es cuando se recibio algo por ultima vez.
CREATE TABLE IF NOT EXISTS fuentes (
  activo    text NOT NULL,                  -- zenit_asset_id; sin_resolver para las huerfanas
  host_name text NOT NULL,
  senal     text NOT NULL CHECK (senal IN ('metricas', 'registros', 'trazas')),
  proveedor text,
  primera   timestamptz NOT NULL,
  ultima    timestamptz NOT NULL,
  recibidas bigint NOT NULL DEFAULT 0,      -- acumulado desde la primera recepcion
  errores   bigint NOT NULL DEFAULT 0,
  PRIMARY KEY (activo, host_name, senal)
);
