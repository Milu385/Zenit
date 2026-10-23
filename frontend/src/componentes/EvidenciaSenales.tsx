import { api } from "../api";
import type { EvidenciaIncidente, MetricaEvidencia } from "../contrato";
import { Enlace } from "../enrutador";
import { fechaHora, formatoValor, nombreMetrica } from "../formato";
import { useCarga } from "../useCarga";
import { SinDatos } from "./Estados";
import SegunCarga from "./SegunCarga";

// Evidencia de las tres senales en el detalle del incidente (H-039, pantalla 4).
// Sale de GET /api/incidentes/{id}/evidencia y se carga aparte del incidente:
// si falla, el resto de la pantalla sigue. Los textos de los registros se
// insertan como texto, nunca como HTML.

const REGISTROS_VISIBLES = 10;

const hora = (iso: string) =>
  new Date(iso).toLocaleTimeString("es-CO", { hour: "2-digit", minute: "2-digit", second: "2-digit" });

const ms = (ns: number | null | undefined) => {
  if (ns == null) return "sin dato";
  const v = ns / 1e6;
  return v >= 1000
    ? `${(v / 1000).toLocaleString("es-CO", { maximumFractionDigits: 2 })} s`
    : `${v.toLocaleString("es-CO", { maximumFractionDigits: 1 })} ms`;
};

// sin el almacen analitico no hay registros ni trazas: no es lo mismo que "no hubo"
const sinAnalitico = (e: EvidenciaIncidente) => e.faltantes.some((f) => f.startsWith("registros"));
const NO_SE_PUDO = "No se pudieron consultar los registros y trazas";

const unaLinea = (texto: string) => texto.replace(/\s+/g, " ").trim();

function cambio(m: MetricaEvidencia) {
  const r = m.resumen;
  const partes: string[] = [];
  if (r.cambio_relativo != null) {
    const pct = r.cambio_relativo * 100;
    partes.push(`${pct >= 0 ? "+" : "−"}${Math.abs(pct).toLocaleString("es-CO", { maximumFractionDigits: 1 })} %`);
  }
  if (r.cambio_absoluto != null) {
    const abs = formatoValor(m.metrica, Math.abs(r.cambio_absoluto), m.clase);
    partes.push(`${r.cambio_absoluto >= 0 ? "+" : "−"}${abs}`);
  }
  return partes.length ? partes.join(" · ") : "sin dato";
}

function Metricas({ e }: { e: EvidenciaIncidente }) {
  return (
    <section className="bloque-evidencia" aria-labelledby="titulo-ev-metricas">
      <h4 id="titulo-ev-metricas">Métricas relacionadas</h4>
      {e.metricas.length === 0 ? (
        <SinDatos titulo="Sin métricas en la ventana" detalle="El almacén de series no devolvió puntos para esta ventana." />
      ) : (
        <div className="tabla-contenedor">
          <table className="tabla">
            <caption className="solo-lector">
              La métrica que se desvió y las relacionadas, con su valor antes y durante el incidente
            </caption>
            <thead>
              <tr>
                <th scope="col">Métrica</th>
                <th scope="col" className="numero">
                  Antes (mediana)
                </th>
                <th scope="col" className="numero">
                  Durante (máximo)
                </th>
                <th scope="col" className="numero">
                  Cambio
                </th>
              </tr>
            </thead>
            <tbody>
              {e.metricas.map((m, i) => {
                const dims = Object.entries(m.dimensiones ?? {});
                return (
                  <tr key={`${m.metrica}-${i}`} className={m.papel === "afectada" ? "fila-elegida" : undefined}>
                    <th scope="row">
                      {nombreMetrica(m.metrica)}
                      {m.papel === "afectada" && " (la que se desvió)"}
                      <span className="meta-dato">
                        {[m.metrica, ...dims.map(([k, v]) => `${k}=${v}`)].join(" · ")}
                        {m.clase === "acumulada" ? " · tasa por segundo" : ""}
                      </span>
                    </th>
                    <td className="numero">{formatoValor(m.metrica, m.resumen.antes_mediana, m.clase)}</td>
                    <td className="numero">{formatoValor(m.metrica, m.resumen.durante_max, m.clase)}</td>
                    <td className="numero">{cambio(m)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <p className="nota">El cambio compara el máximo durante el incidente con la mediana de la hora anterior.</p>
    </section>
  );
}

function Registros({ e }: { e: EvidenciaIncidente }) {
  const visibles = e.registros_error.slice(0, REGISTROS_VISIBLES);
  return (
    <section className="bloque-evidencia" aria-labelledby="titulo-ev-registros">
      <h4 id="titulo-ev-registros">Registros de error</h4>
      {e.conteos && (
        <p className="meta">
          {e.conteos.registros_error.toLocaleString("es-CO")} de {e.conteos.registros.toLocaleString("es-CO")} registros
          de la ventana son de error
          {e.registros_error.length > visibles.length ? `; se muestran los ${visibles.length} más recientes` : ""}.
        </p>
      )}
      {sinAnalitico(e) ? (
        <SinDatos titulo={NO_SE_PUDO} detalle="El almacén de registros no respondió; ver el aviso de arriba." />
      ) : visibles.length === 0 ? (
        <SinDatos titulo="No hubo registros de error en la ventana del incidente" detalle="Ningún registro con severidad de error o más." />
      ) : (
        <div className="tabla-contenedor">
          <table className="tabla tabla-registros">
            <caption className="solo-lector">Registros de error del activo en la ventana del incidente</caption>
            <thead>
              <tr>
                <th scope="col">Hora</th>
                <th scope="col">Servicio</th>
                <th scope="col">Severidad</th>
                <th scope="col">Mensaje</th>
              </tr>
            </thead>
            <tbody>
              {visibles.map((r, i) => (
                <tr key={`${r.momento}-${i}`}>
                  <td title={fechaHora(r.momento)}>{hora(r.momento)}</td>
                  <td>{r.service_name || <span className="apagado">sin servicio</span>}</td>
                  <td>
                    <span className="nivel nivel-error">{r.severidad || "ERROR"}</span>
                  </td>
                  <td className="mensaje-una-linea" title={r.cuerpo}>
                    {unaLinea(r.cuerpo)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function Trazas({ e }: { e: EvidenciaIncidente }) {
  return (
    <section className="bloque-evidencia" aria-labelledby="titulo-ev-trazas">
      <h4 id="titulo-ev-trazas">Trazas con error</h4>
      {e.conteos && (
        <p className="meta">
          {e.conteos.spans_error.toLocaleString("es-CO")} de {e.conteos.spans.toLocaleString("es-CO")} spans de la
          ventana terminaron con error.
        </p>
      )}
      {sinAnalitico(e) ? (
        <SinDatos titulo={NO_SE_PUDO} detalle="El almacén de trazas no respondió; ver el aviso de arriba." />
      ) : e.trazas_error.length === 0 ? (
        <SinDatos titulo="No hubo trazas con error en la ventana del incidente" detalle="Ningún span terminó con error." />
      ) : (
        <div className="tabla-contenedor">
          <table className="tabla">
            <caption className="solo-lector">Trazas con error que tocaron el activo; cada una abre su árbol de spans</caption>
            <thead>
              <tr>
                <th scope="col">Hora</th>
                <th scope="col">Servicio</th>
                <th scope="col">Operación</th>
                <th scope="col" className="numero">
                  Duración
                </th>
                <th scope="col" className="numero">
                  Spans con error
                </th>
              </tr>
            </thead>
            <tbody>
              {e.trazas_error.map((t) => (
                <tr key={t.traza_id}>
                  <td title={fechaHora(t.inicio)}>{hora(t.inicio)}</td>
                  <td>{t.servicios.join(", ")}</td>
                  <th scope="row">
                    {/* la misma vista de traza de H-049, en el detalle del activo */}
                    <Enlace a={`/activos/${encodeURIComponent(e.activo)}?traza=${encodeURIComponent(t.traza_id)}`}>
                      {t.raiz || "(raíz fuera de la ventana)"}
                    </Enlace>
                  </th>
                  <td className="numero">{ms(t.duracion_ns)}</td>
                  <td className="numero">{t.errores}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// "registros y trazas: el almacen analitico no esta configurado" -> "registros y trazas"
const fuente = (f: string) => f.split(":")[0].trim();

export default function EvidenciaSenales({ id }: { id: number }) {
  const r = useCarga((s) => api.evidenciaIncidente(id, s), [id]);
  return (
    <section className="panel panel-blanco evidencia-senales" aria-labelledby="titulo-ev-senales">
      <h3 id="titulo-ev-senales">Métricas, registros y trazas de la ventana</h3>
      <SegunCarga r={r} que="la evidencia de métricas, registros y trazas">
        {(e) => (
          <>
            <p className="meta">
              Ventana de registros y trazas: {fechaHora(e.ventana_registros.desde)} a {fechaHora(e.ventana_registros.hasta)}
            </p>
            {e.faltantes.length > 0 && (
              <p className="aviso-desactualizado" role="status" title={e.faltantes.join("\n")}>
                No se pudo recuperar: {[...new Set(e.faltantes.map(fuente))].join(", ")}. Lo demás se muestra igual.
              </p>
            )}
            <Metricas e={e} />
            <Registros e={e} />
            <Trazas e={e} />
          </>
        )}
      </SegunCarga>
    </section>
  );
}
