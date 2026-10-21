-- Cuando el detector escribio cada anomalia (H-040, RNF-REN-06): el
-- diagnostico se mide desde aqui, no desde `inicio`, que es el comienzo de la
-- racha y queda unos minutos antes de la deteccion.
-- Idempotente: lo aplica postgres-esquema en cada arranque (zenit.admin
-- migrar-pg). Las filas que ya existian quedan con su `inicio`.

ALTER TABLE anomalias ADD COLUMN IF NOT EXISTS detectada_en timestamptz;
UPDATE anomalias SET detectada_en = inicio WHERE detectada_en IS NULL;
ALTER TABLE anomalias ALTER COLUMN detectada_en SET DEFAULT now();
CREATE INDEX IF NOT EXISTS anomalias_por_deteccion ON anomalias (estado, detectada_en);
