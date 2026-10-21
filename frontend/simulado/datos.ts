// Datos simulados. Todo lleva el tipo de src/contrato.ts: si el contrato
// cambia, `npm run build` falla aqui y no en la integracion.
import type {
  Activo,
  Descubrimientos,
  DetalleActivo,
  DetalleIncidente,
  Diagnostico,
  EstadoActivo,
  Evidencia,
  Fuente,
  Hallazgo,
  Huerfana,
  Incidente,
  Inyeccion,
  ReporteCostos,
  RespuestaSerie,
  Registro,
  ResumenTraza,
  Rol,
  Span,
} from "../src/contrato";

const MIN = 60_000;
const hace = (ms: number) => new Date(Date.now() - ms).toISOString();

// Usuarios de prueba. La clave es la misma para todos y solo existe aqui.
export const CLAVE_SIMULADA = "zenit";
export const USUARIOS: Record<string, Rol> = {
  daniel: "administrador",
  lucia: "operador",
  juanjose: "finanzas",
};

// ---------------------------------------------------------------- activos

type Base = Omit<Activo, "estado" | "ultima_recepcion" | "incidentes_abiertos"> & {
  claves: string[];
  retraso_ms: number | null; // null: nunca reporto
  retirado?: boolean;
  memoria: number;
};

const BASE: Base[] = [
  {
    id: "activo-azure-nodo-01",
    claves: ["zenit-nodo-azure"],
    nombre: "zenit-nodo-azure",
    proveedor: "azure",
    region: "westeurope",
    entorno: "azure",
    cpu_reciente: null,
    memoria: 0.5,
    retraso_ms: 7 * MIN,
  },
  {
    id: "activo-aws-nodo-01",
    claves: ["zenit-nodo-aws", "i-0abc123"],
    nombre: "zenit-nodo-aws",
    proveedor: "aws",
    region: "us-east-1",
    entorno: "aws",
    cpu_reciente: 0.93,
    memoria: 0.61,
    retraso_ms: 8_000,
  },
  {
    id: "activo-onprem-db-01",
    claves: ["srv-db-01"],
    nombre: "srv-db-01",
    proveedor: "onprem",
    region: "Medellin DC1",
    entorno: "onprem",
    cpu_reciente: 0.41,
    memoria: 0.88,
    retraso_ms: 11_000,
  },
  {
    id: "activo-azure-web-02",
    claves: ["azure-web-02"],
    nombre: "azure-web-02",
    proveedor: "azure",
    region: "westeurope",
    entorno: "azure",
    cpu_reciente: null,
    memoria: 0.5,
    retraso_ms: null,
  },
  {
    id: "activo-onprem-01",
    claves: ["zenit-nodo-onprem", "srv-core-01"],
    nombre: "srv-core-01",
    proveedor: "onprem",
    region: "Medellin DC1",
    entorno: "onprem",
    cpu_reciente: 0.34,
    memoria: 0.64,
    retraso_ms: 9_000,
  },
  {
    id: "activo-onprem-02",
    claves: ["srv-core-02"],
    nombre: "srv-core-02",
    proveedor: "onprem",
    region: "Medellin DC1",
    entorno: "onprem",
    cpu_reciente: 0.22,
    memoria: 0.48,
    retraso_ms: 7_000,
  },
  {
    id: "activo-do-nodo-01",
    claves: ["zenit-nodo-do"],
    nombre: "zenit-nodo-do",
    proveedor: "digitalocean",
    region: "nyc3",
    entorno: "digitalocean",
    cpu_reciente: 0.27,
    memoria: 0.52,
    retraso_ms: 10_000,
  },
  {
    id: "activo-do-plataforma",
    claves: ["zenit-plataforma"],
    nombre: "zenit-plataforma",
    proveedor: "digitalocean",
    region: "nyc3",
    entorno: "plataforma",
    cpu_reciente: 0.46,
    memoria: 0.58,
    retraso_ms: 4_000,
  },
  {
    id: "activo-aws-api-03",
    claves: ["api-03"],
    nombre: "api-03",
    proveedor: "aws",
    region: "us-east-1",
    entorno: "aws",
    cpu_reciente: 0.19,
    memoria: 0.37,
    retraso_ms: 12_000,
  },
  {
    id: "activo-aws-legado-01",
    claves: ["legado-01"],
    nombre: "legado-01",
    proveedor: "aws",
    region: "us-east-1",
    entorno: "aws",
    cpu_reciente: null,
    memoria: 0,
    retraso_ms: 9 * 24 * 60 * MIN,
    retirado: true,
  },
];

// --------------------------------------------------------- registros y trazas

// Una traza cada 2 min por activo, pedidos -> catalogo; una de cada cinco con
// error en catalogo. Determinista para que la pagina no cambie en cada refresco.
const PASO_TRAZA = 2 * MIN;
const hex = (n: number, largo: number) => (n.toString(16).padStart(largo, "0") + "0".repeat(largo)).slice(0, largo);
const idTraza = (activo: string, t: number) => hex(Math.abs([...activo].reduce((h, c) => (h * 31 + c.charCodeAt(0)) | 0, 7)), 8) + hex(t / PASO_TRAZA, 24);
const conError = (t: number) => (t / PASO_TRAZA) % 5 === 0;

function momentosTraza(desde: Date, hasta: Date): number[] {
  const salida: number[] = [];
  for (let t = Math.floor(hasta.getTime() / PASO_TRAZA) * PASO_TRAZA; t >= desde.getTime() && salida.length < 720; t -= PASO_TRAZA) salida.push(t);
  return salida;
}

function spansDe(activo: string, t: number): Span[] {
  const mal = conError(t);
  const base = { zenit_asset_id: activo, host_name: activo.replace("activo-", ""), atributos: {}, estado_mensaje: "" };
  const span = (i: number, padre: string, nombre: string, servicio: string, desdeMs: number, durMs: number, error = false): Span => ({
    ...base,
    span_id: hex(i + 1, 16),
    padre_id: padre,
    nombre,
    tipo: padre ? "client" : "server",
    service_name: servicio,
    inicio: new Date(t + desdeMs).toISOString(),
    fin: new Date(t + desdeMs + durMs).toISOString(),
    duracion_ns: durMs * 1e6,
    estado: error ? "error" : "ok",
    estado_mensaje: error ? "timeout al consultar el inventario" : "",
  });
  return [
    span(0, "", "POST /pedidos", "pedidos", 0, mal ? 2_140 : 184),
    span(1, hex(1, 16), "GET /catalogo/productos", "pedidos", 12, mal ? 2_100 : 95, mal),
    span(2, hex(2, 16), "SELECT productos", "catalogo", 20, mal ? 2_060 : 61, mal),
    span(3, hex(1, 16), "INSERT pedido", "pedidos", mal ? 2_120 : 120, 14),
  ];
}

export function trazasDe(activo: string, desde: Date, hasta: Date, soloErrores: boolean): ResumenTraza[] {
  return momentosTraza(desde, hasta)
    .filter((t) => !soloErrores || conError(t))
    .slice(0, 100)
    .map((t) => {
      const spans = spansDe(activo, t);
      return {
        traza_id: idTraza(activo, t),
        inicio: spans[0].inicio,
        fin: spans.map((s) => s.fin).sort().pop()!,
        duracion_ns: spans[0].duracion_ns,
        spans: spans.length,
        errores: spans.filter((s) => s.estado === "error").length,
        raiz: spans[0].nombre,
        servicios: ["catalogo", "pedidos"],
      };
    });
}

export function traza(id: string): Span[] {
  for (const b of BASE)
    for (const t of momentosTraza(new Date(Date.now() - 24 * 60 * MIN), new Date())) if (idTraza(b.id, t) === id) return spansDe(b.id, t);
  return [];
}

export function registrosDe(activo: string, desde: Date, hasta: Date, severidadMinima: number, texto: string | null): Registro[] {
  const salida: Registro[] = [];
  for (const t of momentosTraza(desde, hasta)) {
    const mal = conError(t);
    const id = idTraza(activo, t);
    const comun = { host_name: activo.replace("activo-", ""), atributos: {} };
    salida.push({ ...comun, momento: new Date(t + 190).toISOString(), service_name: "pedidos", severidad: "INFO", severidad_num: 9, cuerpo: `pedido creado en ${mal ? 2140 : 184} ms`, traza_id: id, span_id: hex(1, 16) });
    if (mal)
      salida.push({ ...comun, momento: new Date(t + 2_105).toISOString(), service_name: "catalogo", severidad: "ERROR", severidad_num: 17, cuerpo: "consulta de productos supero 2 s: timeout\n  en catalogo/consultas.py:88", traza_id: id, span_id: hex(3, 16) });
    if ((t / PASO_TRAZA) % 7 === 0)
      salida.push({ ...comun, momento: new Date(t + 30_000).toISOString(), service_name: "", severidad: "WARN", severidad_num: 13, cuerpo: "systemd: disco /var al 81 % <b>no es html</b>", traza_id: "", span_id: "" });
  }
  return salida
    .filter((r) => r.severidad_num >= severidadMinima && (!texto || r.cuerpo.toLowerCase().includes(texto.toLowerCase())))
    .sort((a, b) => b.momento.localeCompare(a.momento))
    .slice(0, 200);
}

// ------------------------------------------------------------- incidentes

type IncidenteBase = Omit<Incidente, "inicio" | "fin" | "tiene_diagnostico"> & {
  inicio_ms: number;
  fin_ms: number | null;
  evidencia: (inicio: string, fin: string) => Evidencia;
  diagnostico: ((inicio: string, fin: string) => Diagnostico) | null;
};

const metodo = "mediana y desviacion absoluta mediana, ventana de 60 min";

const INCIDENTES_BASE: IncidenteBase[] = [
  {
    id: 41,
    activo: "activo-aws-nodo-01",
    metrica: "system_cpu_utilization",
    dimensiones: { state: "user" },
    inicio_ms: 38 * MIN,
    fin_ms: null,
    estado: "diagnosticada",
    severidad: "alta",
    puntaje: 6.8,
    evidencia: (inicio, fin) => ({
      ventana: { desde: inicio, hasta: fin },
      valor_observado: 0.71,
      valor_esperado: 0.14,
      metodo,
      metricas_relacionadas: ["system_memory_utilization", "system_network_io"],
    }),
    diagnostico: (inicio, fin) => ({
      texto:
        "Hipotesis: aumento de carga entrante. La CPU de zenit-nodo-aws subio de forma sostenida al mismo tiempo que el trafico de red entrante, y la memoria no cambio, asi que una fuga parece menos probable. Conviene revisar si hubo un pico de peticiones o un cliente reintentando.",
      evidencia_citada: [
        {
          metrica: "system_cpu_utilization",
          desde: inicio,
          hasta: fin,
          observacion: "CPU sostenida sobre 70 % desde el inicio, contra 14 % esperado.",
        },
        {
          metrica: "system_network_io",
          desde: inicio,
          hasta: fin,
          observacion: "Trafico entrante 41 % sobre su linea base en la misma ventana.",
        },
        {
          metrica: "system_memory_utilization",
          desde: inicio,
          hasta: fin,
          observacion: "Memoria estable. <script>alert('esto debe verse como texto')</script>",
        },
      ],
      modelo: "claude-simulado",
      tokens_entrada: 1830,
      tokens_salida: 240,
      generado: hace(30 * MIN),
    }),
  },
  {
    id: 40,
    activo: "activo-azure-nodo-01",
    metrica: "system_network_io",
    dimensiones: { direction: "receive" },
    inicio_ms: 9 * MIN,
    fin_ms: null,
    estado: "detectada",
    severidad: "alta",
    puntaje: 9.1,
    evidencia: (inicio, fin) => ({
      ventana: { desde: inicio, hasta: fin },
      valor_observado: 0,
      valor_esperado: 812_000,
      metodo,
      metricas_relacionadas: ["system_cpu_utilization"],
    }),
    diagnostico: null,
  },
  {
    id: 39,
    activo: "activo-onprem-db-01",
    metrica: "system_memory_utilization",
    dimensiones: { state: "used" },
    inicio_ms: 3 * 60 * MIN,
    fin_ms: null,
    estado: "confirmada",
    severidad: "media",
    puntaje: 4.2,
    evidencia: (inicio, fin) => ({
      ventana: { desde: inicio, hasta: fin },
      valor_observado: 0.88,
      valor_esperado: 0.62,
      metodo,
      metricas_relacionadas: [],
    }),
    diagnostico: (inicio, fin) => ({
      texto:
        "Hipotesis: la cache del motor crece sin limite. La memoria sube en escalones y no baja despues de cada consulta pesada. No hay evidencia de reinicios.",
      evidencia_citada: [
        { metrica: "system_memory_utilization", desde: inicio, hasta: fin, observacion: "Escalones de 3 a 5 % cada hora, sin bajadas." },
      ],
      modelo: "claude-simulado",
      tokens_entrada: 1210,
      tokens_salida: 160,
      generado: hace(2 * 60 * MIN),
    }),
  },
  {
    id: 37,
    activo: "activo-do-nodo-01",
    metrica: "system_cpu_utilization",
    dimensiones: { state: "system" },
    inicio_ms: 9 * 60 * MIN,
    fin_ms: 8.5 * 60 * MIN,
    estado: "falso_positivo",
    severidad: "baja",
    puntaje: 3.1,
    evidencia: (inicio, fin) => ({
      ventana: { desde: inicio, hasta: fin },
      valor_observado: 0.31,
      valor_esperado: 0.18,
      metodo,
      metricas_relacionadas: [],
    }),
    diagnostico: null,
  },
  {
    id: 35,
    activo: "activo-onprem-01",
    metrica: "system_filesystem_utilization",
    dimensiones: { mountpoint: "/" },
    inicio_ms: 26 * 60 * MIN,
    fin_ms: 25 * 60 * MIN,
    estado: "cerrada",
    severidad: "alta",
    puntaje: 5.5,
    evidencia: (inicio, fin) => ({
      ventana: { desde: inicio, hasta: fin },
      valor_observado: 0.97,
      valor_esperado: 0.71,
      metodo,
      metricas_relacionadas: [],
    }),
    diagnostico: null,
  },
];

// cambios hechos durante la sesion del simulador
export const estados = new Map<number, Incidente["estado"]>();
// diagnosticos pedidos: listos a partir de `listo`
export const pedidos = new Map<number, { listo: number }>();

const SEVERIDAD = { alta: 0, media: 1, baja: 2 };
const ventanaDe = (b: IncidenteBase) => ({ inicio: hace(b.inicio_ms), fin: b.fin_ms == null ? new Date().toISOString() : hace(b.fin_ms) });

function diagnosticoDe(b: IncidenteBase): Diagnostico | null {
  const { inicio, fin } = ventanaDe(b);
  const p = pedidos.get(b.id);
  if (p && p.listo <= Date.now()) {
    const base = b.diagnostico?.(inicio, fin);
    return {
      texto:
        base?.texto ??
        "Hipotesis: el nodo dejo de enviar datos de red, no de recibir trafico. El valor observado es cero y la CPU tambien dejo de llegar en la misma ventana, lo que apunta a un corte del agente o del nodo mas que a una caida del trafico.",
      evidencia_citada: base?.evidencia_citada ?? [
        { metrica: b.metrica, desde: inicio, hasta: fin, observacion: "Cero bytes recibidos desde el inicio." },
        { metrica: "system_cpu_utilization", desde: inicio, hasta: fin, observacion: "Sin puntos de CPU en la misma ventana." },
      ],
      modelo: "claude-simulado",
      tokens_entrada: 1500,
      tokens_salida: 200,
      generado: new Date(p.listo).toISOString(),
    };
  }
  return b.diagnostico?.(inicio, fin) ?? null;
}

function estadoDe(b: IncidenteBase, diag: Diagnostico | null): Incidente["estado"] {
  const e = estados.get(b.id) ?? b.estado;
  return e === "detectada" && diag ? "diagnosticada" : e;
}

function resumen(b: IncidenteBase): Incidente {
  const { evidencia: _e, diagnostico: _d, inicio_ms, fin_ms, ...i } = b;
  const diag = diagnosticoDe(b);
  return {
    ...i,
    inicio: hace(inicio_ms),
    fin: fin_ms == null ? null : hace(fin_ms),
    estado: estadoDe(b, diag),
    tiene_diagnostico: diag != null,
  };
}

// ordenada por severidad y luego por inicio, mas reciente primero (contrato)
export function incidentes(filtro: { estado?: string | null; activo?: string | null } = {}): Incidente[] {
  return INCIDENTES_BASE.map(resumen)
    .filter((i) => (!filtro.estado || i.estado === filtro.estado) && (!filtro.activo || i.activo === filtro.activo))
    .sort((a, b) => SEVERIDAD[a.severidad] - SEVERIDAD[b.severidad] || b.inicio.localeCompare(a.inicio));
}

export const existeIncidente = (id: number) => INCIDENTES_BASE.some((b) => b.id === id);
export const tieneDiagnostico = (id: number) => {
  const b = INCIDENTES_BASE.find((x) => x.id === id);
  return !!b && diagnosticoDe(b) != null;
};

export function detalle(id: number): DetalleIncidente | null {
  const b = INCIDENTES_BASE.find((x) => x.id === id);
  if (!b) return null;
  const { tiene_diagnostico: _t, ...i } = resumen(b);
  const { inicio, fin } = ventanaDe(b);
  return { ...i, evidencia: b.evidencia(inicio, fin), diagnostico: diagnosticoDe(b) };
}

// ---------------------------------------------------------------- activos

function estadoActivo(b: Base, abiertos: number): EstadoActivo {
  if (b.retirado) return "retirado";
  if (b.retraso_ms == null) return "sin_datos";
  if (b.retraso_ms > 5 * MIN) return "degradado";
  return abiertos > 0 ? "atencion" : "ok";
}

export function activosDetalle(): DetalleActivo[] {
  const abiertos = incidentes().filter((i) => ["detectada", "diagnosticada", "confirmada"].includes(i.estado));
  return BASE.map(({ retraso_ms, retirado: _r, memoria: _m, ...a }) => {
    const n = abiertos.filter((i) => i.activo === a.id).length;
    const b = BASE.find((x) => x.id === a.id)!;
    return { ...a, estado: estadoActivo(b, n), ultima_recepcion: retraso_ms == null ? null : hace(retraso_ms), incidentes_abiertos: n };
  });
}

export const activos = (): Activo[] => activosDetalle().map(({ claves: _c, ...a }) => a);

// ---------------------------------------------------------------- series

export const METRICAS = ["system_cpu_utilization", "system_memory_utilization", "system_network_io", "system_filesystem_utilization"];
export const DIMENSIONES: Record<string, Record<string, string[]>> = {
  system_cpu_utilization: { state: ["user", "system", "idle"], cpu: ["cpu0", "cpu1"] },
  system_memory_utilization: { state: ["used", "cached"] },
  system_network_io: { direction: ["receive", "transmit"], device: ["eth0"] },
  system_filesystem_utilization: { mountpoint: ["/"] },
};

function semilla(texto: string) {
  let h = 2166136261;
  for (const c of texto) h = Math.imul(h ^ c.charCodeAt(0), 16777619);
  return (h >>> 0) / 2 ** 32;
}

export function serie(activo: string, metrica: string, desde: Date, hasta: Date, agrupar: string | null): RespuestaSerie {
  const rango = hasta.getTime() - desde.getTime();
  const paso = rango > 6 * 3600_000 ? 60_000 : 10_000;
  const valores = agrupar ? (DIMENSIONES[metrica]?.[agrupar] ?? []) : [""];
  const base = BASE.find((a) => a.id === activo);
  const nivel = metrica === "system_memory_utilization" ? (base?.memoria ?? 0.5) : (base?.cpu_reciente ?? 0.3);
  // lo que no llega: desde que el nodo dejo de reportar, o todo si nunca reporto
  const corte = base?.retraso_ms == null ? -Infinity : Date.now() - base.retraso_ms;
  const series = valores.map((v, k) => {
    const s = semilla(activo + metrica + v);
    const puntos: [number, number][] = [];
    for (let t = Math.ceil(desde.getTime() / paso) * paso; t <= hasta.getTime(); t += paso) {
      if (t > corte) continue; // el corte se ve como hueco
      const onda = Math.sin(t / 900_000 + s * 6) * 0.08 + Math.sin(t / 97_000 + s * 3) * 0.03;
      const ruido = (semilla(String(t) + v) - 0.5) * 0.04;
      // agrupado, la primera linea lleva casi todo el nivel y las demas lo que sobra
      const parte = !agrupar ? nivel : k === 0 ? nivel * 0.85 : (nivel * 0.15) / (valores.length - 1 || 1);
      let y = Math.max(0, Math.min(1, parte + onda * (k ? 0.3 : 1) + ruido));
      if (metrica === "system_network_io") y = Math.round(y * 2_000_000);
      puntos.push([t, y]);
    }
    return { etiquetas: agrupar ? { [agrupar]: v } : {}, puntos };
  });
  const esperados = Math.floor(rango / paso);
  const conDatos = series[0]?.puntos.length ?? 0;
  return {
    activo,
    metrica,
    resolucion: paso === 10_000 ? "10s" : "1m",
    desde: desde.toISOString(),
    hasta: hasta.toISOString(),
    agrupar,
    series,
    intervalos_esperados: esperados,
    intervalos_vacios: Math.max(0, esperados - conDatos),
    series_omitidas: 0,
  };
}

// ------------------------------------------------------- configuraciones

// Lo que produce backend/zenit/gobernanza/configuraciones.py con las
// instantaneas de cuatro nodos y deploy/gobernanza/politica.json.
export function configuraciones(): Hallazgo[] {
  const cuando = hace(14 * MIN);
  return [
    {
      regla: "puerto-expuesto-no-esperado",
      severidad: "alta",
      activos: ["activo-do-nodo-01"],
      entornos: ["digitalocean"],
      detalle:
        "El puerto 5432/tcp escucha en 0.0.0.0 en zenit-nodo-do (docker-proxy) y no esta entre los esperados del entorno digitalocean. Es uno de los que nunca deben quedar expuestos.",
      detectado: cuando,
    },
    {
      regla: "contenedor-como-root",
      severidad: "alta",
      activos: ["activo-onprem-01"],
      entornos: ["onprem"],
      detalle: "El contenedor catalogo (ghcr.io/zenit/zenit-catalogo:0.1.9) corre como root en zenit-nodo-onprem.",
      detectado: cuando,
    },
    {
      regla: "intervalo-muestreo-distinto",
      severidad: "media",
      activos: ["activo-onprem-01"],
      entornos: ["onprem"],
      detalle: "Muestrean distinto del comun (10 s): zenit-nodo-onprem cada 30 s.",
      detectado: cuando,
    },
    {
      regla: "version-agente-distinta",
      severidad: "media",
      activos: ["activo-onprem-01"],
      entornos: ["onprem"],
      detalle:
        "zenit-nodo-onprem corre el agente 0.110.0 y el resto 0.116.1. La comparacion entre entornos solo vale si todos miden con la misma version.",
      detectado: cuando,
    },
    {
      regla: "imagen-version-distinta",
      severidad: "baja",
      activos: ["activo-aws-nodo-01", "activo-azure-nodo-01", "activo-do-nodo-01", "activo-onprem-01"],
      entornos: ["aws", "azure", "digitalocean", "onprem"],
      detalle:
        "ghcr.io/zenit/zenit-catalogo corre en 2 versiones: 0.1.9 en zenit-nodo-onprem; 0.2.0 en zenit-nodo-aws, zenit-nodo-azure, zenit-nodo-do.",
      detectado: cuando,
    },
  ];
}

// ---------------------------------------------------------------- costos

// Una factura de septiembre ya importada. Sin fechas, el periodo es todo lo
// importado (como la API).
const IMPORTADO = { desde: "2026-09-01", hasta: "2026-09-30" };

export function costos(desde: string | null, hasta: string | null, vacio: boolean): ReporteCostos {
  const hoy = new Date().toISOString().slice(0, 10);
  if (vacio)
    return { moneda: "USD", periodo: { desde: desde ?? `${hoy.slice(0, 7)}-01`, hasta: hasta ?? hoy }, por_activo: [], sin_asignar: 0 };
  const periodo = { desde: desde ?? IMPORTADO.desde, hasta: hasta ?? IMPORTADO.hasta };
  const cruza = periodo.desde <= IMPORTADO.hasta && periodo.hasta >= IMPORTADO.desde;
  if (!cruza) return { moneda: "USD", periodo, por_activo: [], sin_asignar: 0 };
  const fuente = "factura exportada 2026-09";
  return {
    moneda: "USD",
    periodo,
    por_activo: [
      { activo: "activo-do-plataforma", proveedor: "digitalocean", costo: 28.8, fuente },
      { activo: "activo-do-nodo-01", proveedor: "digitalocean", costo: 12.0, fuente },
    ],
    sin_asignar: 1.37,
  };
}

// ---------------------------------------------------------------- fuentes

// Una fuente por activo vigente, con su retraso. Igual que la API: la regla de
// degradado es mas de 5 min sin recibir metricas.
export function fuentes(): Fuente[] {
  const salida: Fuente[] = BASE.filter((b) => !b.retirado).map((b) => {
    if (b.retraso_ms == null)
      return { activo: b.id, host_name: b.nombre, proveedor: b.proveedor, huerfana: false, senales: {}, ultima_recepcion: null, estado: "sin_datos", minutos_sin_datos: null };
    const estado = b.retraso_ms > 5 * MIN ? "degradado" : "ok";
    const ultima = hace(b.retraso_ms);
    const vivo = estado === "ok";
    return {
      activo: b.id,
      host_name: b.nombre,
      proveedor: b.proveedor,
      huerfana: false,
      senales: {
        metricas: { ultima_recepcion: ultima, estado, recibidas_total: 812_340, errores_total: 3, recibidas_5min: vivo ? 2_730 : 0, errores_1h: 0 },
        registros: { ultima_recepcion: ultima, estado, recibidas_total: 40_120, errores_total: 0, recibidas_5min: vivo ? 96 : 0, errores_1h: 0 },
        ...(b.nombre.startsWith("zenit-nodo")
          ? { trazas: { ultima_recepcion: hace(b.retraso_ms + 2 * MIN), estado, recibidas_total: 9_870, errores_total: vivo ? 0 : 12, recibidas_5min: vivo ? 41 : 0, errores_1h: vivo ? 0 : 4 } }
          : {}),
      },
      ultima_recepcion: ultima,
      estado,
      minutos_sin_datos: Math.round(b.retraso_ms / 6_000) / 10,
    };
  });
  const orden = { degradado: 0, sin_datos: 1, ok: 2 };
  return salida.sort((a, b) => orden[a.estado] - orden[b.estado] || a.activo.localeCompare(b.activo));
}

export function huerfanas(): Huerfana[] {
  return [
    {
      clave: "ip-10-0-3-17.ec2.internal",
      tipo_clave: "host.name",
      proveedor: "aws",
      metricas: 18_204,
      registros: 512,
      trazas: 0,
      primera: hace(95 * MIN),
      ultima: hace(40_000),
      asociada_a: null,
      asociada_en: null,
      resuelve_a: null,
    },
    {
      clave: "srv-core-03",
      tipo_clave: "host.name",
      proveedor: null,
      metricas: 3_960,
      registros: 0,
      trazas: 0,
      primera: hace(3 * 60 * MIN),
      ultima: hace(22 * MIN),
      asociada_a: null,
      asociada_en: null,
      resuelve_a: "activo-onprem-03",
    },
    {
      clave: "zenit-nodo-do-viejo",
      tipo_clave: "host.name",
      proveedor: "digitalocean",
      metricas: 70_118,
      registros: 2_310,
      trazas: 845,
      primera: hace(2 * 24 * 60 * MIN),
      ultima: hace(26 * 60 * MIN),
      asociada_a: "activo-do-nodo-01",
      asociada_en: hace(25 * 60 * MIN),
      resuelve_a: "activo-do-nodo-01",
    },
  ];
}

export function descubrimientos(): Descubrimientos {
  const ej = (id: number, proveedor: string, haceMin: number, estado: "ok" | "error" | "en_curso", n: number[], error: string | null = null) => ({
    id,
    proveedor,
    inicio: hace(haceMin * MIN),
    fin: estado === "en_curso" ? null : hace(haceMin * MIN - 40_000),
    estado,
    vistos: n[0],
    nuevos: n[1],
    retirados: n[2],
    reactivados: n[3],
    error,
  });
  const ultimas = [
    ej(9, "aws", 4, "ok", [3, 0, 0, 0]),
    ej(8, "azure", 4, "error", [0, 0, 0, 0], "AuthorizationFailed: el principal no puede listar maquinas en la suscripcion"),
    ej(7, "digitalocean", 5, "ok", [2, 1, 0, 0]),
    ej(10, "proxmox", 1, "en_curso", [0, 0, 0, 0]),
  ];
  return { ultimas, recientes: [...ultimas, ej(3, "aws", 64, "ok", [4, 0, 1, 0])].sort((a, b) => b.inicio.localeCompare(a.inicio)) };
}

// ----------------------------------------------------------- laboratorio

export function inyecciones(): Inyeccion[] {
  return [
    {
      id: 1,
      uid: "sim-0001",
      tipo: "cpu",
      nodo: "zenit-nodo-aws",
      activo: "activo-aws-nodo-01",
      inicio: hace(40 * MIN),
      fin: null,
      intensidad: "80%",
      parametros: { campana: "simulada" },
      estado: "en_curso",
      causa_en: null,
      manifiesta_en: null,
    },
  ];
}
