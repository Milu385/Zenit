import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, ErrorApi, guardarToken, hayToken, SESION_VENCIDA } from "./api";
import type { PeticionSesion, Permiso, Yo } from "./contrato";

// Quien esta usando la interfaz: lo que responde /api/yo. El token lo guarda
// api.ts. Ocultar algo por rol es comodidad: quien decide es la API (403).

type EstadoSesion =
  | { estado: "comprobando" }
  | { estado: "anonimo"; motivo?: string }
  | { estado: "dentro"; yo: Yo };

type ValorSesion = {
  sesion: EstadoSesion;
  entrar: (p: PeticionSesion, recordar: boolean) => Promise<void>;
  salir: () => Promise<void>;
  puede: (permiso: Permiso) => boolean;
};

const Contexto = createContext<ValorSesion | null>(null);

export function ProveedorSesion({ children }: { children: ReactNode }) {
  const [sesion, setSesion] = useState<EstadoSesion>({ estado: "comprobando" });

  useEffect(() => {
    if (!hayToken()) {
      setSesion({ estado: "anonimo" });
      return;
    }
    const ctl = new AbortController();
    api
      .yo(ctl.signal)
      .then((yo) => setSesion({ estado: "dentro", yo }))
      .catch((e) => {
        if ((e as Error).name === "AbortError") return;
        // 401 es lo normal sin sesion; otro error se dice en el login
        const motivo = e instanceof ErrorApi && e.estado !== 401 ? e.message : undefined;
        setSesion({ estado: "anonimo", motivo });
      });
    return () => ctl.abort();
  }, []);

  useEffect(() => {
    const vencida = () => setSesion({ estado: "anonimo", motivo: "Tu sesion vencio. Vuelve a entrar." });
    window.addEventListener(SESION_VENCIDA, vencida);
    return () => window.removeEventListener(SESION_VENCIDA, vencida);
  }, []);

  const entrar = useCallback(async (p: PeticionSesion, recordar: boolean) => {
    const { token } = await api.iniciarSesion(p);
    guardarToken(token, recordar);
    // los permisos los da /api/yo, no la respuesta de la sesion
    const yo = await api.yo();
    setSesion({ estado: "dentro", yo });
  }, []);

  // El contrato no tiene cierre de sesion en el servidor: se olvida el token y
  // este caduca solo (8 h). Ver NOTAS.md.
  const salir = useCallback(async () => {
    guardarToken(null);
    setSesion({ estado: "anonimo" });
  }, []);

  const valor = useMemo<ValorSesion>(
    () => ({
      sesion,
      entrar,
      salir,
      puede: (permiso) => sesion.estado === "dentro" && sesion.yo.permisos.includes(permiso),
    }),
    [sesion, entrar, salir],
  );
  return <Contexto.Provider value={valor}>{children}</Contexto.Provider>;
}

export function useSesion(): ValorSesion {
  const v = useContext(Contexto);
  if (!v) throw new Error("useSesion fuera de ProveedorSesion");
  return v;
}

export const NOMBRE_ROL: Record<Yo["rol"], string> = {
  administrador: "Administrador",
  operador: "Operador",
  seguridad: "Analista de seguridad",
  finanzas: "Responsable financiero",
};
