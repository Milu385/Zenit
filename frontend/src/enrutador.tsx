import { useEffect, useState, type AnchorHTMLAttributes, type MouseEvent } from "react";

// Enrutador minimo sobre la History API. Cinco rutas no justifican una
// dependencia; nginx ya devuelve index.html para cualquier ruta (nginx-web.conf).

const CAMBIO = "zenit:ruta";

export function navegar(ruta: string, reemplazar = false) {
  if (reemplazar) window.history.replaceState(null, "", ruta);
  else window.history.pushState(null, "", ruta);
  window.dispatchEvent(new Event(CAMBIO));
  window.scrollTo(0, 0);
}

export function useRuta(): { ruta: string; consulta: URLSearchParams } {
  const leer = () => ({ ruta: window.location.pathname, busqueda: window.location.search });
  const [actual, setActual] = useState(leer);
  useEffect(() => {
    const cambiar = () => setActual(leer());
    window.addEventListener("popstate", cambiar);
    window.addEventListener(CAMBIO, cambiar);
    return () => {
      window.removeEventListener("popstate", cambiar);
      window.removeEventListener(CAMBIO, cambiar);
    };
  }, []);
  return { ruta: actual.ruta, consulta: new URLSearchParams(actual.busqueda) };
}

/** "/incidentes/:id" contra "/incidentes/12" -> { id: "12" }; null si no coincide. */
export function coincide(patron: string, ruta: string): Record<string, string> | null {
  const p = patron.split("/").filter(Boolean);
  const r = ruta.split("/").filter(Boolean);
  if (p.length !== r.length) return null;
  const params: Record<string, string> = {};
  for (let i = 0; i < p.length; i++) {
    if (p[i].startsWith(":")) params[p[i].slice(1)] = decodeURIComponent(r[i]);
    else if (p[i] !== r[i]) return null;
  }
  return params;
}

type PropsEnlace = AnchorHTMLAttributes<HTMLAnchorElement> & { a: string };

/** Enlace de verdad (se puede abrir en otra pestana) que navega sin recargar. */
export function Enlace({ a, onClick, ...resto }: PropsEnlace) {
  const alPulsar = (e: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(e);
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    navegar(a);
  };
  return <a href={a} onClick={alPulsar} {...resto} />;
}
