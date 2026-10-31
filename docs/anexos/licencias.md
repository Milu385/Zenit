# Inventario de licencias de terceros

Anexo de derechos de autor. Revisado el 31 de octubre de 2026 sobre `master`.

**Cómo se obtuvo:**
- **Dependencias de Python:** `pip-licenses --from=mixed`, en un entorno con exactamente los `requirements` de cada imagen.
- **Interfaz:** `npx license-checker --production`.
- **Imágenes y paquetes del sistema:** la licencia que declara el repositorio oficial de cada proyecto.

**Pendiente:** repetir `pip-licenses` dentro del contenedor `api` de la plataforma, como pide la tarea. Aquí se corrió con los mismos `requirements`, sin acceso a la plataforma.

Las licencias con copyleft van marcadas **(copyleft)**, con una línea sobre cómo se usan.

## 1. API y trabajadores de la plataforma (imagen `zenit-api`, `backend/requirements-api.txt`)

| Componente | Versión | Licencia |
|---|---|---|
| fastapi | 0.142.2 | MIT |
| starlette | 1.10.0 | BSD-3-Clause |
| pydantic / pydantic_core | 2.14.2 / 2.50.2 | MIT |
| uvicorn | 0.52.4 | BSD-3-Clause |
| httpx / httpcore / h11 | 0.28.1 / 1.0.9 / 0.16.0 | BSD-3-Clause / BSD-3-Clause / MIT |
| anyio | 4.15.1 | MIT |
| psycopg / psycopg-binary | 3.3.6 | **LGPL-3.0-only (copyleft)** |
| bcrypt | 5.0.0 | Apache-2.0 |
| boto3 / botocore / s3transfer | 1.43.110 / 1.43.123 / 0.19.3 | Apache-2.0 |
| pyarrow | 25.0.1 | Apache-2.0 |
| opentelemetry-api | 1.45.2 | Apache-2.0 |
| certifi | 2026.10.23 | MPL-2.0 |
| click, idna | 8.5.0, 3.20 | BSD-3-Clause |
| annotated-types, annotated-doc, typing-inspection, six, jmespath, urllib3 | — | MIT |
| python-dateutil | 2.9.0.post0 | Apache-2.0 / BSD |
| typing_extensions | 4.16.0 | PSF-2.0 |

## 2. Ingesta (imagen `zenit-ingesta`, `backend/ingesta/requirements.txt`)

| Componente | Versión | Licencia |
|---|---|---|
| grpcio | 1.84.0 | Apache-2.0 |
| opentelemetry-proto | 1.45.0 | Apache-2.0 |
| protobuf | 7.36.2 | BSD-3-Clause |
| psycopg / psycopg-binary | 3.3.6 | **LGPL-3.0-only (copyleft)** |
| httpx, httpcore, h11, anyio, idna, certifi, typing_extensions | (las mismas de la tabla 1) | |

## 3. Interfaz (imagen `zenit-web`, dependencias de producción)

| Componente | Versión | Licencia |
|---|---|---|
| react / react-dom / scheduler | 18.3.1 / 18.3.1 / 0.23.2 | MIT |
| uplot | 1.6.32 | MIT |
| @fontsource/poppins (tipografía Poppins) | 5.2.7 | OFL-1.1 |
| js-tokens, loose-envify | 4.0.0, 1.4.0 | MIT |
| zenit-interfaz (el propio proyecto) | 0.3.0 | Sin licencia publicada (obra del equipo) |

Las herramientas de construcción (vite, typescript, esbuild) no van en la imagen final: solo se usan para compilar.

## 4. Servicios del laboratorio (`laboratorio/servicios/*/requirements.txt`)

| Componente | Versión | Licencia |
|---|---|---|
| fastapi | 0.115.6 | MIT |
| uvicorn[standard] | 0.34.0 | BSD-3-Clause |
| asyncpg | 0.30.0 | Apache-2.0 |
| httpx | 0.28.1 | BSD-3-Clause |
| docker (SDK de Python) | 7.1.0 | Apache-2.0 |
| opentelemetry-distro / opentelemetry-exporter-otlp | 0.50b0 / 1.29.0 | Apache-2.0 |
| grpcio, opentelemetry-proto | 1.84.0, 1.45.0 | Apache-2.0 |

## 5. Imágenes y servicios de terceros

| Componente | Versión (compose) | Licencia | Repositorio oficial |
|---|---|---|---|
| InfluxDB 3 Core | `influxdb:3-core` | MIT o Apache-2.0, a elección | github.com/influxdata/influxdb |
| ClickHouse | `clickhouse/clickhouse-server:24.8` | Apache-2.0 | github.com/ClickHouse/ClickHouse |
| PostgreSQL | `postgres:16-alpine` | PostgreSQL License (tipo BSD) | postgresql.org |
| nginx | `nginx:1.27-alpine` | BSD-2-Clause | github.com/nginx/nginx |
| OpenTelemetry Collector Contrib | `otel/opentelemetry-collector-contrib:0.116.1` | Apache-2.0 | github.com/open-telemetry/opentelemetry-collector-contrib |
| Python (imagen base) | `python:3.12-slim` | PSF-2.0 (el sistema Debian de la imagen trae sus propias licencias) | python.org |
| Node.js (solo para construir la interfaz) | `node:22-alpine` | MIT | github.com/nodejs/node |
| stress-ng (imagen del inyector) | paquete de Debian | **GPL-2.0 (copyleft)** | github.com/ColinIanKing/stress-ng |
| iproute2 / `tc` (imagen de pedidos) | paquete de Debian | **GPL-2.0 (copyleft)** | git.kernel.org (iproute2) |

Antes de anexar el informe, confirmar contra cada repositorio la licencia de las imágenes y anotar la fecha de consulta.

## 6. Componentes con copyleft y cómo se usan

| Componente | Licencia | Cómo se usa | ¿Obliga a liberar nuestro código? |
|---|---|---|---|
| psycopg | LGPL-3.0 | Se importa como biblioteca, sin modificar, desde nuestro código Python (enlace dinámico) | No. La LGPL lo permite mientras no se modifique la biblioteca y se pueda reemplazar, que es el caso con un paquete de pip |
| stress-ng | GPL-2.0 | Programa aparte que el inyector ejecuta como proceso (`subprocess`); no se enlaza | No: es un servicio aparte, no código derivado |
| iproute2 (`tc`, `ip`) | GPL-2.0 | Binarios del sistema que el inyector ejecuta dentro del contenedor de pedidos (`docker exec`); no se enlazan | No, por la misma razón |

No hay ninguna dependencia AGPL. Ninguna dependencia de la interfaz que se sirve al navegador tiene copyleft. Poppins es OFL-1.1: permite incrustar la tipografía en la aplicación sin cambiar la licencia del resto.
