import type { EstadoActivo, EstadoIncidente, Proveedor, Severidad } from "./contrato";

export const PROVEEDOR: Record<Proveedor, { nombre: string; sigla: string }> = {
  onprem: { nombre: "On-premise", sigla: "OP" },
  proxmox: { nombre: "On-premise", sigla: "OP" },
  aws: { nombre: "AWS", sigla: "AWS" },
  azure: { nombre: "Azure", sigla: "AZ" },
  digitalocean: { nombre: "DigitalOcean", sigla: "DO" },
};

// Cada estado lleva texto y simbolo ademas de color: se distingue sin color.
// degradado: mas de 5 min sin datos (H-021); atencion: incidentes abiertos.
export const ESTADO_ACTIVO: Record<EstadoActivo, { texto: string; plural: string; simbolo: string }> = {
  degradado: { texto: "Degradado", plural: "Degradados", simbolo: "✕" },
  atencion: { texto: "Requiere atención", plural: "Requieren atención", simbolo: "!" },
  sin_datos: { texto: "Sin datos", plural: "Sin datos", simbolo: "?" },
  ok: { texto: "Operativo", plural: "Operativos", simbolo: "✓" },
  retirado: { texto: "Retirado", plural: "Retirados", simbolo: "–" },
};
export const ORDEN_ESTADO_ACTIVO: EstadoActivo[] = ["degradado", "atencion", "sin_datos", "ok", "retirado"];
// los que alguien deberia mirar ahora
export const REQUIERE_ATENCION: EstadoActivo[] = ["degradado", "atencion", "sin_datos"];

export const ESTADO_INCIDENTE: Record<EstadoIncidente, { texto: string; simbolo: string; ayuda: string }> = {
  detectada: { texto: "Detectada", simbolo: "●", ayuda: "El detector la marcó y aún no hay diagnóstico" },
  diagnosticada: { texto: "Con hipótesis", simbolo: "◆", ayuda: "Hay una hipótesis de causa por revisar" },
  confirmada: { texto: "Confirmada", simbolo: "▲", ayuda: "Un operador confirmó que es real" },
  falso_positivo: { texto: "Falso positivo", simbolo: "○", ayuda: "Un operador la descartó" },
  cerrada: { texto: "Cerrada", simbolo: "✓", ayuda: "El detector la cerró porque la desviación cesó" },
};
export const ORDEN_ESTADO_INCIDENTE: EstadoIncidente[] = ["detectada", "diagnosticada", "confirmada", "falso_positivo", "cerrada"];
export const ABIERTO: EstadoIncidente[] = ["detectada", "diagnosticada", "confirmada"];

export const SEVERIDAD: Record<Severidad, { texto: string; orden: number }> = {
  alta: { texto: "Alta", orden: 0 },
  media: { texto: "Media", orden: 1 },
  baja: { texto: "Baja", orden: 2 },
};

export function hace(iso: string | null, ahora = Date.now()): string {
  if (!iso) return "nunca";
  const s = Math.round((ahora - new Date(iso).getTime()) / 1000);
  if (s < 0) return "ahora";
  if (s < 60) return `hace ${s} s`;
  if (s < 3600) return `hace ${Math.round(s / 60)} min`;
  if (s < 86400) return `hace ${Math.round(s / 3600)} h`;
  return `hace ${Math.round(s / 86400)} d`;
}

export function duracion(desde: string, hasta: string | null, ahora = Date.now()): string {
  const s = Math.max(0, Math.round(((hasta ? new Date(hasta).getTime() : ahora) - new Date(desde).getTime()) / 1000));
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.round(s / 60)} min`;
  const h = Math.floor(s / 3600);
  const m = Math.round((s % 3600) / 60);
  return m ? `${h} h ${m} min` : `${h} h`;
}

export const fechaValida = (iso: string) => !Number.isNaN(new Date(iso).getTime());

// Un texto que no es fecha se muestra tal cual en vez de "Invalid Date".
// Fechas cortas y en 24 h, como la cabecera: "29 oct, 13:01". El formato del
// navegador ("29 de oct, 01:01 p. m.") hacia ilegibles los rangos repetidos.
const MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
const dosDigitos = (n: number) => String(n).padStart(2, "0");
const diaCorto = (d: Date) => `${d.getDate()} ${MESES[d.getMonth()]}`;
const horaCorta = (d: Date) => `${dosDigitos(d.getHours())}:${dosDigitos(d.getMinutes())}`;

export const fechaHora = (iso: string) => {
  if (!fechaValida(iso)) return iso;
  const d = new Date(iso);
  return `${diaCorto(d)}, ${horaCorta(d)}`;
};

/** "29 oct, 12:23–13:01" si es el mismo dia; "28 oct, 23:10 – 29 oct, 01:05" si no. */
export function rangoFechas(desde: string, hasta: string): string {
  if (!fechaValida(desde) || !fechaValida(hasta)) return `${fechaHora(desde)} a ${fechaHora(hasta)}`;
  const a = new Date(desde);
  const b = new Date(hasta);
  return diaCorto(a) === diaCorto(b) && a.getFullYear() === b.getFullYear()
    ? `${diaCorto(a)}, ${horaCorta(a)}–${horaCorta(b)}`
    : `${fechaHora(desde)} – ${fechaHora(hasta)}`;
}

export const porcentaje = (v: number | null) => (v == null ? "sin dato" : `${Math.round(v * 100)} %`);

export const dinero = (monto: number, moneda = "USD") =>
  `${monto.toLocaleString("es-CO", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${moneda}`;

export const NOMBRE_METRICA: Record<string, string> = {
  system_cpu_utilization: "CPU",
  system_memory_utilization: "Memoria",
  system_filesystem_utilization: "Disco",
  system_network_io: "Red",
  http_server_request_duration: "Latencia",
  system_disk_io: "E/S de disco",
  system_paging_operations: "Paginación",
  system_filesystem_usage: "Uso del disco",
  system_paging_usage: "Uso de swap",
  system_cpu_load_average_1m: "Carga (1 min)",
  system_processes_count: "Procesos",
  traces_span_metrics_calls: "Llamadas",
};
export const nombreMetrica = (m: string) => NOMBRE_METRICA[m] ?? m;

// ------------------------------------------------------------- unidades
// Contadores que solo crecen (Sum monotonica): se leen como tasa por segundo.
// Es la misma lista que las senales ACUMULADA del detector (zenit/deteccion/senales.py).
export const ACUMULADAS = new Set(["system_disk_io", "system_network_io", "system_paging_operations", "traces_span_metrics_calls"]);
export type ClaseMetrica = "medida" | "acumulada";
export const claseDe = (metrica: string, clase?: string | null): ClaseMetrica =>
  clase === "acumulada" || clase === "medida" ? clase : ACUMULADAS.has(metrica) ? "acumulada" : "medida";

const EN_BYTES = new Set([
  "system_disk_io",
  "system_network_io",
  "system_filesystem_usage",
  "system_memory_usage",
  "system_paging_usage",
]);
const POR_SEGUNDO: Record<string, string> = {
  system_paging_operations: "op",
  system_disk_operations: "op",
  traces_span_metrics_calls: "llamadas",
};

const decimal = (v: number, d = 2) => v.toLocaleString("es-CO", { minimumFractionDigits: 0, maximumFractionDigits: d });

export function bytes(v: number): string {
  const abs = Math.abs(v);
  if (abs >= 1024 ** 3) return `${decimal(v / 1024 ** 3)} GB`;
  if (abs >= 1024 ** 2) return `${decimal(v / 1024 ** 2)} MB`;
  if (abs >= 1024) return `${decimal(v / 1024)} KB`;
  return `${decimal(v, 0)} B`;
}

/**
 * Un valor de una metrica con su unidad. Las *_utilization son fracciones y
 * van como porcentaje con 2 decimales; las de bytes en KB, MB o GB; las
 * acumuladas, que el detector compara como tasa, por segundo.
 */
export function formatoValor(metrica: string, valor: number | null | undefined, clase?: string | null): string {
  if (valor == null || Number.isNaN(valor)) return "sin dato";
  const tasa = claseDe(metrica, clase) === "acumulada";
  if (metrica.endsWith("_utilization")) return `${(valor * 100).toLocaleString("es-CO", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} %`;
  if (EN_BYTES.has(metrica)) return `${bytes(valor)}${tasa ? "/s" : ""}`;
  if (POR_SEGUNDO[metrica]) return `${decimal(valor)} ${POR_SEGUNDO[metrica]}${tasa ? "/s" : ""}`;
  return `${decimal(valor)}${tasa ? "/s" : ""}`;
}

export const tituloIncidente = (i: { metrica: string; dimensiones: Record<string, string> }) => {
  const dims = Object.values(i.dimensiones);
  return `${nombreMetrica(i.metrica)} fuera de lo normal${dims.length ? ` (${dims.join(", ")})` : ""}`;
};

// Un proveedor que la interfaz no conoce se muestra con su nombre, sin romper la pantalla.
export const datosProveedor = (p: string) => PROVEEDOR[p as Proveedor] ?? { nombre: p || "desconocido", sigla: (p || "?").slice(0, 3).toUpperCase() };
