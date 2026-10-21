-- Evidencia recuperada junto con cada diagnostico (H-039, tercer criterio):
-- lo que el modelo vio queda guardado para poder verificar sus citas despues.
-- Idempotente: lo aplica postgres-esquema en cada arranque (zenit.admin
-- migrar-pg). A mano:
--   docker compose exec -T postgres psql -U zenit -d zenit < postgres/07-evidencia-diagnostico.sql

ALTER TABLE diagnosticos ADD COLUMN IF NOT EXISTS evidencia_recuperada jsonb;
CREATE INDEX IF NOT EXISTS diagnosticos_por_anomalia ON diagnosticos (anomalia_id, generado);
