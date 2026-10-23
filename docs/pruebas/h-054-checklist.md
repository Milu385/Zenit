# H-054 · Estados y accesibilidad de la interfaz (P-VIS-07, P-VIS-08)

Rama `h-054-accesibilidad`. Revisión hecha sobre el código y en el simulador (`npm run dev:simulado`), el 23 de octubre de 2026. **Falta correr Lighthouse**: la tabla de la sección 3 tiene las columnas vacías.

## 1. P-VIS-07 · Cinco estados en las ocho pantallas

Los estados salen de los componentes compartidos:
- `SegunCarga` y `useCarga`: cargando, error, sin permiso y datos desactualizados.
- `SinDatos`: vacío.
- `VistaSerie`: aviso de fuente degradada en las gráficas.

Cada estado se distingue por texto, no solo por color.

| # | Pantalla | Vacío | Cargando | Error | Desactualizado | Sin permiso |
|---|---|---|---|---|---|---|
| 9 | Inicio de sesión | No aplica | ✓ "Entrando..." en el botón | ✓ mensaje bajo el formulario | No aplica | No aplica |
| 1 | Infraestructura | ✓ | ✓ | ✓ | ✓ refresco cada 15 s | ✓ |
| 2 | Detalle del activo | ✓ "No hay un activo..." y serie sin datos | ✓ | ✓ | ✓ refresco cada 30 s y aviso de fuente degradada en la gráfica | ✓ |
| 3 | Incidentes | ✓ "No hay incidentes abiertos" | ✓ | ✓ | ✓ refresco cada 15 s | ✓ |
| 4 | Detalle del incidente | ✓ **nuevo:** un 404 dice "No hay un incidente N" | ✓ | ✓ | ✓ **nuevo:** refresco cada 30 s | ✓ |
| 5 | Configuración | ✓ "Sin hallazgos" | ✓ | ✓ | ✓ refresco cada 60 s | ✓ |
| 6 | Costos | ✓ | ✓ | ✓ | ✓ **nuevo:** refresco cada 5 min | ✓ |
| 7 | Fuentes | ✓ en cada bloque | ✓ | ✓ | ✓ refresco cada 30 s | ✓ |

Cambios de esta rama:
- **Detalle del incidente:** un 404 dejó de verse como error de carga, y antes no se refrescaba, así que nunca podía quedar "desactualizado".
- **Costos:** antes no se refrescaba.
- **Evidencia del detalle (H-039):** tiene su propia carga. Si falla, lo dice en su bloque y el resto de la pantalla sigue.

### Cómo provocar cada estado (en Azure)

| Estado | Cómo |
|---|---|
| Vacío | Un activo que no existe: `/activos/no-existe` (sus incidentes y su ficha salen vacíos). En la lista, un filtro sin incidentes, por ejemplo `/incidentes?ver=cerrada` si no hay ninguno cerrado |
| Cargando | Herramientas del navegador, pestaña Red, "Slow 3G", y recargar |
| Error | Herramientas del navegador, bloquear la URL `/api/...` de la pantalla (Network request blocking) y recargar |
| Desactualizado | Con la pantalla abierta, bloquear la URL de la API: al siguiente refresco aparece el aviso con la hora de los últimos datos. En una gráfica, abrir el nodo de AWS que dejó de enviar datos: aparece el aviso de fuente degradada |
| Sin permiso | Entrar como `finanzas` y abrir `/incidentes` |

En el simulador: `ZENIT_ESCENARIO=vacio`, `lento`, `error` o `intermitente` (este último da el aviso de desactualizado), con `npm run dev:simulado`.

## 2. P-VIS-08 · Recorrido solo con teclado

Recorrido: inicio de sesión, incidentes, detalle, confirmar o descartar, y volver. Se hizo con Tab, Shift+Tab y Enter en el simulador, mirando `document.activeElement` en cada paso.

| Paso | Antes | Arreglo | Después |
|---|---|---|---|
| Inicio de sesión | El foco empieza en Usuario; Tab va a Clave y luego a "Mostrar clave" (tiene `aria-label`); Enter entra | No hizo falta | Igual |
| Al entrar y en cada cambio de pantalla | **El foco se perdía** en el cuerpo de la página, y había que volver a recorrer desde arriba | `Marco.tsx`: el foco pasa al título `h1` de la pantalla (`tabIndex=-1`) | El foco queda en "Incidentes", "Incidente 41", etc. |
| Lista de incidentes | Las filas solo se abrían con el ratón; el teclado tenía que llegar al enlace del título | La fila es enfocable (`tabIndex=0`) y se abre con Enter o Espacio; el enlace queda fuera del orden de tabulación para no duplicar paradas; foco visible en la fila | Tab: filtros, fila 1, "Falso positivo" de la fila 1, fila 2... |
| "Falso positivo" desde la fila | **El foco se perdía**: el botón desaparece al marcar | El foco pasa al mensaje "Incidente N marcado como falso positivo" (`aria-live`) | El foco queda en el mensaje; el siguiente Tab sigue en la tabla |
| Detalle: confirmar o descartar | **El foco se perdía**: el botón pulsado desaparece | El foco pasa al mensaje "Incidente confirmado como real" o "...marcado como falso positivo" (`role=status`) | El foco queda en el mensaje; Shift+Tab llega a "← Incidentes" |
| Volver | "← Incidentes" es un enlace y se abre con Enter | Con el arreglo de los títulos | El foco queda en el título de la lista |

Otros puntos del recorrido:
- **`:focus-visible`:** ya estaba en `marco.css` (contorno azul de 3 px). Se agregó contorno blanco dentro del panel azul, contorno negro dentro de los amarillos, y foco visible en las filas.
- **Diálogos de confirmación:** no hay ninguno. Confirmar y descartar son acciones directas (decisión de diseño de la pantalla 3), así que la regla de devolver el foco al botón que abrió el diálogo se aplica al mensaje que reemplaza al botón.
- **Botones con solo un ícono:** el único es "Mostrar clave", que ya tenía `aria-label`. Los íconos de la barra lateral llevan texto al lado.
- **Saltar al contenido:** el enlace aparece con el primer Tab.

## 3. Contraste y Lighthouse

### Contraste (WCAG 2.1 AA, 4,5:1 para texto normal)

Se calculó el contraste de los colores de `marco.css` con la fórmula de WCAG. Estos no llegaban:

| Par | Antes | Arreglo | Después |
|---|---|---|---|
| Texto blanco sobre el panel azul (hipótesis) | 3,47 | fondo `--azul-panel` #2a62d4 | 5,53 |
| `--apagado` sobre blanco (falso positivo, sin datos) | 4,18 | #66655f | 5,85 |
| `--bien` sobre blanco (operativo, mensajes de éxito) | 4,14 | #157a40 | 5,40 |
| `--regular` sobre blanco (requiere atención, severidad media) | 3,65 | #9e5600 | 5,55 |
| Texto gris (`.nota`, `.meta`) sobre amarillo | 3,32 | #3d3c39 dentro de `.panel-amarillo` | 6,76 |
| Enlaces sobre amarillo | 4,48 | color de texto dentro de `.panel-amarillo` | 11,48 |
| Severidad y niveles en color sobre amarillo (2,2 a 2,9) | menos de 3 | fondo blanco en `.severidad`, `.nivel` y `.texto-mal` dentro de `.panel-amarillo` | 4,8 o más |

### Lighthouse, accesibilidad (meta: 90 o más y sin errores de contraste)

**Pendiente: lo corre Daniel.** Lighthouse no está instalado en el equipo donde se hizo la revisión. Pasos: Chrome, herramientas del navegador, pestaña Lighthouse, solo "Accessibility", modo escritorio. Primero en `master`, luego en esta rama, con la misma sesión de administrador.

| Pantalla | Antes (`master`) | Después (`h-054-accesibilidad`) | Errores de contraste después |
|---|---|---|---|
| 1 Infraestructura | | | |
| 2 Detalle del activo | | | |
| 3 Incidentes | | | |
| 4 Detalle del incidente | | | |

Si alguna pantalla queda por debajo de 90, anotar aquí los avisos de Lighthouse para corregirlos en la misma rama.
