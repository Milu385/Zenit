// Cliente de la API de Zenit. Toda llamada pasa por aqui.

export type Activo = { id: string; claves: string[] };

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
};

export class ErrorApi extends Error {
  constructor(mensaje: string, public estado: number) {
    super(mensaje);
  }
}

async function pedir<T>(ruta: string, senal?: AbortSignal): Promise<T> {
  let r: Response;
  try {
    r = await fetch(ruta, { signal: senal, headers: { Accept: "application/json" } });
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
    throw new ErrorApi(detalle, r.status);
  }
  return r.json() as Promise<T>;
}

const c = encodeURIComponent;

export const api = {
  activos: (s?: AbortSignal) => pedir<Activo[]>("/api/activos", s),

  metricas: (activo: string, s?: AbortSignal) =>
    pedir<{ metricas: string[] }>(`/api/activos/${c(activo)}/metricas`, s).then((r) => r.metricas),

  dimensiones: (activo: string, metrica: string, desde: Date, hasta: Date, s?: AbortSignal) =>
    pedir<{ dimensiones: Record<string, string[]> }>(
      `/api/activos/${c(activo)}/metricas/${c(metrica)}/dimensiones?desde=${c(desde.toISOString())}&hasta=${c(hasta.toISOString())}`,
      s,
    ).then((r) => r.dimensiones),

  serie: (activo: string, metrica: string, desde: Date, hasta: Date, agrupar: string, s?: AbortSignal) => {
    const q = new URLSearchParams({ desde: desde.toISOString(), hasta: hasta.toISOString() });
    if (agrupar) q.set("agrupar", agrupar);
    return pedir<RespuestaSerie>(`/api/activos/${c(activo)}/series/${c(metrica)}?${q}`, s);
  },
};
