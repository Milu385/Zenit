# H-055 · Prueba de usabilidad con SUS: tareas guiadas

Cinco tareas sobre las pantallas 1 a 4 (Infraestructura, Detalle del activo, Incidentes y Detalle del incidente), más una sobre costos. Están pensadas para alguien que nunca ha visto Zenit. El participante entra con un usuario de rol **operador** (las tareas 1 a 4) y con uno de **finanzas** (la tarea 5). El facilitador le lee cada tarea en voz alta y además se la entrega en una tarjeta.

Por cada tarea se anota: éxito (sí, con ayuda o no), tiempo en segundos y comentarios. Si se cumple el tiempo límite, la tarea cuenta como no lograda y se pasa a la siguiente.

| # | Pantalla | Tarea (lo que se le dice al participante) | Criterio de éxito | Tiempo límite |
|---|---|---|---|---|
| 1 | 1 Infraestructura | "Hay un servidor que dejó de enviar datos. Dime cuál es y desde cuándo." | Nombra el activo que aparece como **Degradado** y lee su "última recepción" | 2 min |
| 2 | 3 Incidentes | "Busca el incidente abierto de severidad alta más reciente y dime qué métrica se desvió y desde cuándo." | Abre o señala el incidente correcto y dice la métrica (por ejemplo, CPU) y la hora o el "hace N min" | 3 min |
| 3 | 4 Detalle del incidente | "En ese incidente, Zenit propone una explicación. Dime en qué evidencia se apoya." | Encuentra la hipótesis y abre al menos una de las evidencias citadas: la gráfica se ve | 3 min |
| 4 | 3 o 4 | "Después de revisarlo, el equipo decidió que ese incidente no era real. Márcalo así." | El incidente queda en estado **Falso positivo** | 2 min |
| 5 | 6 Costos (rol finanzas) | "¿Cuál es el activo en la nube que más costó este mes?" | Nombra el activo con el mayor costo de la tabla del periodo | 2 min |

Notas para el facilitador:
- **Antes de cada sesión:** que haya al menos un activo degradado y un incidente abierto de severidad alta con hipótesis. En Azure se provoca con una **inyección de disco**, no de CPU: con el detector mad-v2, una de CPU no llega a severidad alta (`docker compose exec inyector python -m inyector manual disco --modo abrupto --objetivo 0.85 --duracion 900`). Anotar el número del incidente usado en cada sesión.
- **Tarea 4:** cambia los datos, así que con cada participante se usa un incidente distinto. Otra opción es que Juan José lo vuelva a abrir en la base entre una sesión y otra.
- **No se dice el nombre de los botones ni de las secciones.** Si el participante pregunta "¿dónde está…?", se responde "¿dónde lo buscarías?".
