-- Catalogo canonico de activos (H-009a), huerfanas (H-011) y progreso de la
-- agregacion (H-018). Epica 1.
--
-- PostgreSQL ejecuta los archivos de esta carpeta solo la primera vez, con el
-- volumen vacio. En una plataforma que ya existia los aplica el servicio
-- postgres-esquema en cada arranque (python -m zenit.admin migrar-pg), asi que
-- todo aqui tiene que poder correr dos veces sin error ni cambio.

-- Un activo es lo que Zenit observa. Su id es el zenit.asset.id que llevan
-- las tres senales: se asigna una sola vez y no cambia nunca, aunque el
-- proveedor cambie el nombre o el recurso desaparezca.
CREATE TABLE IF NOT EXISTS activos (
  id          text PRIMARY KEY,                -- activo-do-nodo-01
  nombre      text NOT NULL,                   -- nombre en el proveedor; para el laboratorio, ZENIT_NODO
  proveedor   text NOT NULL,                   -- proxmox | digitalocean | aws | azure | desconocido
  region      text,
  entorno     text,
  origen      text NOT NULL CHECK (origen IN ('descubrimiento', 'manual')),
  estado      text NOT NULL DEFAULT 'activo' CHECK (estado IN ('activo', 'retirado')),
  ausencias   integer NOT NULL DEFAULT 0,      -- ejecuciones exitosas seguidas sin verlo
  creado      timestamptz NOT NULL DEFAULT now(),
  visto       timestamptz,                     -- ultima vez que el descubrimiento lo listo
  retirado_en timestamptz,
  atributos   jsonb NOT NULL DEFAULT '{}'      -- lo que el proveedor dice del recurso (estado nativo, tamano...)
);

-- Correspondencia de identificador nativo a activo (ADR-008). Dos clases:
--   tipo = 'host.name'  la clave que llega en la telemetria y con la que resuelve la ingesta;
--   tipo = 'nativo'     el identificador del proveedor (digitalocean:droplet/412345678,
--                       aws:instancia/i-0abc, proxmox:qemu/105). Es el que usa el
--                       descubrimiento para reconocer el recurso entre ejecuciones.
CREATE TABLE IF NOT EXISTS claves_nativas (
  clave     text PRIMARY KEY,
  activo_id text NOT NULL REFERENCES activos(id),
  tipo      text NOT NULL CHECK (tipo IN ('host.name', 'nativo')),
  proveedor text NOT NULL,
  creada    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS claves_por_activo ON claves_nativas (activo_id);

-- Una fila por ejecucion del descubrimiento y por proveedor. La ultima de
-- cada proveedor es lo que pide el criterio 5 de H-009.
CREATE TABLE IF NOT EXISTS descubrimientos (
  id          bigserial PRIMARY KEY,
  proveedor   text NOT NULL,
  inicio      timestamptz NOT NULL DEFAULT now(),
  fin         timestamptz,
  estado      text NOT NULL CHECK (estado IN ('en_curso', 'ok', 'error')),
  vistos      integer NOT NULL DEFAULT 0,
  nuevos      integer NOT NULL DEFAULT 0,
  retirados   integer NOT NULL DEFAULT 0,
  reactivados integer NOT NULL DEFAULT 0,
  error       text
);
CREATE INDEX IF NOT EXISTS descubrimientos_recientes ON descubrimientos (proveedor, inicio DESC);

-- Origenes de telemetria que no resolvieron contra el catalogo (H-011). La
-- ingesta suma aqui cada 30 s; la senal en si esta en los almacenes con
-- zenit_asset_id = sin_resolver.
CREATE TABLE IF NOT EXISTS huerfanas (
  clave       text PRIMARY KEY,                -- valor de host.name (o de la primera clave nativa que trajo)
  tipo_clave  text NOT NULL,                   -- host.name, host.id...
  proveedor   text,                            -- cloud.provider que declaro el agente
  metricas    bigint NOT NULL DEFAULT 0,       -- puntos
  registros   bigint NOT NULL DEFAULT 0,
  trazas      bigint NOT NULL DEFAULT 0,
  primera     timestamptz NOT NULL,
  ultima      timestamptz NOT NULL,
  asociada_a  text REFERENCES activos(id),     -- cuando la telemetria previa se reasigno al activo
  asociada_en timestamptz
);

-- Hasta donde llego cada nivel de resumen (H-018). Retroceder 'hasta' obliga
-- al agregador a rehacer desde ahi; es lo que hace asociar-huerfanas.
CREATE TABLE IF NOT EXISTS agregacion_progreso (
  nivel       text PRIMARY KEY CHECK (nivel IN ('1m', '1h')),
  hasta       timestamptz NOT NULL,            -- todo lo anterior esta resumido
  actualizado timestamptz NOT NULL DEFAULT now()
);
