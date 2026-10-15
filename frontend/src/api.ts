// Cliente de la API de Zenit. Toda llamada pasa por aqui y solo usa las
// formas de contrato.ts.
import type {
  Activo,
  DetalleActivo,
  DetalleIncidente,
  Diagnostico,
  Hallazgo,
  Incidente,
  PeticionEstado,
  PeticionSesion,
  ReporteCostos,
  RespuestaDimensiones,
  RespuestaMetricas,
  RespuestaSerie,
  RespuestaSesion,
  ResumenImportacion,
  Yo,
} from "./contrato";

export type { Activo, RespuestaSerie, Serie } from "./contrato";

export class ErrorApi extends Error {
  constructor(mensaje: string, public estado: number) {
    super(mensaje);
  }
}

// La sesion vencio en medio del uso: quien escuche (sesion.tsx) vuelve al login.
export const SESION_VENCIDA = "zenit:sesion-vencida";

// ---- token (contrato §1: Authorization: Bearer)
// Vive en sessionStorage, que se borra al cerrar la pestana; con "mantener la
// sesion" va a localStorage. Ninguna de las dos lo protege de un XSS, por eso
// la interfaz nunca inserta HTML que venga de la API.
const CLAVE_TOKEN = "zenit.token";
let token: string | null = leerToken();

function leerToken(): string | null {
  try {
    return sessionStorage.getItem(CLAVE_TOKEN) ?? localStorage.getItem(CLAVE_TOKEN);
  } catch {
    return null;
  }
}

export function guardarToken(nuevo: string | null, recordar = false) {
  token = nuevo;
  try {
    sessionStorage.removeItem(CLAVE_TOKEN);
    localStorage.removeItem(CLAVE_TOKEN);
    if (nuevo) (recordar ? localStorage : sessionStorage).setItem(CLAVE_TOKEN, nuevo);
  } catch {
    /* sin almacenamiento: la sesion dura lo que la pestana */
  }
}

export const hayToken = () => token !== null;

type Opciones = { senal?: AbortSignal; metodo?: string; cuerpo?: BodyInit; tipo?: string };

async function pedir<T>(ruta: string, o: Opciones = {}): Promise<T> {
  const cabeceras: Record<string, string> = { Accept: "application/json" };
  if (o.tipo) cabeceras["Content-Type"] = o.tipo;
  if (token) cabeceras.Authorization = `Bearer ${token}`;
  let r: Response;
  try {
    r = await fetch(ruta, { method: o.metodo ?? "GET", signal: o.senal, body: o.cuerpo, headers: cabeceras });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ErrorApi("No hay conexion con la API de Zenit.", 0);
  }
  if (!r.ok) {
    let detalle = `La API respondio ${r.status}.`;
    try {
      const cuerpo = await r.json();
      if (typeof cuerpo.detail === "string") detalle = cuerpo.detail;
    } catch {
      /* cuerpo no JSON: se queda el mensaje generico */
    }
    if (r.status === 401 && ruta !== "/api/sesion") {
      guardarToken(null);
      window.dispatchEvent(new Event(SESION_VENCIDA));
    }
    throw new ErrorApi(detalle, r.status);
  }
  if (r.status === 202 || r.status === 204) return undefined as T;
  return r.json() as Promise<T>;
}

const c = encodeURIComponent;
const json = (datos: unknown) => ({ cuerpo: JSON.stringify(datos), tipo: "application/json" });

export const api = {
  // sesion
  iniciarSesion: (p: PeticionSesion) => pedir<RespuestaSesion>("/api/sesion", { metodo: "POST", ...json(p) }),
  yo: (s?: AbortSignal) => pedir<Yo>("/api/yo", { senal: s }),

  // activos y series
  activos: (s?: AbortSignal) => pedir<Activo[]>("/api/activos", { senal: s }),
  activo: (id: string, s?: AbortSignal) => pedir<DetalleActivo>(`/api/activos/${c(id)}`, { senal: s }),

  metricas: (activo: string, s?: AbortSignal) =>
    pedir<RespuestaMetricas>(`/api/activos/${c(activo)}/metricas`, { senal: s }).then((r) => r.metricas),

  dimensiones: (activo: string, metrica: string, desde: Date, hasta: Date, s?: AbortSignal) =>
    pedir<RespuestaDimensiones>(
      `/api/activos/${c(activo)}/metricas/${c(metrica)}/dimensiones?desde=${c(desde.toISOString())}&hasta=${c(hasta.toISOString())}`,
      { senal: s },
    ).then((r) => r.dimensiones),

  serie: (activo: string, metrica: string, desde: Date, hasta: Date, agrupar: string, s?: AbortSignal) => {
    const q = new URLSearchParams({ desde: desde.toISOString(), hasta: hasta.toISOString() });
    if (agrupar) q.set("agrupar", agrupar);
    return pedir<RespuestaSerie>(`/api/activos/${c(activo)}/series/${c(metrica)}?${q}`, { senal: s });
  },

  // incidentes
  incidentes: (filtro: { activo?: string } = {}, s?: AbortSignal) => {
    const q = new URLSearchParams();
    if (filtro.activo) q.set("activo", filtro.activo);
    return pedir<Incidente[]>(`/api/incidentes${q.toString() ? `?${q}` : ""}`, { senal: s });
  },
  incidente: (id: number, s?: AbortSignal) => pedir<DetalleIncidente>(`/api/incidentes/${id}`, { senal: s }),
  // el contrato no fija el cuerpo de la respuesta: la interfaz no depende de el
  cambiarEstado: (id: number, estado: PeticionEstado["estado"]) =>
    pedir<unknown>(`/api/incidentes/${id}/estado`, { metodo: "POST", ...json({ estado } satisfies PeticionEstado) }),
  // 200 con el diagnostico si fue inmediato; 202 (undefined) si quedo en cola
  pedirDiagnostico: (id: number) => pedir<Diagnostico | undefined>(`/api/incidentes/${id}/diagnostico`, { metodo: "POST" }),

  // gobernanza
  configuraciones: (s?: AbortSignal) => pedir<Hallazgo[]>("/api/gobernanza/configuraciones", { senal: s }),
  costos: (desde: string, hasta: string, s?: AbortSignal) => {
    const q = new URLSearchParams();
    if (desde) q.set("desde", desde);
    if (hasta) q.set("hasta", hasta);
    return pedir<ReporteCostos>(`/api/gobernanza/costos${q.toString() ? `?${q}` : ""}`, { senal: s });
  },
  // FUERA DEL CONTRATO (NOTAS.md)
  importarFactura: (proveedor: string, archivo: File) =>
    pedir<ResumenImportacion>(`/api/gobernanza/costos/facturas?proveedor=${c(proveedor)}`, {
      metodo: "POST",
      cuerpo: archivo,
      tipo: "text/csv",
    }),
};
