# Inventario de datos

Insumo para la evaluación de impacto (María Lucía). Una fila por cada tipo de dato que guarda o mueve la plataforma. Revisado el 31 de octubre de 2026 sobre `master` y los PR #29 (redacción de correos y secretos) y #30 (política de privacidad).

Fuentes:
- **Almacenes y plazos:** `deploy/compose.yml`, `deploy/.env.example` (`RETENCION_*`), `deploy/clickhouse/01-esquema.sql` (TTL), `deploy/postgres/*.sql` y `deploy/nginx.conf`.
- **Quién ve qué:** `backend/zenit/gobernanza/permisos.py`.
- **Política publicada:** `textos-legales.md`, sección 1.

Notas para leer la tabla:
- Todo vive en la VM `zenit-plataforma` de Microsoft Azure, en Estados Unidos, así que todo "sale del país".
- **Hasta el cierre** quiere decir hasta el 12 de noviembre de 2026, cuando la plataforma y sus datos se eliminan.
- **Administrador** incluye el acceso directo a la VM y a las bases (Juan José).

| Dato | De quién es | Dónde vive | Cuánto tiempo | Quién lo ve | ¿Sale del país? |
|---|---|---|---|---|---|
| Usuario, rol, derivación bcrypt de la clave y si está activo | Usuarios de la plataforma | PostgreSQL, tabla `usuarios` | Hasta el cierre | Administrador (por línea de órdenes; no hay pantalla) | Sí, a Azure |
| Token de sesión | Usuarios de la plataforma | Navegador del usuario (`sessionStorage` o `localStorage`); el servidor no lo guarda, solo lo verifica con su firma | 8 h, o hasta cerrar la pestaña | El propio usuario | No se guarda en el servidor |
| Quién confirmó o descartó un incidente y cuándo (`marcada_por`, `marcada_en`) | Usuarios (operador, administrador) | PostgreSQL, tabla `anomalias` | Hasta el cierre | Administrador, operador, seguridad | Sí, a Azure |
| Quién importó una factura (`importada_por`) | Usuarios (finanzas, administrador) | PostgreSQL, tabla `facturas` | Hasta el cierre | Administrador, finanzas | Sí, a Azure |
| Métricas de los servidores (CPU, memoria, disco, red, procesos) | Servidores observados; las etiquetas traen el nombre del anfitrión | InfluxDB, `zenit_raw` y `zenit_1m` | 30 días | Todos los roles | Sí, a Azure |
| Resumen por hora de las métricas | Servidores observados | InfluxDB, `zenit_1h` | 180 días | Todos los roles; finanzas lo ve cruzado con el costo | Sí, a Azure |
| Indicadores internos de la ingesta | La plataforma | InfluxDB, `zenit_interno` | 7 días | Administrador | Sí, a Azure |
| Registros (logs) de los servidores | Servidores observados; **pueden traer IP, usuarios del sistema, correos o credenciales** en el texto | ClickHouse, tabla `registros` | 7 días (TTL) | Administrador, operador, seguridad | Sí, a Azure. Al modelo, solo redactados (H-041, PR #29) |
| Trazas de las aplicaciones | Servicios observados; los atributos pueden traer URL o parámetros | ClickHouse, tabla `trazas` | 7 días (TTL) | Administrador, operador, seguridad | Sí, a Azure. Al modelo, solo un resumen redactado |
| Estado de las fuentes y telemetría huérfana (nombre del anfitrión, proveedor, volumen, última recepción) | Servidores observados | PostgreSQL, tablas `fuentes` y `huerfanas` | Hasta el cierre | Administrador | Sí, a Azure |
| Catálogo de activos (nombre, región, ids nativos de la nube, claves) y ejecuciones del descubrimiento | Infraestructura del equipo y de Kaderno | PostgreSQL, tablas `activos`, `claves_nativas` y `descubrimientos` | Hasta el cierre | Todos los roles el inventario; administrador el descubrimiento | Sí, a Azure |
| Anomalías (incidentes) y su evidencia del detector | Servidores observados | PostgreSQL, tabla `anomalias` | Hasta el cierre | Administrador, operador, seguridad | Sí, a Azure |
| Hipótesis del modelo, evidencia citada y lo que se envió (redactado) | Generado por el modelo a partir de los servidores observados | PostgreSQL, tabla `diagnosticos` (`texto`, `evidencia_citada`, `evidencia_recuperada`); desde el parche 1eda04a no guarda el texto crudo de los registros | Hasta el cierre | Administrador, operador, seguridad | Sí: a Azure, y la evidencia **redactada** a Anthropic, en EE. UU. |
| Modelo usado y tokens por diagnóstico | La plataforma | PostgreSQL, tabla `diagnosticos` | Hasta el cierre | Administrador (`diagnostico:consumo`, PR #20) | Sí, a Azure |
| Instantáneas de configuración de los nodos (puertos, contenedores, versiones) | Infraestructura del equipo | PostgreSQL, tabla `instantaneas` | Hasta el cierre | Administrador, seguridad | Sí, a Azure |
| Facturas y cargos (producto, concepto, monto, activo) | Cuenta de nube del equipo | PostgreSQL, tablas `facturas` y `cargos` | Hasta el cierre | Administrador, finanzas | Sí, a Azure |
| Verdad de referencia de las inyecciones (tipo, nodo, inicio, fin, causa y manifestación) | Laboratorio del equipo | PostgreSQL, tabla `inyecciones`; archivo en el nodo; `/datos/salida` en la ingesta | Hasta el cierre | Administrador (`laboratorio:ver`) | Sí, a Azure |
| Rechazos del borde: IP de origen, certificado del cliente y ruta de cada petición rechazada (400, 401, 403, 429, 495, 496) | Quien se conecta a la plataforma: usuarios, nodos o terceros | Salida del contenedor `borde` y archivo `/var/log/zenit/rechazos-otlp.log` | **Sin plazo definido** (ver pendientes) | Administrador (acceso a la VM) | Sí, a Azure |
| Telemetría que la ingesta no pudo guardar | Servidores observados | Archivos `/datos/salida/rechazadas/*.jsonl` | **Sin plazo definido** (ver pendientes) | Administrador (acceso a la VM) | Sí, a Azure |

## Pendientes que salen del inventario

1. **Rechazos del borde y telemetría rechazada:** guardan IP de origen o pedazos de registros sin plazo de borrado. La política dice que los datos se borran automáticamente al vencer su plazo. Hay dos opciones: rotarlos (por ejemplo, 7 días como los registros) o declararlos en la política.
2. **PostgreSQL** no tiene borrado automático: todo se elimina al cierre, con la plataforma. La política lo dice ("Hasta el cierre del proyecto"), pero solo para las hipótesis. Conviene que la tabla de la política diga lo mismo para usuarios, incidentes y costos, o que la evaluación de impacto lo declare.
3. **Kaderno:** su telemetría entra a la plataforma (pasivo, autorizado en la sección 3 de los textos legales). Si sus registros traen datos de usuarios de Kaderno, esos titulares no son del equipo: hay que mencionarlo en la evaluación de impacto.
