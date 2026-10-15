-- Agregacion (H-018). Epica 1. Idempotente.
--
-- rehacer_desde lo fija quien necesita que se rehagan resumenes viejos (la
-- asociacion de huerfanas, o zenit.admin reagregar). El agregador lo lee y lo
-- limpia al empezar cada ciclo, sin pisar el progreso que esta guardando.
ALTER TABLE agregacion_progreso ADD COLUMN IF NOT EXISTS rehacer_desde timestamptz;
