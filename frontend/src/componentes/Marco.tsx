import { useEffect, useRef, type ReactNode } from "react";
import { api } from "../api";
import type { Activo, Permiso } from "../contrato";
import { Enlace, useRuta } from "../enrutador";
import { hace } from "../formato";
import { NOMBRE_ROL, useSesion } from "../sesion";
import { useCarga } from "../useCarga";
import { IconoUsuario, Logo } from "./Iconos";
// Iconos de seccion de Maria Lucia (carpeta "disenos zenit/Iconos secciones"):
// amarillo sobre la barra negra y negro sobre la pastilla blanca de la activa
import configuracionesAmarillo from "../recursos/iconos/configuraciones-amarillo.png";
import configuracionesNegro from "../recursos/iconos/configuraciones-negro.png";
import costosAmarillo from "../recursos/iconos/costos-amarillo.png";
import costosNegro from "../recursos/iconos/costos-negro.png";
import fuentesAmarillo from "../recursos/iconos/fuentes-amarillo.png";
import fuentesNegro from "../recursos/iconos/fuentes-negro.png";
import incidentesAmarillo from "../recursos/iconos/incidentes-amarillo.png";
import incidentesNegro from "../recursos/iconos/incidentes-negro.png";
import infraestructuraAmarillo from "../recursos/iconos/infraestructura-amarillo.png";
import infraestructuraNegro from "../recursos/iconos/infraestructura-negro.png";

// Diseno general de los wireframes: barra lateral negra, cabecera con titulo,
// saludo y tres indicadores, y el contenido a la derecha.

const SECCIONES: { a: string; texto: string; permiso: Permiso; icono: [string, string] }[] = [
  { a: "/", texto: "Infraestructura", permiso: "activos:ver", icono: [infraestructuraAmarillo, infraestructuraNegro] },
  { a: "/incidentes", texto: "Incidentes", permiso: "incidentes:ver", icono: [incidentesAmarillo, incidentesNegro] },
  { a: "/configuraciones", texto: "Configuración", permiso: "configuraciones:ver", icono: [configuracionesAmarillo, configuracionesNegro] },
  { a: "/costos", texto: "Costos", permiso: "costos:ver", icono: [costosAmarillo, costosNegro] },
  { a: "/fuentes", texto: "Fuentes", permiso: "fuentes:ver", icono: [fuentesAmarillo, fuentesNegro] },
];

function activa(a: string, ruta: string) {
  if (a === "/") return ruta === "/" || ruta.startsWith("/activos");
  return ruta === a || ruta.startsWith(a + "/");
}

const mayuscula = (t: string) => t.charAt(0).toUpperCase() + t.slice(1);

function saludo(d: Date) {
  const h = d.getHours();
  return h < 12 ? "Buenos días" : h < 19 ? "Buenas tardes" : "Buenas noches";
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
        <b>{abiertos}</b> {abiertos === 1 ? "incidente abierto" : "incidentes abiertos"}
      </li>
      <li className={reportando < activos.length ? "regular" : "bueno"}>
        <span className="punto" aria-hidden="true" />
        <b>{reportando}</b> de <b>{activos.length}</b> fuentes reportando
      </li>
      <li className={vieja ? "regular" : "bueno"}>
        <span className="punto" aria-hidden="true" />
        Última recepción <b>{hace(ultima ?? null)}</b>
      </li>
      {r.desactualizado && <li className="regular">Indicadores sin actualizar</li>}
    </ul>
  );
}

export default function Marco({ titulo, children }: { titulo: string; children: ReactNode }) {
  const { sesion, salir, puede } = useSesion();
  const { ruta } = useRuta();
  const ahora = new Date();
  // H-054: al cambiar de pantalla el foco va al titulo, no se queda en el
  // cuerpo de la pagina; el lector de pantalla anuncia donde se llego
  const titulo1 = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    titulo1.current?.focus({ preventScroll: true });
  }, [ruta]);
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
            {SECCIONES.filter((s) => puede(s.permiso)).map(({ a, texto, icono }) => (
              <li key={a}>
                <Enlace a={a} className={activa(a, ruta) ? "activa" : undefined} aria-current={activa(a, ruta) ? "page" : undefined}>
                  <img className="icono" src={activa(a, ruta) ? icono[1] : icono[0]} alt="" width={30} height={30} />
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
                Cerrar sesión
              </button>
            </div>
          </div>
        )}
      </aside>
      <div className="principal">
        <header className="cabecera-pagina">
          <div>
            <h1 ref={titulo1} tabIndex={-1} className="titulo-pagina">
              {titulo}
            </h1>
            <p className="saludo">
              {saludo(ahora)}
              {yo ? `, ${mayuscula(yo.usuario)}` : ""}
              <span className="saludo-fecha">
                {mayuscula(ahora.toLocaleDateString("es-CO", { weekday: "long", day: "numeric", month: "long" }))},{" "}
                {ahora.toLocaleTimeString("es-CO", { hour: "2-digit", minute: "2-digit", hour12: false })}
              </span>
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
