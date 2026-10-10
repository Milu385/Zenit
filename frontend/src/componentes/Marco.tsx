import type { ReactNode } from "react";
import { api } from "../api";
import type { Activo, Permiso } from "../contrato";
import { Enlace, useRuta } from "../enrutador";
import { hace } from "../formato";
import { NOMBRE_ROL, useSesion } from "../sesion";
import { useCarga } from "../useCarga";
import { IconoConfiguracion, IconoCostos, IconoIncidentes, IconoInfraestructura, IconoUsuario, Logo } from "./Iconos";

// Diseno general de los wireframes: barra lateral negra, cabecera con titulo,
// saludo y tres indicadores, y el contenido a la derecha.

const SECCIONES: { a: string; texto: string; permiso: Permiso; Icono: (p: { className?: string }) => JSX.Element }[] = [
  { a: "/", texto: "Infraestructura", permiso: "activos:ver", Icono: IconoInfraestructura },
  { a: "/incidentes", texto: "Incidentes", permiso: "incidentes:ver", Icono: IconoIncidentes },
  { a: "/configuraciones", texto: "Configuracion", permiso: "configuraciones:ver", Icono: IconoConfiguracion },
  { a: "/costos", texto: "Costos", permiso: "costos:ver", Icono: IconoCostos },
];

function activa(a: string, ruta: string) {
  if (a === "/") return ruta === "/" || ruta.startsWith("/activos");
  return ruta === a || ruta.startsWith(a + "/");
}

function saludo(d: Date) {
  const h = d.getHours();
  return h < 12 ? "Buenos dias" : h < 19 ? "Buenas tardes" : "Buenas noches";
}

const REFRESCO_INDICADORES_MS = 30_000;

function Indicadores() {
  const r = useCarga((s) => api.activos(s), [], REFRESCO_INDICADORES_MS);
  if (r.carga.estado !== "listo") return null;
  // los retirados ya no deberian reportar: no cuentan como fuentes
  const activos: Activo[] = r.carga.datos.filter((a) => a.estado !== "retirado");
  const abiertos = activos.reduce((n, a) => n + a.incidentes_abiertos, 0);
  const reportando = activos.filter((a) => a.estado !== "sin_datos" && a.estado !== "degradado").length;
  const ultima = activos
    .map((a) => a.ultima_recepcion)
    .filter((x): x is string => !!x)
    .sort()
    .pop();
  const vieja = !ultima || Date.now() - new Date(ultima).getTime() > 3 * 60_000;
  return (
    <ul className="indicadores" aria-label="Estado general">
      <li className={abiertos ? "malo" : "bueno"}>
        <span className="punto" aria-hidden="true" />
        {abiertos} {abiertos === 1 ? "incidente abierto" : "incidentes abiertos"}
      </li>
      <li className={reportando < activos.length ? "regular" : "bueno"}>
        <span className="punto" aria-hidden="true" />
        {reportando} de {activos.length} fuentes reportando
      </li>
      <li className={vieja ? "regular" : "bueno"}>
        <span className="punto" aria-hidden="true" />
        Ultima recepcion {hace(ultima ?? null)}
      </li>
      {r.desactualizado && <li className="regular">Indicadores sin actualizar</li>}
    </ul>
  );
}

export default function Marco({ titulo, children }: { titulo: string; children: ReactNode }) {
  const { sesion, salir, puede } = useSesion();
  const { ruta } = useRuta();
  const ahora = new Date();
  const yo = sesion.estado === "dentro" ? sesion.yo : null;
  return (
    <div className="marco">
      <a className="saltar" href="#contenido">
        Saltar al contenido
      </a>
      <aside className="lateral">
        <Enlace a="/" className="lateral-logo" aria-label="Zenit, inicio">
          <Logo tallo="#fff" />
        </Enlace>
        <nav aria-label="Secciones">
          <ul>
            {SECCIONES.filter((s) => puede(s.permiso)).map(({ a, texto, Icono }) => (
              <li key={a}>
                <Enlace a={a} className={activa(a, ruta) ? "activa" : undefined} aria-current={activa(a, ruta) ? "page" : undefined}>
                  <Icono className="icono" />
                  {texto}
                </Enlace>
              </li>
            ))}
          </ul>
        </nav>
        {yo && (
          <div className="lateral-usuario">
            <IconoUsuario />
            <div>
              <p className="nombre">{yo.usuario}</p>
              <p className="rol">{NOMBRE_ROL[yo.rol]}</p>
              <button type="button" className="boton-texto" onClick={() => void salir()}>
                Cerrar sesion
              </button>
            </div>
          </div>
        )}
      </aside>
      <div className="principal">
        <header className="cabecera-pagina">
          <div>
            <h1>{titulo}</h1>
            <p className="saludo">
              {saludo(ahora)}
              {yo ? `, ${yo.usuario}` : ""} · {ahora.toLocaleDateString("es-CO", { weekday: "long", day: "numeric", month: "long" })},{" "}
              {ahora.toLocaleTimeString("es-CO", { hour: "2-digit", minute: "2-digit" })}
            </p>
          </div>
          {puede("activos:ver") && <Indicadores />}
        </header>
        <main id="contenido" className="contenido-pagina" tabIndex={-1}>
          {children}
        </main>
      </div>
    </div>
  );
}
