import type { CorrelacionEvidencia } from "../contrato";
import { Enlace } from "../enrutador";
import { fechaHora } from "../formato";
import { SinDatos } from "./Estados";

// Registros unidos con el span que los produjo (RF-ALM-07). La union la hace
// el almacen analitico por el identificador canonico (traza y span), no esta
// pantalla. Sale en dos lugares: la evidencia del incidente (los mismos que
// se le entregan al diagnostico) y el detalle del activo, para cualquier
// ventana. Los cuerpos se insertan como texto, nunca como HTML.

const hora = (iso: string) =>
  new Date(iso).toLocaleTimeString("es-CO", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });

const ms = (ns: number | null) => {
  if (ns == null) return null;
  const v = ns / 1e6;
  return v >= 1000
    ? `${(v / 1000).toLocaleString("es-CO", { maximumFractionDigits: 2 })} s`
    : `${v.toLocaleString("es-CO", { maximumFractionDigits: 1 })} ms`;
};

const claseNivel = (sev: string) =>
  /^(ERROR|FATAL)/i.test(sev) ? "nivel-error" : /^WARN/i.test(sev) ? "nivel-aviso" : "nivel-info";

export default function TablaCorrelacion({
  filas,
  activo,
  verTraza,
  vacio,
}: {
  filas: CorrelacionEvidencia[];
  activo: string;
  // en el detalle del activo la traza se abre ahi mismo; en el incidente,
  // con un enlace a la ficha del activo
  verTraza?: (id: string) => void;
  vacio: { titulo: string; detalle: string };
}) {
  if (filas.length === 0) return <SinDatos titulo={vacio.titulo} detalle={vacio.detalle} />;
  return (
    <div className="tabla-contenedor tabla-alta">
      <table className="tabla tabla-correlacion">
        <caption className="solo-lector">Registros con el span que los produjo, el más reciente primero</caption>
        <thead>
          <tr>
            <th scope="col">Hora</th>
            <th scope="col">Servicio</th>
            <th scope="col">Nivel</th>
            <th scope="col">Mensaje</th>
            <th scope="col">Span que lo produjo</th>
            <th scope="col" className="numero">
              Duración del span
            </th>
          </tr>
        </thead>
        <tbody>
          {filas.map((f, i) => (
            <tr key={`${f.momento}-${i}`}>
              <td title={fechaHora(f.momento)}>{hora(f.momento)}</td>
              <td>{f.servicio || <span className="apagado">sin servicio</span>}</td>
              <td>
                <span className={`nivel ${claseNivel(f.severidad)}`}>{f.severidad || "sin nivel"}</span>
              </td>
              <td className="cuerpo-registro">{f.cuerpo}</td>
              <td>
                {f.operacion ? (
                  <>
                    {f.traza_id && verTraza ? (
                      <button type="button" className="boton-texto" onClick={() => verTraza(f.traza_id)}>
                        {f.operacion}
                      </button>
                    ) : f.traza_id ? (
                      <Enlace a={`/activos/${encodeURIComponent(activo)}?traza=${encodeURIComponent(f.traza_id)}`}>{f.operacion}</Enlace>
                    ) : (
                      f.operacion
                    )}
                    {f.estado_span === "error" && <span className="meta-dato texto-mal">el span terminó con error</span>}
                  </>
                ) : (
                  <span className="apagado">{f.traza_id ? "span fuera de la ventana" : "sin traza"}</span>
                )}
              </td>
              <td className="numero">{ms(f.duracion_ns) ?? <span className="apagado">sin dato</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
