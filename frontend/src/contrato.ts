// Formas de la API segun contrato-api-v1.md (version 1.1, 2026-10-03). La
// interfaz y el servidor simulado (simulado/) dependen SOLO de este archivo:
// si una forma cambia, `npm run build` rompe el simulador y la pantalla a la
// vez, no la integracion de la semana 15.
//
// Lo que no esta en el contrato va marcado FUERA DEL CONTRATO y esta listado
// en NOTAS.md para acordarlo con el equipo.

// -------------------------------------------------------------- sesion (§3)

export type Rol = "administrador" | "operador" | "seguridad" | "finanzas";

// Nombres de permiso: el contrato solo da el ejemplo "incidentes:ver"; el resto
// sigue las filas de la tabla de la seccion 2 (backend/zenit/gobernanza/permisos.py).
export type Permiso =
  | "activos:ver"
  | "incidentes:ver"
  | "incidentes:gestionar"
  | "configuraciones:ver"
  | "costos:ver"
  | "laboratorio:ver"
  | "usuarios:gestionar"
  | "registros:ver" // (1.2)
  | "fuentes:ver" // (1.2)
  | "configuraciones:enviar" // FUERA DEL CONTRATO
  | "costos:importar"; // FUERA DEL CONTRATO

export type PeticionSesion = { usuario: string; clave: string };
export type RespuestaSesion = { token: string; usuario: string; rol: Rol; expira: string };
export type Yo = { usuario: string; rol: Rol; permisos: Permiso[] };

// ------------------------------------------------------------- activos (§3)

// el catalogo de la epica 1 llama "proxmox" al entorno on-premise; "onprem" queda por compatibilidad
export type Proveedor = "onprem" | "proxmox" | "aws" | "azure" | "digitalocean";

// degradado: mas de 5 min sin datos (H-021). atencion: tiene incidentes abiertos.
export type EstadoActivo = "ok" | "atencion" | "degradado" | "sin_datos" | "retirado";

export type Activo = {
  id: string;
  nombre: string;
  proveedor: Proveedor;
  region: string;
  entorno: string;
  estado: EstadoActivo;
  ultima_recepcion: string | null; // null: nunca reporto (supuesto: el contrato no lo dice)
  incidentes_abiertos: number;
  cpu_reciente: number | null; // promedio de los ultimos 5 min, 0..1
};

export type DetalleActivo = Activo & { claves: string[] };

// ----------------------------------------------------- series (ya existian)

export type Serie = {
  etiquetas: Record<string, string>;
  puntos: [number, number][]; // [milisegundos, valor]
};

export type RespuestaSerie = {
  activo: string;
  metrica: string;
  resolucion: string;
  desde: string;
  hasta: string;
  agrupar: string | null;
  series: Serie[];
  intervalos_esperados: number;
  intervalos_vacios: number;
  series_omitidas: number;
  // (1.2)
  intervalo_s?: number; // segundos que abarca cada punto: 10 en crudo, mas en los resumenes (H-019)
  intervalos_incompletos?: number; // intervalos con menos observaciones de las esperadas (H-018)
  fuente_degradada?: boolean; // RF-VIS-08: mas de 5 min sin recibir metricas del activo
  ultima_recepcion?: string | null;
};

export type RespuestaMetricas = { activo: string; metricas: string[] };
export type RespuestaDimensiones = { activo: string; metrica: string; dimensiones: Record<string, string[]> };

// ---------------------------------------------------------- incidentes (§3)

export type EstadoIncidente = "detectada" | "diagnosticada" | "confirmada" | "falso_positivo" | "cerrada";
export type Severidad = "baja" | "media" | "alta";

export type Incidente = {
  id: number;
  activo: string;
  metrica: string;
  dimensiones: Record<string, string>;
  inicio: string;
  fin: string | null; // null mientras siga abierta
  estado: EstadoIncidente;
  severidad: Severidad;
  puntaje: number;
  tiene_diagnostico: boolean;
};

export type Evidencia = {
  ventana: { desde: string; hasta: string };
  valor_observado: number | null; // null si el detector no lo guardo (H-038)
  valor_esperado: number | null;
  metodo: string;
  metricas_relacionadas: string[];
};

export type EvidenciaCitada = { metrica: string; desde: string; hasta: string; observacion: string };

export type Diagnostico = {
  texto: string;
  evidencia_citada: EvidenciaCitada[];
  modelo: string;
  tokens_entrada: number;
  tokens_salida: number;
  generado: string;
};

export type DetalleIncidente = Omit<Incidente, "tiene_diagnostico"> & {
  evidencia: Evidencia; // existe siempre, aunque el diagnostico falle
  diagnostico: Diagnostico | null; // null si todavia no se genero
  version_modelo: string; // del detector (RNF-REP-02)
  marcada_por: string | null; // quien confirmo o descarto (H-037)
  marcada_en: string | null;
};

// POST /api/incidentes/{id}/estado (H-037). Solo esas dos transiciones son
// manuales. 409 si el incidente ya esta cerrado o descartado.
export type PeticionEstado = { estado: "confirmada" | "falso_positivo" };
// Lo que responde: el incidente como queda, con quien lo marco y cuando.
export type IncidenteMarcado = Incidente & { marcada_por: string; marcada_en: string };

// ---------------------------------------------------------- gobernanza (§3)

export type Hallazgo = {
  regla: string; // p. ej. "puerto-expuesto-no-esperado"
  severidad: Severidad;
  activos: string[];
  entornos: string[];
  detalle: string;
  detectado: string;
};

export type ReporteCostos = {
  moneda: string;
  periodo: { desde: string; hasta: string }; // fechas AAAA-MM-DD
  por_activo: { activo: string; proveedor: Proveedor; costo: number; fuente: string }[];
  sin_asignar: number;
};

// FUERA DEL CONTRATO: respuesta de POST /api/gobernanza/costos/facturas
export type ResumenImportacion = {
  huella: string;
  proveedor: string;
  nueva: boolean;
  lineas: number;
  asignadas: number;
  sin_asignar: number;
  total: number;
  lineas_sin_asignar: { concepto: string; producto: string; monto: number; motivo: string }[];
};

// --------------------------------------------------------- laboratorio (§3)

// GET /api/laboratorio/inyecciones: las filas de la tabla inyecciones (§4).
// La interfaz todavia no la usa; esta para que el simulador la sirva.
export type Inyeccion = {
  id: number;
  uid: string;
  tipo: "cpu" | "memoria" | "disco" | "caida" | "latencia";
  nodo: string;
  activo: string | null;
  inicio: string;
  fin: string | null;
  intensidad: string;
  parametros: Record<string, unknown>;
  estado: "en_curso" | "completada" | "fallida";
  causa_en: string | null;
  manifiesta_en: string | null;
};

// ------------------------------------------------------------ errores (§1)
export type ErrorRespuesta = { detail: string };
