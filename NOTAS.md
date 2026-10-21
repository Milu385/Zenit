# NOTAS · interfaz, roles y gobernanza

Decisiones de diseño del encargo de Daniel (interfaz de la plataforma y
objetivo 3). Se pega al empezar cada sesión de trabajo. Contrato de referencia:
`contrato-api-v1.md` versión 1.1 (2026-10-03).

---

## 1. Cómo correrlo

### Interfaz con datos simulados

```bash
cd frontend
npm install
npm run dev:simulado                       # http://localhost:5173
ZENIT_ESCENARIO=intermitente npm run dev:simulado
```

`npm run dev` (sin `:simulado`) manda `/api` a la API real en `localhost:8000`; si no esta corriendo, Vite muestra `http proxy error ... ECONNREFUSED` y el inicio de sesion falla.

| Usuario | Rol |
|---|---|
| `daniel` | administrador |
| `lucia` | operador |
| `juanjose` | finanzas |

La clave de los tres es `zenit`. Solo existe en `frontend/simulado/datos.ts`; la API real no la conoce.

`ZENIT_ESCENARIO` cambia lo que responde el simulador, para ver los estados transversales:

| Escenario | Qué pasa |
|---|---|
| `normal` (por defecto) | Datos completos |
| `vacio` | Listas vacías, reporte de costos sin cargos |
| `error` | Toda ruta con sesión responde 503 |
| `lento` | 2,5 s por respuesta |
| `intermitente` | Una de cada tres responde 503: se ve el aviso de datos desactualizados |

El simulador está en `frontend/simulado/`, como un plugin de Vite que solo se carga con `--mode simulado`. Sirve las rutas del contrato con los tipos de `src/contrato.ts` y aplica la misma matriz de permisos que la API. Si el contrato cambia, `npm run build` falla en el simulador y en la pantalla a la vez.

### API real

Variables nuevas en `deploy/.env` (están vacías en `.env.example`):

| Variable | Qué es |
|---|---|
| `ZENIT_SECRETO_SESION` | Firma los tokens de sesión. 32 bytes o más en hexadecimal: `openssl rand -hex 32` |
| `TOKEN_INSTANTANEAS` | Lo usan los nodos para enviar su instantánea de configuración: `openssl rand -hex 32` |

Si falta `ZENIT_SECRETO_SESION`, la API genera uno al arrancar y las sesiones mueren en cada reinicio. El compose lo exige.

Tablas nuevas: `deploy/postgres/05-gobernanza.sql`. Se aplica sola en una base nueva. En una base que ya existe se aplica a mano:

```bash
cd deploy && docker compose exec -T postgres psql -U zenit -d zenit < postgres/05-gobernanza.sql
```

Los usuarios se crean con un script, desde `deploy/`; la pantalla de usuarios no se construyó (encargo, sección 4):

```bash
docker compose exec api python -m zenit.gobernanza.usuarios crear daniel administrador   # pide la clave
docker compose exec api python -m zenit.gobernanza.usuarios listar
# también: rol <usuario> <rol>, clave <usuario>, borrar <usuario>
docker compose exec api python -m zenit.gobernanza.usuarios desactivar maria   # H-044; 'activar' lo deshace
```

Desactivar no borra: el usuario conserva su rol y lo que marcó, pero no puede iniciar sesión (recibe el mismo mensaje que con una clave mala) y su sesión vigente deja de valer. Necesita la columna `activo` de `deploy/postgres/06-usuarios-activo.sql`, que `postgres-esquema` aplica sola al arrancar.

### Instantánea de configuración de un nodo

`scripts/instantanea-nodo.sh` imprime el JSON. Con `--enviar` lo manda a la API. Su encabezado explica cómo tomarla por SSH.

### Factura de costos

Desde la pantalla de costos, con un rol que tenga `costos:importar`. Otra opción es hacer un `POST` del CSV a `/api/gobernanza/costos/facturas?proveedor=digitalocean`. Importar dos veces el mismo archivo no duplica nada (se identifica por huella sha256).

---

## 2. Decisiones de diseño

### Generales

- **Solo tema claro.** Los wireframes son claros, y un tema oscuro sin diseñar se ve peor que ninguno. Queda abierto (ver la sección 4).
- **Lo que un rol no puede ver no aparece en la navegación.** La API es quien decide (responde 403); esconder es solo comodidad. Si alguien entra por URL directa, ve el estado "sin permiso", no un error.
- **Nada de la API se inserta como HTML.** React escapa todo el texto. El simulador trae un `<script>` dentro de una observación del diagnóstico para comprobarlo: se ve como texto.
- **El texto lleva el estado, además del color.** Los estados de activo e incidente llevan símbolo, texto y color a la vez, y la severidad alta va subrayada.
- **Datos desactualizados.** Si un refresco falla y ya había datos, se siguen mostrando con su hora y un aviso con botón de reintentar, en vez de cambiarlos por un error (`useCarga`).

### Sesión (pantalla 9)

- **Dónde vive el token.** Va en `sessionStorage`, que se borra al cerrar la pestaña. Con "mantener la sesión" va a `localStorage`. Ninguno de los dos lo protege de un XSS: la defensa es no insertar HTML nunca. Se descartó la cookie HttpOnly porque el contrato fija `Authorization: Bearer`.
- **Cerrar sesión solo olvida el token.** El contrato no tiene `DELETE /api/sesion`. El token caduca solo a las 8 h. Si el equipo quiere revocar sesiones, hace falta una ruta nueva (sección 3).
- **Sin "olvidé mi contraseña" ni "crear cuenta".** No hay correo, y las cuentas las crea el administrador (RF-GOB-02).
- **Un 401 en medio del uso** borra el token y vuelve al login con el aviso "tu sesión venció".

### Inventario (pantalla 1)

- **Se agrupa por estado, no por proveedor.** El orden es degradado, requiere atención, sin datos, operativo y retirado. El proveedor es un filtro y una etiqueta.
- **Filtros:** búsqueda, proveedor, entorno y estado.
- **La franja de arriba** dice cuántos de los activos vigentes requieren atención. Los retirados no cuentan.
- **Retirados.** Se muestran al final, atenuados y con su insignia. No cuentan como fuentes en los indicadores de la cabecera.
- **Una sola barra, la de CPU de los últimos 5 min.** El contrato solo trae `cpu_reciente`. La de memoria del primer borrador se quitó.

### Incidentes (pantalla 3)

- **Orden:** primero los abiertos (detectada, con hipótesis, confirmada), luego por severidad y luego por inicio. El contrato ya los entrega por severidad e inicio; la interfaz solo sube los abiertos.
- **Falso positivo en un clic.** El botón está en la fila y llama a `POST /api/incidentes/{id}/estado` (H-037). La fila toma el incidente que responde la API. Si responde 409 (ya estaba cerrado o descartado), lo dice y el siguiente refresco pone la lista al día.
- **Una columna "Hipótesis"** dice si ya hay diagnóstico (`tiene_diagnostico`).

### Detalle del incidente (pantalla 4)

- **Arriba, "Qué se desvió".** Muestra la métrica, los valores observado y esperado, la ventana y el método del detector.
- **La hipótesis y la evidencia van lado a lado,** con el mismo peso. La hipótesis está titulada así, lleva la advertencia de que no es una conclusión y un enlace "se apoya en N evidencias" que lleva el foco a la lista. El modelo y la hora van en letra pequeña.
- **La evidencia es una lista numerada.** Primero va la que cita la hipótesis (`evidencia_citada`, con su observación). Después, la métrica que se desvió y las `metricas_relacionadas` del detector en la ventana de la anomalía. Al elegir una, se dibuja su serie con 10 min de margen a cada lado.
- **Pedir o regenerar la hipótesis.** El botón se muestra a quien tiene `incidentes:gestionar`. Con 200 se muestra el diagnóstico al momento. Con 202 la página dice "en cola" y vuelve a pedir el incidente cada 15 s hasta que llegue.
- **Sin "confianza".** El primer borrador la mostraba, pero el contrato no la trae.
- **Cada cita lleva a su evidencia (H-051).** Debajo de la hipótesis se listan las citas con su métrica e intervalo; al pulsar una se dibuja esa serie. Si una cita no trae un intervalo legible se usa la ventana del detector y se dice.
- **Versión del modelo sin protagonismo.** La del detector (`version_modelo`) va en letra pequeña junto al método; la del diagnóstico, al pie de la hipótesis.
- **Quién marcó.** Si el incidente se confirmó o descartó, la cabecera dice quién y cuándo (`marcada_por`, `marcada_en`).
- **Sin servicio de diagnóstico.** Mientras no exista `POST /diagnostico` (H-040), pedir una hipótesis dice que el servicio no está disponible y la evidencia se ve igual.

### Detalle del activo (pantalla 2)

- Usa `GET /api/activos/{id}` (con `claves`). Un 404 se muestra como "no hay un activo así", no como error.
- Los incidentes se piden con `?activo=`. El costo atribuido solo aparece si el rol tiene `costos:ver`, y usa el periodo por defecto de la API.
- **Registros y trazas recientes (H-049).** Solo con `registros:ver`. Ventana de 15 min, 1 h (por defecto), 6 h o 24 h. Los registros se filtran por nivel y por texto, y muestran los 200 más recientes; los que vienen de una traza tienen "Ver su traza". Las trazas se resumen por traza (operación raíz, duración, spans, errores, servicios) y al elegir una se ven sus spans en árbol, con los de otro activo marcados. Si ClickHouse no responde (503), la sección lo dice y el resto de la ficha sigue.

### Fuentes (pantalla 7, H-053)

- **Solo administrador** (`fuentes:ver`). La sección aparece en la barra lateral solo con ese permiso.
- **Arriba, cuatro cifras:** fuentes degradadas, sin datos nunca, orígenes huérfanos sin asociar y proveedores cuyo último descubrimiento falló.
- **Fuentes que no reportan.** Por defecto solo las degradadas (más de 5 min sin métricas, regla de la API) y las que nunca reportaron; una casilla muestra también las sanas. Por señal: última recepción, recibidas en 5 min y errores de la última hora.
- **Huérfanos.** Origen (clave y tipo), proveedor declarado, volumen acumulado por señal, última recepción y si ya es asociable o ya se asoció. Los asociados se ven atenuados.
- **Descubrimiento.** La última pasada por proveedor con su resultado (texto y símbolo), duración y conteos; si falló, el error.

### Configuraciones (pantalla 5, como tabla)

- Una fila por hallazgo: severidad, regla (nombre legible y detalle), activos, entornos comparados (como etiquetas) y cuándo se detectó. Hay filtros por severidad y entorno, y totales por severidad arriba.
- **Reglas del backend** (`zenit/gobernanza/configuraciones.py`):

  | Regla | Severidad |
  |---|---|
  | `puerto-expuesto-no-esperado` | Alta si el puerto es sensible (5432, 8000, 8080, 8181), media si no |
  | `version-agente-distinta` | Media |
  | `intervalo-muestreo-distinto` | Media |
  | `contenedor-como-root` | Alta |
  | `imagen-version-distinta` | Baja |

  Lo esperado está en `deploy/gobernanza/politica.json`. Ahí van los puertos por entorno y los contenedores que corren como root a propósito, con su motivo.
- **`detectado`** es la hora de la instantánea más reciente que muestra el hallazgo.
- **Entorno de cada nodo.** Los cuatro nodos del laboratorio comparten `ZENIT_ENTORNO=lab`, así que el script usa el proveedor como entorno. La plataforma usa `plataforma`.

### Costos (pantalla 6, como tablas)

- **Atribución.** Una línea de la factura se asigna a un activo solo si nombra, como palabra completa, exactamente una clave del catálogo o un id de activo. Si no nombra ninguno, o nombra varios, queda sin asignar y con su motivo. Nunca se reparte a ojo (RF-GOB-07).
- **Periodo.** Por defecto cubre todo lo importado; si no hay nada importado, el mes en curso. Con `desde` mayor que `hasta`, la API responde 422.
- **`fuente`** dice "factura exportada AAAA-MM", con el mes más frecuente de las líneas.
- **La pantalla calcula** el total y los totales por proveedor y por entorno. El entorno sale del inventario (`/api/activos`), porque el reporte de costos no lo trae.
- **Detalle de lo no asignado.** El contrato solo da el número `sin_asignar`. El detalle por línea se ve al importar, en la respuesta de la importación, que es una extensión (sección 3).
- **Tendencia del gasto (H-047).** Por mes, por entorno y por activo, con la variación del último mes contra el anterior. Cada cargo cuenta en el mes en que empieza, como en el reporte.
- **Costo contra uso (H-047).** Cada activo con su costo, CPU promedio, la hora de más CPU y memoria promedio del resumen por hora (`zenit_1h`). Se marca subutilizado si tiene costo, al menos 24 h medidas, CPU promedio < 10 %, ninguna hora con CPU ≥ 40 % y memoria promedio < 40 % (`zenit/gobernanza/subutilizados.py`). El criterio va en la respuesta y la pantalla lo muestra arriba de la tabla. No se sugiere apagar ni redimensionar.

---

## 3. Fuera del contrato: acordar con el equipo

Lo que se agregó y que no estaba en `contrato-api-v1.md`. En el código va marcado `FUERA DEL CONTRATO`.

| Qué | Por qué |
|---|---|
| `POST /api/gobernanza/configuraciones/instantaneas` (201) | Sin esto el reporte de configuraciones no tiene de dónde salir. Lo llama el script del nodo con `TOKEN_INSTANTANEAS`, no con una sesión de usuario |
| `POST /api/gobernanza/costos/facturas?proveedor=digitalocean` (201, cuerpo CSV) | Importar la factura. Responde `ResumenImportacion`, con `lineas_sin_asignar[{concepto, producto, monto, motivo}]` |
| `GET /api/gobernanza/costos/analisis?desde&hasta` (H-047, `costos:ver`) | Tendencia del gasto por mes y costo contra uso. Responde `criterio`, `activos[]` con `subutilizado` (true, false o null si faltan datos) y su `motivo`, `tendencia{meses, por_entorno, por_activo}` y `uso{desde, hasta, disponible}` |
| Permiso `configuraciones:enviar` (administrador) | Lo tiene el token de los nodos, y el administrador para pruebas |
| Permiso `costos:importar` (administrador, finanzas) | Quién sube facturas |
| Nombres de permiso (`activos:ver`, `incidentes:gestionar`...) | El contrato solo da el ejemplo `incidentes:ver`. El resto sigue las filas de la tabla de la sección 2 |
| `ultima_recepcion: null` | Para un activo que nunca reportó. El contrato no dice qué va ahí |

Preguntas abiertas:

- **`POST /api/incidentes/{id}/estado` (H-037).** Cuerpo `{"estado": "confirmada" | "falso_positivo"}`; otro valor es 422. Responde 200 con el incidente en la forma de la lista más `marcada_por` y `marcada_en`. Un incidente `cerrada` o `falso_positivo` responde 409, igual que el detector los trata como terminales; `confirmada` sí se puede cambiar a falso positivo. 404 si no existe. Falta que el equipo lo pase al contrato.
- **¿Hace falta revocar sesiones?** Hoy no hay cierre de sesión en el servidor (`DELETE /api/sesion`).
- **¿La factura de AWS y Azure?** Por ahora solo se lee el CSV de DigitalOcean.

---

## 4. Lo que falta o está bloqueado

| Qué | Estado |
|---|---|
| `GET /api/activos` y `/api/activos/{id}` con la forma ampliada (estado, `ultima_recepcion`, `incidentes_abiertos`, `cpu_reciente`) | El backend sigue devolviendo `{id, claves}`. Depende de la tabla `anomalias` del detector para `incidentes_abiertos` y `atencion` |
| `GET /api/incidentes` y `/{id}` | Construidas (H-038). Leen `anomalias` y el diagnóstico más reciente de `diagnosticos`. La evidencia sale con `ventana: {desde, hasta}` (desde = inicio menos la ventana de línea base; hasta = fin, o ahora si sigue abierta) y `metricas_relacionadas: []` hasta H-039 |
| `GET /api/incidentes/{id}/evidencia` | Construida (H-039), fuera del contrato 1.2. La serie afectada filtrada por sus dimensiones, las métricas relacionadas por tipo de fallo (`fallas` de `senales.py`), registros de error, trazas con error y la correlación del motor analítico. Lo que no se pudo recuperar va en `faltantes`. Desde H-039, `metricas_relacionadas` del detalle ya no sale vacía |
| `POST /api/incidentes/{id}/estado` | Construida (H-037). Guarda `marcada_por` y `marcada_en` en `anomalias`. Ver la sección 3 |
| `POST /api/incidentes/{id}/diagnostico` | Construida (H-040). Responde 200 con el diagnóstico en el momento; nunca 202. 502 si el proveedor del modelo falla (el incidente queda igual), 503 si falta `ZENIT_MODELO_DIAGNOSTICO`, `ANTHROPIC_API_KEY` o la redacción de H-041. El servicio `diagnosticador` genera solo los de severidad media y alta en menos de 60 s; los demás, a pedido |
| `GET /api/laboratorio/inyecciones` | Solo en el simulador |
| Registros y trazas como evidencia | El wireframe los pide, pero la evidencia del contrato v1 solo trae métricas. La pantalla lo dice |
| Tendencia de costos en el tiempo y activos subutilizados (pantalla 6) | No están en el contrato |
| Pantalla 8 | Fuera de este encargo. La gestión de usuarios es el script de la sección 1. La 7 (fuentes) ya está (H-053) |
| Modo oscuro | Sin decidir |
| `scripts/instantanea-nodo.sh` en un nodo real | Se corrió en un equipo sin Docker: su JSON pasa `leer_instantanea` y las reglas. Falta correrlo en un nodo con contenedores y enviarlo a una plataforma desplegada |
