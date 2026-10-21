import { useMemo, useState } from "react";
import { api } from "../api";
import type { Registro, ResumenTraza, Span } from "../contrato";
import { fechaHora, hace } from "../formato";
import { useCarga } from "../useCarga";
import { SinDatos } from "./Estados";
import SegunCarga from "./SegunCarga";

// Registros y trazas recientes de un activo (H-049, pantalla 2). Usa las rutas
// de H-016 y H-017 (permiso registros:ver). Si el almacen de registros no
// responde, la seccion lo dice y el resto de la ficha sigue igual.
// Los cuerpos de los registros se insertan como texto, nunca como HTML.

const VENTANAS = [
  { min: 15, texto: "15 min" },
  { min: 60, texto: "1 h" },
  { min: 6 * 60, texto: "6 h" },
  { min: 24 * 60, texto: "24 h" },
];

// severidad_num de OpenTelemetry
const SEVERIDADES = [
  { n: 0, texto: "Todas" },
  { n: 13, texto: "Avisos y errores" },
  { n: 17, texto: "Solo errores" },
];

function nivel(n: number): { texto: string; clase: string } {
  if (n >= 17) return { texto: n >= 21 ? "Fatal" : "Error", clase: "nivel-error" };
  if (n >= 13) return { texto: "Aviso", clase: "nivel-aviso" };
  if (n >= 9) return { texto: "Info", clase: "nivel-info" };
  return { texto: n ? "Depuracion" : "Sin nivel", clase: "nivel-info" };
}

const ms = (ns: number) => {
  const v = ns / 1e6;
  return v >= 1000 ? `${(v / 1000).toLocaleString("es-CO", { maximumFractionDigits: 2 })} s` : `${v.toLocaleString("es-CO", { maximumFractionDigits: 1 })} ms`;
};

const hora = (iso: string) => new Date(iso).toLocaleTimeString("es-CO", { hour: "2-digit", minute: "2-digit", second: "2-digit" });

// ---------------------------------------------------------------- registros

function TablaRegistros({ registros, verTraza }: { registros: Registro[]; verTraza: (id: string) => void }) {
  if (registros.length === 0) return <SinDatos titulo="Sin registros" detalle="El activo no envio registros con estos filtros en la ventana." />;
  return (
    <div className="tabla-contenedor tabla-alta">
      <table className="tabla tabla-registros">
        <caption className="solo-lector">Registros del activo, el mas reciente primero</caption>
        <thead>
          <tr>
            <th scope="col">Hora</th>
            <th scope="col">Nivel</th>
            <th scope="col">Servicio</th>
            <th scope="col">Mensaje</th>
          </tr>
        </thead>
        <tbody>
          {registros.map((r, i) => {
            const n = nivel(r.severidad_num);
            return (
              <tr key={`${r.momento}-${i}`}>
                <td title={fechaHora(r.momento)}>{hora(r.momento)}</td>
                <td>
                  <span className={`nivel ${n.clase}`}>{n.texto}</span>
                </td>
                <td>{r.service_name || <span className="apagado">sin servicio</span>}</td>
                <td className="cuerpo-registro">
                  {r.cuerpo}
                  {r.traza_id && (
                    <button type="button" className="boton-texto enlace-traza" onClick={() => verTraza(r.traza_id)}>
                      Ver su traza
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ------------------------------------------------------------------- trazas

function TablaTrazas({ trazas, elegida, verTraza }: { trazas: ResumenTraza[]; elegida: string | null; verTraza: (id: string) => void }) {
  if (trazas.length === 0) return <SinDatos titulo="Sin trazas" detalle="Ninguna traza toco este activo en la ventana." />;
  return (
    <div className="tabla-contenedor tabla-alta">
      <table className="tabla">
        <caption className="solo-lector">Trazas que tocaron el activo, la mas reciente primero</caption>
        <thead>
          <tr>
            <th scope="col">Termino</th>
            <th scope="col">Operacion raiz</th>
            <th scope="col">Duracion</th>
            <th scope="col">Spans</th>
            <th scope="col">Resultado</th>
            <th scope="col">Servicios</th>
          </tr>
        </thead>
        <tbody>
          {trazas.map((t) => (
            <tr key={t.traza_id} className={t.traza_id === elegida ? "fila-elegida" : undefined}>
              <td title={fechaHora(t.fin)}>{hora(t.fin)}</td>
              <th scope="row">
                <button type="button" className="boton-texto" aria-pressed={t.traza_id === elegida} onClick={() => verTraza(t.traza_id)}>
                  {t.raiz || "(raiz fuera de la ventana)"}
                </button>
              </th>
              <td>{ms(t.duracion_ns)}</td>
              <td>{t.spans}</td>
              <td>{t.errores ? <span className="nivel nivel-error">{t.errores} con error</span> : <span className="nivel nivel-info">Sin errores</span>}</td>
              <td>{t.servicios.join(", ")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// Los spans de una traza en arbol: cada uno sangrado bajo su padre.
function ordenarEnArbol(spans: Span[]): { s: Span; nivel: number }[] {
  const hijos = new Map<string, Span[]>();
  const ids = new Set(spans.map((s) => s.span_id));
  for (const s of spans) {
    const padre = s.padre_id && ids.has(s.padre_id) ? s.padre_id : "";
    hijos.set(padre, [...(hijos.get(padre) ?? []), s]);
  }
  const salida: { s: Span; nivel: number }[] = [];
  const visitar = (padre: string, nivel: number) => {
    for (const s of (hijos.get(padre) ?? []).sort((a, b) => a.inicio.localeCompare(b.inicio))) {
      salida.push({ s, nivel });
      visitar(s.span_id, nivel + 1);
    }
  };
  visitar("", 0);
  return salida;
}

function DetalleTraza({ id, activo, cerrar }: { id: string; activo: string; cerrar: () => void }) {
  const r = useCarga((s) => api.traza(id, s), [id]);
  return (
    <div className="detalle-traza">
      <div className="detalle-traza-cabecera">
        <h4>
          Traza <code>{id}</code>
        </h4>
        <button type="button" className="boton-texto" onClick={cerrar}>
          Cerrar
        </button>
      </div>
      <SegunCarga r={r} que="la traza">
        {(t) => {
          const arbol = ordenarEnArbol(t.spans);
          const inicio = Math.min(...t.spans.map((s) => new Date(s.inicio).getTime()));
          const total = Math.max(1, Math.max(...t.spans.map((s) => new Date(s.fin).getTime())) - inicio);
          return (
            <ol className="spans">
              {arbol.map(({ s, nivel }) => {
                const desde = ((new Date(s.inicio).getTime() - inicio) / total) * 100;
                const ancho = Math.max(0.5, (s.duracion_ns / 1e6 / total) * 100);
                const otro = s.zenit_asset_id !== activo;
                return (
                  <li key={s.span_id} className={s.estado === "error" ? "span span-error" : "span"} style={{ paddingLeft: `${nivel * 16}px` }}>
                    <span className="span-nombre">
                      {s.estado === "error" && <strong>Error · </strong>}
                      {s.nombre} <span className="meta-dato-linea">
                        {s.service_name}
                        {otro ? ` en ${s.zenit_asset_id}` : ""} · {ms(s.duracion_ns)}
                        {s.estado_mensaje ? ` · ${s.estado_mensaje}` : ""}
                      </span>
                    </span>
                    <span className="span-barra" aria-hidden="true">
                      <span style={{ marginLeft: `${desde}%`, width: `${ancho}%` }} />
                    </span>
                  </li>
                );
              })}
            </ol>
          );
        }}
      </SegunCarga>
    </div>
  );
}

// -------------------------------------------------------------------- todo

export default function RegistrosYTrazas({ activo }: { activo: string }) {
  const [minutos, setMinutos] = useState(60);
  const [severidad, setSeveridad] = useState(0);
  const [texto, setTexto] = useState("");
  const [buscar, setBuscar] = useState("");
  const [soloErrores, setSoloErrores] = useState(false);
  const [traza, setTraza] = useState<string | null>(null);
  const [vuelta, setVuelta] = useState(0);
  // la ventana se fija al elegirla o al pulsar "actualizar", no en cada render
  const ventana = useMemo(() => {
    const hasta = new Date();
    return { desde: new Date(hasta.getTime() - minutos * 60_000), hasta };
  }, [minutos, vuelta]);

  const reg = useCarga(
    (s) => api.registros(activo, ventana.desde, ventana.hasta, { severidadMinima: severidad, texto: buscar }, s),
    [activo, ventana, severidad, buscar],
  );
  const tra = useCarga((s) => api.trazas(activo, ventana.desde, ventana.hasta, soloErrores, s), [activo, ventana, soloErrores]);

  const verTraza = (id: string) => {
    setTraza(id);
    window.requestAnimationFrame(() => document.getElementById("titulo-trazas")?.scrollIntoView({ block: "start", behavior: "smooth" }));
  };

  return (
    <section className="panel panel-blanco registros-trazas" aria-labelledby="titulo-registros">
      <div className="registros-cabecera">
        <h3 id="titulo-registros">Registros y trazas recientes</h3>
        <div className="selector-ventana" role="group" aria-label="Ventana">
          {VENTANAS.map((v) => (
            <button key={v.min} type="button" className={v.min === minutos ? "chip activo" : "chip"} aria-pressed={v.min === minutos} onClick={() => setMinutos(v.min)}>
              {v.texto}
            </button>
          ))}
          <button type="button" className="boton-texto" onClick={() => setVuelta((n) => n + 1)}>
            Actualizar
          </button>
        </div>
      </div>
      <p className="nota">
        {fechaHora(ventana.desde.toISOString())} a {fechaHora(ventana.hasta.toISOString())} ({hace(ventana.desde.toISOString())} hasta ahora)
      </p>

      <h4>Registros</h4>
      <form
        className="filtros-inventario"
        onSubmit={(e) => {
          e.preventDefault();
          setBuscar(texto.trim());
        }}
      >
        <label>
          Nivel
          <select value={severidad} onChange={(e) => setSeveridad(Number(e.target.value))}>
            {SEVERIDADES.map((s) => (
              <option key={s.n} value={s.n}>
                {s.texto}
              </option>
            ))}
          </select>
        </label>
        <label>
          Contiene
          <input type="search" value={texto} maxLength={200} onChange={(e) => setTexto(e.target.value)} placeholder="texto del mensaje" />
        </label>
        <button type="submit" className="boton-secundario">
          Buscar
        </button>
      </form>
      <SegunCarga r={reg} que="los registros">
        {(d) => (
          <>
            <TablaRegistros registros={d.registros} verTraza={verTraza} />
            {d.registros.length === 200 && <p className="nota">Se muestran los 200 mas recientes. Acorta la ventana o filtra para ver otros.</p>}
          </>
        )}
      </SegunCarga>

      <h4 id="titulo-trazas">Trazas</h4>
      <label className="casilla">
        <input type="checkbox" checked={soloErrores} onChange={(e) => setSoloErrores(e.target.checked)} /> Solo las que tienen errores
      </label>
      <SegunCarga r={tra} que="las trazas">
        {(d) => <TablaTrazas trazas={d.trazas} elegida={traza} verTraza={verTraza} />}
      </SegunCarga>
      {traza && <DetalleTraza key={traza} id={traza} activo={activo} cerrar={() => setTraza(null)} />}
    </section>
  );
}
