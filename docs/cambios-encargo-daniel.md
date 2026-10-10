# Cambios del encargo de Daniel

Qué se cambió en el repositorio para cumplir `encargo-daniel.md` (interfaz de
la plataforma y objetivo 3: roles, configuraciones y costos), y qué tiene que
saber el resto del equipo antes de traer la rama.

- Rama: `frontend-testing`, sobre `65e9eae` (merge de la épica 2).
- Commit: "Frontend-Infraestructura, incidentes, Configuracion y costos" (51 archivos).
- Detalle de cada decisión de diseño: `NOTAS.md` en la raíz.

---

## 1. Lo que cambia para ustedes

Esto es lo que puede romper o cambiar su trabajo al traer la rama.

| Cambio | A quién afecta | Qué hacer |
|---|---|---|
| **Toda ruta de la API pide sesión** (`Authorization: Bearer`), menos `/api/salud` y `/api/sesion`. Una ruta nueva que no declare su permiso responde 403 a todos, también al administrador | Quien agregue rutas a `backend/zenit/api/main.py` | Marcar cada ruta con `@requiere("permiso")`, `@autenticada` o `@publica` (en `zenit/api/autorizacion.py`). Ver el ejemplo de `/api/activos` |
| **El compose exige dos variables nuevas**: `ZENIT_SECRETO_SESION` y `TOKEN_INSTANTANEAS`. Sin ellas, `docker compose up` falla con el mensaje de qué falta | Quien despliegue la plataforma | Agregarlas a `deploy/.env` con `openssl rand -hex 32` cada una |
| **Tablas nuevas en PostgreSQL**: `instantaneas`, `facturas` y `cargos` (`deploy/postgres/02-gobernanza.sql`) | Quien tenga la base ya creada | En una base nueva se aplica sola. En la plataforma que ya está arriba: `docker compose exec -T postgres psql -U zenit -d zenit < postgres/02-gobernanza.sql` |
| **Dependencia nueva**: `bcrypt==5.0.0` en `backend/requirements-api.txt` | Quien corra la API o las pruebas en local | `pip install -r backend/requirements-api.txt` |
| **Las pruebas de la API usan sesión.** `test_repositorio_y_api.py` ahora crea la app con `con_sesion(...)` de `backend/pruebas/sesion_prueba.py` | Quien escriba pruebas de rutas | Usar `con_sesion(crear_app(..., **opciones()))` en vez de `TestClient(crear_app(...))` |
| **Sin `PG_URL` no hay usuarios.** Los usuarios viven en la tabla `usuarios` y se crean con un script | Quien pruebe la API real | `python -m zenit.gobernanza.usuarios crear <usuario> <rol>` (pide la clave). También `listar`, `rol`, `clave`, `borrar` |
| **`frontend/src/App.tsx` se rehízo.** La vista de serie que vivía ahí ahora es `componentes/VistaSerie.tsx`, y `App.tsx` solo tiene las rutas | Quien tocara la interfaz | Traer la rama antes de cambiar algo en `frontend/` |

El backend sigue pasando sus 132 pruebas y `npm run build` pasa sin errores.

---

## 2. Lo que la interfaz espera del backend (pendiente de ustedes)

La interfaz y el simulador ya usan estas formas, tal como están en
`contrato-api-v1.md` v1.1. Están escritas como tipos en
`frontend/src/contrato.ts`. Mientras no existan, la interfaz contra la API
real muestra error en esas pantallas.

| Ruta | Quién la construye | Estado |
|---|---|---|
| `GET /api/incidentes` (con `?activo=` y `?estado=`) | Detector (María Lucía) | No existe en el backend |
| `GET /api/incidentes/{id}` con `evidencia` y `diagnostico` | Detector y diagnóstico | No existe |
| `POST /api/incidentes/{id}/estado` con `{estado: "confirmada" \| "falso_positivo"}` | Detector | No existe |
| `POST /api/incidentes/{id}/diagnostico` (200 con el diagnóstico o 202 si queda en cola) | Diagnóstico (Juan José) | No existe |
| `GET /api/activos` y `/api/activos/{id}` con la forma ampliada: `estado`, `ultima_recepcion`, `incidentes_abiertos`, `cpu_reciente` | Quien tenga la tabla `anomalias` (para `incidentes_abiertos` y el estado `atencion`) | Hoy devuelve solo `{id, claves}`. `/api/activos/{id}` no existe |
| `GET /api/laboratorio/inyecciones` | Laboratorio | Solo existe en el simulador |

Los permisos que deben declarar esas rutas ya existen en
`backend/zenit/gobernanza/permisos.py`: `incidentes:ver`,
`incidentes:gestionar`, `activos:ver` y `laboratorio:ver`.

Dos preguntas para cerrar entre todos:

- ¿Qué responde `POST /api/incidentes/{id}/estado`? La interfaz no depende de eso, pero conviene fijarlo.
- ¿Hace falta cerrar sesión en el servidor (`DELETE /api/sesion`)? Hoy el token solo caduca a las 8 h.

---

## 3. Lo que se agregó fuera del contrato (hay que acordarlo)

Va marcado `FUERA DEL CONTRATO` en el código. Si el equipo no lo acepta, se
quita o se cambia.

| Qué | Por qué |
|---|---|
| `POST /api/gobernanza/configuraciones/instantaneas` | Por aquí llega la instantánea de cada nodo; sin ella el reporte de configuraciones no tiene datos. La envía `scripts/instantanea-nodo.sh` con `TOKEN_INSTANTANEAS` |
| `POST /api/gobernanza/costos/facturas?proveedor=digitalocean` (cuerpo: el CSV) | Importar la factura. Responde el resumen, con las líneas que no se pudieron atribuir y su motivo |
| Permisos `configuraciones:enviar` (administrador) y `costos:importar` (administrador, finanzas) | Quién puede enviar instantáneas y subir facturas |
| Nombres de permiso (`activos:ver`, `incidentes:gestionar`...) | El contrato solo da el ejemplo `incidentes:ver`; el resto sigue las filas de su tabla de la sección 2 |
| `ultima_recepcion: null` | Para un activo que nunca reportó; el contrato no dice qué va ahí |

---

## 4. Qué se hizo de cada entrega del encargo

### Lunes 5 de octubre · La base

- **Estructura de la interfaz** en `frontend/src/`: rutas (`enrutador.tsx`, sin dependencias nuevas), diseño general de los wireframes (`componentes/Marco.tsx` y `marco.css`: barra lateral negra, cabecera con saludo y tres indicadores, paneles amarillo, azul y blanco).
- **Los cinco estados transversales** (vacío, cargando, error, datos desactualizados y sin permiso) en `componentes/Estados.tsx`, `componentes/SegunCarga.tsx` y `useCarga.ts`. Si un refresco falla y ya había datos, se siguen mostrando con su hora y un aviso.
- **Servidor de datos simulados** en `frontend/simulado/`: un plugin de Vite que responde con las formas exactas del contrato y aplica la misma matriz de permisos que la API. Se arranca con `npm run dev:simulado`. Usuarios `daniel` (administrador), `lucia` (operador) y `juanjose` (finanzas), clave `zenit`; solo existen en el simulador. `ZENIT_ESCENARIO` (`vacio`, `error`, `lento`, `intermitente`) fuerza cada estado.

### Viernes 9 de octubre · Flujo del operador, primera mitad

- **Pantalla 1, inventario** (`pantallas/Inventario.tsx`). Agrupado por estado y no por proveedor: degradado, requiere atención, sin datos, operativo, retirado. El proveedor es filtro y etiqueta. A la derecha, el "Inventario híbrido" del wireframe.
- **Pantalla 3, incidentes** (`pantallas/Incidentes.tsx`). Los cinco estados se distinguen por símbolo, texto y color. Marcar falso positivo es un botón en la misma fila.
- **Pantalla 9, inicio de sesión** (`pantallas/Login.tsx`). Sin "olvidé mi contraseña" ni "crear cuenta": las cuentas las crea el administrador.

### Viernes 16 de octubre · Flujo completo y gobernanza en el backend

- **Pantalla 4, detalle del incidente** (`pantallas/DetalleIncidente.tsx`). El diagnóstico se titula "Hipótesis", lleva la advertencia de que no es una conclusión y va al lado de la evidencia numerada. Al elegir una evidencia se dibuja su serie. El texto del modelo se inserta como texto, nunca como HTML.
- **Pantalla 2, detalle del activo** (`pantallas/DetalleActivo.tsx`), reutilizando la vista de serie.
- **Sesión y roles en la API**:
  - `POST /api/sesion` y `GET /api/yo`.
  - Claves con bcrypt, costo 12 (`zenit/gobernanza/claves.py`).
  - Tokens firmados con HMAC, que duran 8 h (`sesiones.py`).
  - Bloqueo temporal tras varios intentos fallidos: responde 429.
  - Denegación por defecto (`api/autorizacion.py` y `gobernanza/permisos.py`).
- **Reporte de configuraciones**:
  - `scripts/instantanea-nodo.sh` toma la instantánea de un nodo: puertos que escuchan, contenedores con imagen, versión y usuario, versión del agente e intervalo de muestreo.
  - `zenit/gobernanza/configuraciones.py` la compara entre entornos con cinco reglas: puerto expuesto no esperado, versión del agente distinta, intervalo de muestreo distinto, contenedor como root e imagen en versiones distintas.
  - Lo esperado está en `deploy/gobernanza/politica.json`.
  - Se consulta en `GET /api/gobernanza/configuraciones`.
- **Reporte de costos**:
  - `zenit/gobernanza/costos.py` importa el CSV de DigitalOcean y atribuye cada línea a un activo.
  - Una línea se atribuye solo si nombra exactamente una clave del catálogo.
  - Lo que no se puede atribuir queda aparte, con su motivo, y nunca se reparte.
  - Importar dos veces el mismo archivo no duplica nada.
  - Se consulta en `GET /api/gobernanza/costos`.
- **Pruebas**: `backend/pruebas/test_gobernanza.py`.

### Viernes 23 de octubre · Integración

- **Pantallas 5 y 6** (`pantallas/Configuraciones.tsx` y `pantallas/Costos.tsx`), como tablas con filtros y totales. Costos incluye el formulario para importar la factura.
- **Interfaz contra la API real: pendiente.** Las llamadas (`frontend/src/api.ts`) ya apuntan a las rutas reales y `npm run dev` las manda a `localhost:8000`. Sesión, series, configuraciones y costos funcionan contra el backend. Incidentes y el inventario ampliado esperan las rutas de la sección 2.
- **Prueba de usabilidad**: se puede hacer hoy con el simulador.

---

## 5. Lo que no se hizo, a propósito

- **Panel general, Observabilidad, Análisis con IA y Seguridad** están en los wireframes pero no entre las pantallas del encargo (1 a 6 y 9).
- **Gestión de usuarios** es un script y no una pantalla, como permite el encargo en su sección 4.
- **Proyección, presupuesto, gráficas de torta, recomendaciones de IA y "Cambios recientes"** de los wireframes no tienen datos en el contrato v1. No se inventaron.
- **La barra de memoria del inventario** no está porque el contrato solo trae `cpu_reciente`.
- **Registros y trazas como evidencia**: el contrato v1 solo trae métricas, y la pantalla lo dice.
- **Modo oscuro**: sin decidir.

---

## 6. Cómo probarlo

```bash
# solo la interfaz, sin backend
cd frontend && npm install && npm run dev:simulado      # http://localhost:5173

# pruebas del backend
cd backend && pip install -r requirements-api.txt -r requirements-pruebas.txt && python -m pytest
```

Para la API real en local hacen falta PostgreSQL con `01-esquema.sql` y
`02-gobernanza.sql`, `PG_URL` exportada y un usuario creado con el script de
la sección 1. Los pasos completos están en `README.md` y `NOTAS.md`.
