import { useCallback, useEffect, useRef, useState } from "react";
import { ErrorApi } from "./api";

// Carga de una pantalla con los estados transversales de pantallas-wireframe:
// cargando, error, sin permiso y datos (el vacio lo decide cada pantalla con
// los datos en la mano). El quinto, datos desactualizados, se da cuando ya
// habia datos y un refresco fallo: se siguen mostrando, con su hora, en vez
// de cambiarlos por un error.

export type Carga<T> =
  | { estado: "cargando" }
  | { estado: "error"; mensaje: string }
  | { estado: "sin_permiso"; mensaje: string }
  | { estado: "listo"; datos: T };

export type ResultadoCarga<T> = {
  carga: Carga<T>;
  actualizado: Date | null;
  desactualizado: string | null; // por que, si lo esta
  refrescando: boolean;
  reintentar: () => void;
  reemplazar: (f: (d: T) => T) => void;
};

const abortado = (e: unknown) => e instanceof Error && e.name === "AbortError";

export function useCarga<T>(
  pedir: (senal: AbortSignal) => Promise<T>,
  deps: unknown[],
  refrescoMs = 0,
): ResultadoCarga<T> {
  const [carga, setCarga] = useState<Carga<T>>({ estado: "cargando" });
  const [actualizado, setActualizado] = useState<Date | null>(null);
  const [desactualizado, setDesactualizado] = useState<string | null>(null);
  const [refrescando, setRefrescando] = useState(false);
  const [intento, setIntento] = useState(0);
  const tieneDatos = useRef(false);
  const enCurso = useRef<AbortController | null>(null);
  const pedirEstable = useCallback(pedir, deps);

  const cargar = useCallback(
    (primera: boolean) => {
      enCurso.current?.abort();
      const ctl = new AbortController();
      enCurso.current = ctl;
      if (primera) {
        tieneDatos.current = false;
        setCarga({ estado: "cargando" });
        setDesactualizado(null);
      } else setRefrescando(true);
      pedirEstable(ctl.signal)
        .then((datos) => {
          if (ctl.signal.aborted) return;
          tieneDatos.current = true;
          setCarga({ estado: "listo", datos });
          setActualizado(new Date());
          setDesactualizado(null);
        })
        .catch((e) => {
          if (abortado(e) || ctl.signal.aborted) return;
          const mensaje = e instanceof Error ? e.message : String(e);
          if (e instanceof ErrorApi && e.estado === 403) setCarga({ estado: "sin_permiso", mensaje });
          else if (tieneDatos.current) setDesactualizado(mensaje);
          else setCarga({ estado: "error", mensaje });
        })
        .finally(() => !ctl.signal.aborted && setRefrescando(false));
    },
    [pedirEstable],
  );

  useEffect(() => {
    cargar(true);
    return () => enCurso.current?.abort();
  }, [cargar, intento]);

  useEffect(() => {
    if (!refrescoMs) return;
    const t = window.setInterval(() => cargar(false), refrescoMs);
    return () => window.clearInterval(t);
  }, [cargar, refrescoMs]);

  return {
    carga,
    actualizado,
    desactualizado,
    refrescando,
    reintentar: useCallback(() => setIntento((n) => n + 1), []),
    reemplazar: useCallback(
      (f: (d: T) => T) => setCarga((c) => (c.estado === "listo" ? { estado: "listo", datos: f(c.datos) } : c)),
      [],
    ),
  };
}
