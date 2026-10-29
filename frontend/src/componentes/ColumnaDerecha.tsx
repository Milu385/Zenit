import type { ReactNode } from "react";
import { api } from "../api";
import type { Activo, Proveedor } from "../contrato";
import { datosProveedor, ESTADO_ACTIVO, ORDEN_ESTADO_ACTIVO } from "../formato";
import { useSesion } from "../sesion";
import { useCarga } from "../useCarga";

// Columna azul de la derecha, como en todos los wireframes: arriba la
// tarjeta "Inventario hibrido" y debajo lo propio de cada pantalla (la
// importacion de la factura en costos). En la infraestructura la misma
// tarjeta sirve de filtro por proveedor.
//
// Del wireframe no se trae "Cambios recientes": la API no tiene un registro
// de cambios. Lo unico parecido es quien confirmo o descarto un incidente
// (marcada_por), y solo sale en el detalle de cada uno; armar la lista
// pediria un detalle por incidente en cada pantalla. Queda fuera hasta que
// exista una ruta para eso.

const REFRESCO_MS = 30_000;

/** Elegir un proveedor en la tarjeta (solo en la infraestructura) filtra la
 * lista; es el mismo filtro de proveedor de la barra de filtros. Antes habia
 * ademas un bloque "Servidores visualizados" que repetia esta misma lista. */
export interface FiltroProveedor {
  elegido: Proveedor | "";
  alElegir: (p: Proveedor | "") => void;
}

export function InventarioHibrido({ activos, filtro }: { activos: Activo[]; filtro?: FiltroProveedor }) {
  const proveedores = [...new Set(activos.map((a) => a.proveedor))].sort();
  return (
    <section className="panel-blanco resumen-hibrido" aria-labelledby="titulo-hibrido">
      <h2 id="titulo-hibrido">Inventario híbrido</h2>
      {proveedores.length === 0 ? (
        <p className="nota">Todavía no hay activos en el catálogo.</p>
      ) : (
        <ul>
          {proveedores.map((p) => {
            const suyos = activos.filter((a) => a.proveedor === p);
            const contenido = (
              <>
                <span>
                  <span className="resumen-nombre">{datosProveedor(p).nombre}</span>
                  <span className="resumen-meta">
                    {suyos.length} {suyos.length === 1 ? "activo" : "activos"}
                  </span>
                </span>
                <span
                  className="cuadros"
                  role="img"
                  aria-label={ORDEN_ESTADO_ACTIVO.map(
                    (e) => `${suyos.filter((a) => a.estado === e).length} ${ESTADO_ACTIVO[e].plural.toLowerCase()}`,
                  ).join(", ")}
                >
                  {suyos
                    .slice()
                    .sort((x, y) => ORDEN_ESTADO_ACTIVO.indexOf(x.estado) - ORDEN_ESTADO_ACTIVO.indexOf(y.estado))
                    .map((a) => (
                      <span key={a.id} className={`cuadro cuadro-${a.estado}`} title={`${a.nombre}: ${ESTADO_ACTIVO[a.estado].texto}`} />
                    ))}
                </span>
              </>
            );
            if (!filtro) return <li key={p}>{contenido}</li>;
            const elegido = filtro.elegido === p;
            return (
              <li key={p} className="resumen-con-boton">
                <button
                  type="button"
                  className={`resumen-fila${filtro.elegido && !elegido ? " atenuada" : ""}`}
                  aria-pressed={elegido}
                  onClick={() => filtro.alElegir(elegido ? "" : p)}
                >
                  {contenido}
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {filtro && proveedores.length > 1 && (
        <p className="nota nota-filtro">
          {filtro.elegido ? "Pulsa el proveedor otra vez para ver todos." : "Pulsa un proveedor para ver solo sus activos."}
        </p>
      )}
    </section>
  );
}

const nunca = () => new Promise<never>(() => {});

/**
 * El contenido de la pantalla a la izquierda y la columna azul a la derecha.
 * `activos`: si la pantalla ya los cargo, se usan esos y no se piden otra vez.
 * Sin permiso para ver activos y sin nada propio que poner, no hay columna.
 */
export default function ConColumna({
  children,
  lateral,
  activos,
  filtro,
}: {
  children: ReactNode;
  lateral?: ReactNode;
  activos?: Activo[];
  filtro?: FiltroProveedor;
}) {
  const { puede } = useSesion();
  const verActivos = puede("activos:ver");
  const r = useCarga((s) => (activos || !verActivos ? nunca() : api.activos(s)), [!!activos, verActivos], REFRESCO_MS);
  const lista = activos ?? (r.carga.estado === "listo" ? r.carga.datos : null);
  const hayInventario = verActivos && lista;
  if (!hayInventario && !lateral) return <>{children}</>;
  return (
    <div className="rejilla-con-lateral">
      <div>{children}</div>
      <aside className="panel panel-azul columna-derecha" aria-label="Resumen de la infraestructura">
        {hayInventario && <InventarioHibrido activos={lista} filtro={filtro} />}
        {lateral}
      </aside>
    </div>
  );
}
