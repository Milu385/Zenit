import type { ReactNode } from "react";
import type { ResultadoCarga } from "../useCarga";
import { AvisoDesactualizado, Cargando, ErrorVista, SinPermiso } from "./Estados";

// Elige el estado transversal que toca y, si hay datos, los entrega al hijo.
export default function SegunCarga<T>({
  r,
  que,
  children,
}: {
  r: ResultadoCarga<T>;
  que: string; // "el inventario", "los costos"... para los textos
  children: (datos: T) => ReactNode;
}) {
  const { carga } = r;
  if (carga.estado === "cargando") return <Cargando texto={`Cargando ${que}...`} />;
  if (carga.estado === "sin_permiso") return <SinPermiso que={que} />;
  if (carga.estado === "error") return <ErrorVista mensaje={carga.mensaje} reintentar={r.reintentar} />;
  return (
    <>
      {r.desactualizado && <AvisoDesactualizado desde={r.actualizado} motivo={r.desactualizado} reintentar={r.reintentar} />}
      {children(carga.datos)}
    </>
  );
}
