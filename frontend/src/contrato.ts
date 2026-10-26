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
  | "diagnostico:consumo" // FUERA DEL CONTRATO (H-042)
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

// ------------------------------------------- registros y trazas (1.2, H-016/17)

// severidad_num sigue OpenTelemetry: 9 info, 13 aviso, 17 error, 21 fatal
export type Registro = {
  momento: string;
  service_name: string;
  host_name: string;
  severidad: string;
  severidad_num: number;
  cuerpo: string;
  traza_id: string; // "" si el registro no viene de una traza
  span_id: string;
  atributos: Record<string, string>;
};
export type RespuestaRegistros = { activo: string; desde: string; hasta: string; registros: Registro[] };

// GET /api/activos/{id}/trazas: una fila por traza que toco el activo
export type ResumenTraza = {
  traza_id: string;
  inicio: string;
  fin: string;
  duracion_ns: number;
  spans: number;
  errores: number;
  raiz: string; // nombre del span raiz; "" si no cayo en la ventana
  servicios: string[];
};
export type RespuestaTrazas = { activo: string; desde: string; hasta: string; trazas: ResumenTraza[] };

export type Span = {
  inicio: string;
  fin: string;
  duracion_ns: number;
  span_id: string;
  padre_id: string; // "" en la raiz
  nombre: string;
  tipo: string;
  estado: string; // "error" | "ok" | "unset"
  estado_mensaje: string;
  zenit_asset_id: string;
  service_name: string;
  host_name: string;
  atributos: Record<string, string>;
};
export type RespuestaTraza = { traza_id: string; spans: Span[] };

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
  // la escribe el detector: "acumulada" si los valores son una tasa por segundo de un contador
  clase?: "medida" | "acumulada";
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

// GET /api/incidentes/{id}/evidencia (H-039): las tres senales en la ventana del incidente.
export type ResumenCambio = {
  antes_mediana: number | null;
  durante_mediana: number | null;
  durante_min: number | null;
  durante_max: number | null;
  puntos_antes?: number;
  puntos_durante?: number;
  cambio_absoluto: number | null; // maximo durante menos la mediana de antes
  cambio_relativo: number | null; // fraccion; null si la base es cero
  cambio_relativo_mediana?: number | null;
};
export type MetricaEvidencia = {
  metrica: string;
  dimensiones: Record<string, string>;
  papel: "afectada" | "relacionada";
  clase: "medida" | "acumulada" | null;
  unidad: string | null; // "por segundo" en las acumuladas
  resolucion_s?: number;
  resumen: ResumenCambio;
  puntos?: [string, number][]; // solo la afectada
};
export type ConteosEvidencia = { registros: number; registros_error: number; spans: number; spans_error: number };
export type CorrelacionEvidencia = {
  momento: string;
  servicio: string;
  severidad: string;
  cuerpo: string;
  traza_id: string;
  operacion: string | null;
  duracion_ns: number | null;
  estado_span: string | null;
};
export type EvidenciaIncidente = {
  incidente: number;
  activo: string;
  metrica: string;
  dimensiones: Record<string, string>;
  recuperada: string;
  ventana: { desde: string; hasta: string };
  ventana_registros: { desde: string; hasta: string };
  metricas: MetricaEvidencia[]; // la afectada primero, luego las que mas cambiaron
  metricas_relacionadas: string[];
  registros_error: Registro[];
  trazas_error: ResumenTraza[];
  correlacion: CorrelacionEvidencia[];
  conteos: ConteosEvidencia | null; // null si no se pudo consultar el almacen analitico
  faltantes: string[]; // lo que no se pudo recuperar, en texto
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

// FUERA DEL CONTRATO (H-047): GET /api/gobernanza/costos/analisis?desde&hasta
export type CriterioSubutilizado = {
  cpu_promedio_max: number; // fracciones 0..1
  cpu_pico_max: number;
  memoria_promedio_max: number;
  horas_minimas: number;
};
export type UsoActivo = {
  activo: string;
  entorno: string;
  proveedor: string | null;
  costo: number;
  horas_medidas: number;
  cpu_promedio: number | null;
  cpu_pico: number | null;
  memoria_promedio: number | null;
  subutilizado: boolean | null; // null: sin datos suficientes para decirlo
  motivo: string;
};
export type AnalisisCostos = {
  moneda: string;
  periodo: { desde: string; hasta: string };
  criterio: CriterioSubutilizado;
  activos: UsoActivo[];
  tendencia: {
    meses: string[]; // AAAA-MM
    por_entorno: { entorno: string; montos: number[] }[];
    por_activo: { activo: string; entorno: string; montos: number[] }[];
  };
  uso: { desde: string; hasta: string; disponible: boolean };
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

// ------------------------------------------------- fuentes (1.2, H-021, H-011)

// degradado: mas de 5 min sin recibir metricas; sin_datos: nunca reporto
export type EstadoFuente = "ok" | "degradado" | "sin_datos";
export type Senal = "metricas" | "registros" | "trazas";

export type SenalFuente = {
  ultima_recepcion: string;
  estado: EstadoFuente;
  recibidas_total: number;
  errores_total: number;
  recibidas_5min: number;
  errores_1h: number;
};

// GET /api/fuentes: una por activo y host.name, las degradadas primero
export type Fuente = {
  activo: string; // "sin_resolver" para las huerfanas
  host_name: string;
  proveedor: string | null;
  huerfana: boolean;
  senales: Partial<Record<Senal, SenalFuente>>;
  ultima_recepcion: string | null;
  estado: EstadoFuente;
  minutos_sin_datos: number | null;
};

// GET /api/fuentes/huerfanas: origenes que no resolvieron contra el catalogo
export type Huerfana = {
  clave: string; // valor de host.name u otra clave nativa
  tipo_clave: string;
  proveedor: string | null;
  metricas: number; // acumulados desde `primera`
  registros: number;
  trazas: number;
  primera: string;
  ultima: string;
  asociada_a: string | null; // la telemetria previa ya se reasigno a ese activo
  asociada_en: string | null;
  resuelve_a: string | null; // la clave ya esta en el catalogo: asociable
};

export type EjecucionDescubrimiento = {
  id: number;
  proveedor: string;
  inicio: string;
  fin: string | null;
  estado: "en_curso" | "ok" | "error";
  vistos: number;
  nuevos: number;
  retirados: number;
  reactivados: number;
  error: string | null;
};

// GET /api/catalogo/descubrimientos
export type Descubrimientos = { ultimas: EjecucionDescubrimiento[]; recientes: EjecucionDescubrimiento[] };

// FUERA DEL CONTRATO (H-042): GET /api/diagnostico/consumo?desde&hasta
export type ConsumoDiagnostico = {
  periodo: { desde: string; hasta: string };
  modelo_configurado: string | null; // ZENIT_MODELO_DIAGNOSTICO; null si falta
  por_dia: { dia: string; modelo: string; diagnosticos: number; tokens_entrada: number; tokens_salida: number }[];
  por_modelo: { modelo: string; diagnosticos: number; tokens_entrada: number; tokens_salida: number }[];
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
