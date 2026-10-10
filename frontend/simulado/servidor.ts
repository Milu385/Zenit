// Servidor de datos simulados: un plugin de Vite que contesta /api/* con las
// rutas y formas del contrato v1.1 (src/contrato.ts). Se activa con
// `npm run dev:simulado`.
//
// Aplica la misma matriz de permisos que la API (backend/zenit/gobernanza/
// permisos.py), asi se prueba el estado "sin permiso" entrando con cada rol.
//
// ZENIT_ESCENARIO cambia el comportamiento para ver los estados transversales:
//   normal (por defecto) | vacio | error | lento | intermitente
import type { IncomingMessage, ServerResponse } from "node:http";
import type { Plugin } from "vite";
import type { Permiso, PeticionEstado, PeticionSesion, RespuestaSesion, ResumenImportacion, Rol, Yo } from "../src/contrato";
import * as d from "./datos";

const MATRIZ: Record<Permiso, Rol[]> = {
  "activos:ver": ["administrador", "operador", "seguridad", "finanzas"],
  "incidentes:ver": ["administrador", "operador", "seguridad"],
  "incidentes:gestionar": ["administrador", "operador"],
  "configuraciones:ver": ["administrador", "seguridad"],
  "costos:ver": ["administrador", "finanzas"],
  "laboratorio:ver": ["administrador"],
  "usuarios:gestionar": ["administrador"],
  // fuera de la tabla del contrato (NOTAS.md)
  "configuraciones:enviar": ["administrador"],
  "costos:importar": ["administrador", "finanzas"],
};
const permisos = (rol: Rol) => (Object.keys(MATRIZ) as Permiso[]).filter((p) => MATRIZ[p].includes(rol)).sort();

// El token simulado es "simulado.<usuario>": no se firma porque aqui no hay
// nada que proteger. La API real firma el suyo (zenit.gobernanza.sesiones).
const PREFIJO_TOKEN = "simulado.";
const DURACION_MS = 8 * 3600_000;
const DIAGNOSTICO_EN_COLA_MS = 8_000;
const importadas = new Set<string>();
let llamadas = 0;

type Ctx = { req: IncomingMessage; res: ServerResponse; url: URL; usuario: string | null; rol: Rol | null };

function enviar(res: ServerResponse, estado: number, cuerpo?: unknown, cabeceras: Record<string, string> = {}) {
  res.statusCode = estado;
  for (const [k, v] of Object.entries(cabeceras)) res.setHeader(k, v);
  if (cuerpo === undefined) return res.end();
  res.setHeader("Content-Type", "application/json");
  res.end(JSON.stringify(cuerpo));
}

const leerCuerpo = (req: IncomingMessage) =>
  new Promise<string>((ok, mal) => {
    let datos = "";
    req.on("data", (c) => (datos += c));
    req.on("end", () => ok(datos));
    req.on("error", mal);
  });

function leerJson<T>(texto: string): T | null {
  try {
    return JSON.parse(texto || "null") as T;
  } catch {
    return null;
  }
}

function usuarioDe(req: IncomingMessage): string | null {
  const m = (req.headers.authorization ?? "").match(/^Bearer\s+(\S+)$/i);
  if (!m || !m[1].startsWith(PREFIJO_TOKEN)) return null;
  const u = m[1].slice(PREFIJO_TOKEN.length);
  return u in d.USUARIOS ? u : null;
}

// "autenticada": basta con tener sesion (GET /api/yo)
type Ruta = { metodo: string; patron: RegExp; permiso: Permiso | "publica" | "autenticada"; h: (c: Ctx, m: RegExpMatchArray) => unknown };

const yo = (usuario: string): Yo => {
  const rol = d.USUARIOS[usuario];
  return { usuario, rol, permisos: permisos(rol) };
};

const fecha = (texto: string | null, porDefecto: Date) => (texto ? new Date(texto) : porDefecto);
const FECHA = /^\d{4}-\d{2}-\d{2}$/;
const id = (m: RegExpMatchArray) => Number(m[1]);

const RUTAS: Ruta[] = [
  { metodo: "GET", patron: /^\/api\/salud$/, permiso: "publica", h: () => ({ estado: "ok", almacen: true }) },

  // ---- sesion
  {
    metodo: "POST",
    patron: /^\/api\/sesion$/,
    permiso: "publica",
    h: async ({ req, res }) => {
      const p = leerJson<PeticionSesion>(await leerCuerpo(req));
      if (!p || typeof p.usuario !== "string" || typeof p.clave !== "string")
        return enviar(res, 422, { detail: "se espera {usuario, clave}" });
      // mismo mensaje exista o no el usuario (contrato)
      if (!(p.usuario in d.USUARIOS) || p.clave !== d.CLAVE_SIMULADA) return enviar(res, 401, { detail: "usuario o clave incorrectos" });
      const r: RespuestaSesion = {
        token: PREFIJO_TOKEN + p.usuario,
        usuario: p.usuario,
        rol: d.USUARIOS[p.usuario],
        expira: new Date(Date.now() + DURACION_MS).toISOString(),
      };
      return r;
    },
  },
  { metodo: "GET", patron: /^\/api\/yo$/, permiso: "autenticada", h: ({ usuario }) => yo(usuario!) },

  // ---- activos y series
  { metodo: "GET", patron: /^\/api\/activos$/, permiso: "activos:ver", h: () => (escenario() === "vacio" ? [] : d.activos()) },
  {
    metodo: "GET",
    patron: /^\/api\/activos\/([^/]+)$/,
    permiso: "activos:ver",
    h: ({ res }, m) => {
      const a = d.activosDetalle().find((x) => x.id === decodeURIComponent(m[1]));
      return a ?? enviar(res, 404, { detail: `no existe el activo ${decodeURIComponent(m[1])}` });
    },
  },
  {
    metodo: "GET",
    patron: /^\/api\/activos\/([^/]+)\/metricas$/,
    permiso: "activos:ver",
    h: (_, m) => ({ activo: decodeURIComponent(m[1]), metricas: d.METRICAS }),
  },
  {
    metodo: "GET",
    patron: /^\/api\/activos\/([^/]+)\/metricas\/([^/]+)\/dimensiones$/,
    permiso: "activos:ver",
    h: (_, m) => ({ activo: decodeURIComponent(m[1]), metrica: m[2], dimensiones: d.DIMENSIONES[m[2]] ?? {} }),
  },
  {
    metodo: "GET",
    patron: /^\/api\/activos\/([^/]+)\/series\/([^/]+)$/,
    permiso: "activos:ver",
    h: ({ url }, m) => {
      const hasta = fecha(url.searchParams.get("hasta"), new Date());
      const desde = fecha(url.searchParams.get("desde"), new Date(hasta.getTime() - 3600_000));
      return d.serie(decodeURIComponent(m[1]), m[2], desde, hasta, url.searchParams.get("agrupar"));
    },
  },

  // ---- incidentes
  {
    metodo: "GET",
    patron: /^\/api\/incidentes$/,
    permiso: "incidentes:ver",
    h: ({ url }) =>
      escenario() === "vacio" ? [] : d.incidentes({ estado: url.searchParams.get("estado"), activo: url.searchParams.get("activo") }),
  },
  {
    metodo: "GET",
    patron: /^\/api\/incidentes\/(\d+)$/,
    permiso: "incidentes:ver",
    h: ({ res }, m) => d.detalle(id(m)) ?? enviar(res, 404, { detail: `no existe el incidente ${m[1]}` }),
  },
  {
    metodo: "POST",
    patron: /^\/api\/incidentes\/(\d+)\/estado$/,
    permiso: "incidentes:gestionar",
    h: async ({ req, res }, m) => {
      const p = leerJson<PeticionEstado>(await leerCuerpo(req));
      if (!p || !["confirmada", "falso_positivo"].includes(p.estado))
        return enviar(res, 422, { detail: "estado invalido: solo confirmada o falso_positivo" });
      if (!d.existeIncidente(id(m))) return enviar(res, 404, { detail: `no existe el incidente ${m[1]}` });
      d.estados.set(id(m), p.estado);
      // el contrato no fija la respuesta; se devuelve el incidente como queda
      return d.incidentes().find((i) => i.id === id(m));
    },
  },
  {
    metodo: "POST",
    patron: /^\/api\/incidentes\/(\d+)\/diagnostico$/,
    permiso: "incidentes:gestionar",
    h: ({ res }, m) => {
      if (!d.existeIncidente(id(m))) return enviar(res, 404, { detail: `no existe el incidente ${m[1]}` });
      // sin diagnostico: queda en cola (202). Con diagnostico: se regenera al momento (200).
      if (!d.tieneDiagnostico(id(m))) {
        d.pedidos.set(id(m), { listo: Date.now() + DIAGNOSTICO_EN_COLA_MS });
        return enviar(res, 202);
      }
      d.pedidos.set(id(m), { listo: Date.now() });
      return d.detalle(id(m))!.diagnostico;
    },
  },

  // ---- gobernanza
  {
    metodo: "GET",
    patron: /^\/api\/gobernanza\/configuraciones$/,
    permiso: "configuraciones:ver",
    h: () => (escenario() === "vacio" ? [] : d.configuraciones()),
  },
  {
    metodo: "GET",
    patron: /^\/api\/gobernanza\/costos$/,
    permiso: "costos:ver",
    h: ({ url, res }) => {
      const desde = url.searchParams.get("desde") || null;
      const hasta = url.searchParams.get("hasta") || null;
      if ((desde && !FECHA.test(desde)) || (hasta && !FECHA.test(hasta)))
        return enviar(res, 422, { detail: "fechas en formato AAAA-MM-DD" });
      if (desde && hasta && desde > hasta) return enviar(res, 422, { detail: "desde es posterior a hasta" });
      return d.costos(desde, hasta, escenario() === "vacio");
    },
  },
  {
    // FUERA DEL CONTRATO (NOTAS.md)
    metodo: "POST",
    patron: /^\/api\/gobernanza\/costos\/facturas$/,
    permiso: "costos:importar",
    h: async ({ req, res, url }) => {
      if (url.searchParams.get("proveedor") !== "digitalocean") return enviar(res, 422, { detail: "proveedor no soportado" });
      const texto = await leerCuerpo(req);
      const lineas = texto.split(/\r?\n/).filter((l) => l.trim()).length - 1;
      if (!/description/i.test(texto) || lineas < 1)
        return enviar(res, 422, { detail: "no parece una factura de DigitalOcean: faltan las columnas description y USD" });
      const nueva = !importadas.has(texto);
      importadas.add(texto);
      const r: ResumenImportacion = {
        huella: "simulada",
        proveedor: "digitalocean",
        nueva,
        lineas,
        asignadas: Math.max(0, lineas - 2),
        sin_asignar: Math.min(2, lineas),
        total: 42.17,
        lineas_sin_asignar: [
          {
            concepto: "volumen-sin-nombre (10 GiB)",
            producto: "Volumes",
            monto: 1.0,
            motivo: "ningun activo del catalogo aparece en la linea",
          },
          { concepto: "Bandwidth overage", producto: "Bandwidth", monto: 0.37, motivo: "ningun activo del catalogo aparece en la linea" },
        ].slice(0, Math.min(2, lineas)),
      };
      enviar(res, 201, r);
    },
  },

  // ---- laboratorio
  { metodo: "GET", patron: /^\/api\/laboratorio\/inyecciones$/, permiso: "laboratorio:ver", h: () => d.inyecciones() },
];

const escenario = () => process.env.ZENIT_ESCENARIO ?? "normal";
const esperar = (ms: number) => new Promise((ok) => setTimeout(ok, ms));

async function atender(req: IncomingMessage, res: ServerResponse) {
  const url = new URL(req.url ?? "/", "http://simulado");
  const ruta = RUTAS.find((r) => r.metodo === req.method && r.patron.test(url.pathname));
  if (!ruta) return enviar(res, 404, { detail: "Not Found" });

  await esperar(escenario() === "lento" ? 2500 : 150 + Math.random() * 250);
  const usuario = usuarioDe(req);
  const rol = usuario ? d.USUARIOS[usuario] : null;
  if (ruta.permiso !== "publica") {
    if (!rol) return enviar(res, 401, { detail: "inicia sesion para continuar" }, { "WWW-Authenticate": "Bearer" });
    if (ruta.permiso !== "autenticada" && !MATRIZ[ruta.permiso].includes(rol))
      return enviar(res, 403, { detail: "tu rol no tiene permiso para esto" });
    if (escenario() === "error") return enviar(res, 503, { detail: "el almacen de series no respondio" });
    if (escenario() === "intermitente" && ++llamadas % 3 === 0) return enviar(res, 503, { detail: "el almacen de series no respondio" });
  }
  const resultado = await ruta.h({ req, res, url, usuario, rol }, url.pathname.match(ruta.patron)!);
  if (!res.writableEnded) enviar(res, 200, resultado);
}

export default function servidorSimulado(): Plugin {
  return {
    name: "zenit-simulado",
    configureServer(server) {
      server.config.logger.info(
        `\n  API simulada activa (escenario: ${escenario()}). Usuarios: ${Object.keys(d.USUARIOS).join(", ")}; clave: ${d.CLAVE_SIMULADA}\n`,
      );
      server.middlewares.use((req, res, siguiente) => {
        if (!req.url?.startsWith("/api/")) return siguiente();
        atender(req, res).catch((e) => enviar(res, 500, { detail: String(e) }));
      });
    },
  };
}
