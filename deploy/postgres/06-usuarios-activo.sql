-- Desactivar usuarios sin borrarlos (H-044). Un usuario desactivado conserva
-- su rol y su historia (por ejemplo, marcada_por en anomalias), pero no puede
-- iniciar sesion y su sesion vigente deja de valer.
-- Idempotente: lo aplica postgres-esquema en cada arranque (zenit.admin
-- migrar-pg). A mano:
--   docker compose exec -T postgres psql -U zenit -d zenit < postgres/06-usuarios-activo.sql

ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS activo boolean NOT NULL DEFAULT true;
