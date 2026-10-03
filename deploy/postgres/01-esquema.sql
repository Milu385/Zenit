-- Tablas compartidas de la plataforma (contrato de la API v1.1, seccion 4).
-- PostgreSQL ejecuta este archivo solo la primera vez, con el volumen vacio.
-- Un cambio posterior va en un archivo nuevo y se aplica a mano.

CREATE TABLE IF NOT EXISTS inyecciones (  -- escribe el cargador de verdad, lee la evaluacion
  id            bigserial PRIMARY KEY,
  uid           text UNIQUE NOT NULL,     -- lo genera el inyector en el nodo
  tipo          text NOT NULL CHECK (tipo IN ('cpu', 'memoria', 'disco', 'caida', 'latencia')),
  nodo          text NOT NULL,            -- ZENIT_NODO del nodo victima
  activo        text,                     -- zenit_asset_id resuelto; NULL si el nodo no esta en el catalogo
  inicio        timestamptz NOT NULL,     -- registrado ANTES de inyectar
  fin           timestamptz,              -- registrado al terminar
  intensidad    text NOT NULL,
  parametros    jsonb NOT NULL DEFAULT '{}',  -- incluye campana, motivo de fallo y cierre
  estado        text NOT NULL CHECK (estado IN ('en_curso', 'completada', 'fallida')),
  causa_en      text,                     -- solo latencia: componente causante
  manifiesta_en text                      -- solo latencia: donde se ve la degradacion
);
CREATE INDEX IF NOT EXISTS inyecciones_por_activo ON inyecciones (activo, inicio);

CREATE TABLE IF NOT EXISTS anomalias (    -- escribe el detector, lee la API
  id             bigserial PRIMARY KEY,
  activo         text NOT NULL,
  metrica        text NOT NULL,
  dimensiones    jsonb NOT NULL DEFAULT '{}',
  inicio         timestamptz NOT NULL,
  fin            timestamptz,
  estado         text NOT NULL DEFAULT 'detectada',
  severidad      text NOT NULL,
  puntaje        double precision NOT NULL,
  evidencia      jsonb NOT NULL,
  version_modelo text NOT NULL,           -- RNF-REP-02
  marcada_por    text,
  marcada_en     timestamptz
);
CREATE INDEX IF NOT EXISTS anomalias_por_activo ON anomalias (activo, inicio);

CREATE TABLE IF NOT EXISTS diagnosticos ( -- escribe el servicio de diagnostico
  id               bigserial PRIMARY KEY,
  anomalia_id      bigint NOT NULL REFERENCES anomalias(id),
  texto            text NOT NULL,
  evidencia_citada jsonb NOT NULL,
  modelo           text NOT NULL,
  tokens_entrada   integer NOT NULL,
  tokens_salida    integer NOT NULL,
  generado         timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS usuarios (     -- roles de la API
  usuario    text PRIMARY KEY,
  clave_hash text NOT NULL,               -- RNF-SEG-06: argon2 o bcrypt, nunca texto plano
  rol        text NOT NULL CHECK (rol IN ('administrador', 'operador', 'seguridad', 'finanzas'))
);
