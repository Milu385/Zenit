import type { EstadoActivo, EstadoIncidente, Proveedor, Severidad } from "./contrato";

export const PROVEEDOR: Record<Proveedor, { nombre: string; sigla: string }> = {
  onprem: { nombre: "On-premise", sigla: "OP" },
  aws: { nombre: "AWS", sigla: "AWS" },
  azure: { nombre: "Azure", sigla: "AZ" },
  digitalocean: { nombre: "DigitalOcean", sigla: "DO" },
};

// Cada estado lleva texto y simbolo ademas de color: se distingue sin color.
// degradado: mas de 5 min sin datos (H-021); atencion: incidentes abiertos.
export const ESTADO_ACTIVO: Record<EstadoActivo, { texto: string; plural: string; simbolo: string }> = {
  degradado: { texto: "Degradado", plural: "Degradados", simbolo: "✕" },
  atencion: { texto: "Requiere atencion", plural: "Requieren atencion", simbolo: "!" },
  sin_datos: { texto: "Sin datos", plural: "Sin datos", simbolo: "?" },
  ok: { texto: "Operativo", plural: "Operativos", simbolo: "✓" },
  retirado: { texto: "Retirado", plural: "Retirados", simbolo: "–" },
};
export const ORDEN_ESTADO_ACTIVO: EstadoActivo[] = ["degradado", "atencion", "sin_datos", "ok", "retirado"];
// los que alguien deberia mirar ahora
export const REQUIERE_ATENCION: EstadoActivo[] = ["degradado", "atencion", "sin_datos"];

export const ESTADO_INCIDENTE: Record<EstadoIncidente, { texto: string; simbolo: string; ayuda: string }> = {
  detectada: { texto: "Detectada", simbolo: "●", ayuda: "El detector la marco y aun no hay diagnostico" },
  diagnosticada: { texto: "Con hipotesis", simbolo: "◆", ayuda: "Hay una hipotesis de causa por revisar" },
  confirmada: { texto: "Confirmada", simbolo: "▲", ayuda: "Un operador confirmo que es real" },
  falso_positivo: { texto: "Falso positivo", simbolo: "○", ayuda: "Un operador la descarto" },
  cerrada: { texto: "Cerrada", simbolo: "✓", ayuda: "El detector la cerro porque la desviacion ceso" },
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

export const fechaHora = (iso: string) =>
  new Date(iso).toLocaleString("es-CO", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

export const porcentaje = (v: number | null) => (v == null ? "sin dato" : `${Math.round(v * 100)} %`);

export const dinero = (monto: number, moneda = "USD") =>
  `${monto.toLocaleString("es-CO", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${moneda}`;

export const NOMBRE_METRICA: Record<string, string> = {
  system_cpu_utilization: "CPU",
  system_memory_utilization: "Memoria",
  system_filesystem_utilization: "Disco",
  system_network_io: "Red",
  http_server_request_duration: "Latencia",
};
export const nombreMetrica = (m: string) => NOMBRE_METRICA[m] ?? m;

export const tituloIncidente = (i: { metrica: string; dimensiones: Record<string, string> }) => {
  const dims = Object.values(i.dimensiones);
  return `${nombreMetrica(i.metrica)} fuera de lo normal${dims.length ? ` (${dims.join(", ")})` : ""}`;
};
