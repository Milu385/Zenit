import { useState } from "react";
import ConColumna from "../componentes/ColumnaDerecha";
import ConsumoDiagnostico from "../componentes/ConsumoDiagnostico";
import { api } from "../api";
import { SinDatos } from "../componentes/Estados";
import Marco from "../componentes/Marco";
import SegunCarga from "../componentes/SegunCarga";
import type { Descubrimientos, EjecucionDescubrimiento, EstadoFuente, Fuente, Huerfana, Senal } from "../contrato";
import { Enlace } from "../enrutador";
import { datosProveedor, duracion, ESTADO_ACTIVO, fechaHora, hace } from "../formato";
import { useSesion } from "../sesion";
import { useCarga, type ResultadoCarga } from "../useCarga";

// Pantalla 7 (H-053), solo para quien tiene fuentes:ver (administrador).
// Responde tres preguntas: que fuentes dejaron de reportar, que telemetria
// llega sin poder asociarse a un activo y cuando paso el descubrimiento por
// ultima vez en cada proveedor. La regla de degradado (mas de 5 min sin
// metricas) la aplica la API; aqui solo se muestra.

const REFRESCO_MS = 30_000;
const SENALES: Senal[] = ["metricas", "registros", "trazas"];
const NOMBRE_SENAL: Record<Senal, string> = { metricas: "Métricas", registros: "Registros", trazas: "Trazas" };
const ORDEN: Record<EstadoFuente, number> = { degradado: 0, sin_datos: 1, ok: 2 };

const listo = <T,>(r: ResultadoCarga<T>) => (r.carga.estado === "listo" ? r.carga.datos : null);
const entero = (n: number) => n.toLocaleString("es-CO");

function InsigniaFuente({ estado }: { estado: EstadoFuente }) {
  const e = ESTADO_ACTIVO[estado];
  return (
    <span className={`insignia insignia-${estado}`}>
      <span aria-hidden="true">{e.simbolo}</span> {e.texto}
    </span>
  );
}

// ---------------------------------------------------------------- fuentes

function TablaFuentes({ fuentes }: { fuentes: Fuente[] }) {
  const [todas, setTodas] = useState(false);
  // las huerfanas tienen su propia seccion
  const propias = fuentes.filter((f) => !f.huerfana);
  const conProblema = propias.filter((f) => f.estado !== "ok");
  const visibles = (todas ? propias : conProblema).slice().sort((a, b) => ORDEN[a.estado] - ORDEN[b.estado] || a.activo.localeCompare(b.activo));
  return (
    <section className="panel panel-amarillo" aria-labelledby="titulo-fuentes">
      <h2 id="titulo-fuentes">
        {todas ? "Todas las fuentes" : "Fuentes que no reportan"} <span className="cuenta">{visibles.length}</span>
      </h2>
      <p className="nota">
        Una fuente queda degradada con más de 5 minutos sin recibir métricas. Sin datos: nunca reportó. En cada señal: la última vez
        que llegó · cuántas en los últimos 5 min, y debajo el total recibido.
      </p>
      <label className="casilla">
        <input type="checkbox" checked={todas} onChange={(e) => setTodas(e.target.checked)} /> Mostrar también las que reportan bien (
        {propias.length - conProblema.length})
      </label>
      {visibles.length === 0 ? (
        <SinDatos
          titulo={propias.length === 0 ? "No hay fuentes registradas" : "Todas las fuentes están reportando"}
          detalle={propias.length === 0 ? "La ingesta todavía no recibió telemetría de ningún activo del catálogo." : "Ninguna lleva más de 5 minutos sin enviar métricas."}
        />
      ) : (
        <div className="tabla-contenedor">
          <table className="tabla">
            <caption className="solo-lector">Fuentes de telemetría, las degradadas primero</caption>
            <thead>
              <tr>
                <th scope="col">Estado</th>
                <th scope="col">Activo</th>
                <th scope="col">Última recepción</th>
                {SENALES.map((s) => (
                  <th key={s} scope="col">
                    {NOMBRE_SENAL[s]}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {visibles.map((f) => (
                <tr key={`${f.activo}|${f.host_name}`}>
                  <td>
                    <InsigniaFuente estado={f.estado} />
                  </td>
                  <th scope="row">
                    <Enlace a={`/activos/${encodeURIComponent(f.activo)}`}>{f.activo}</Enlace>
                    {/* el proveedor va debajo del activo, no en su propia columna: la tabla cabe sin desplazarse */}
                    <span className="meta-dato">
                      {f.proveedor ? datosProveedor(f.proveedor).nombre : "proveedor sin dato"} · {f.host_name}
                    </span>
                  </th>
                  <td title={f.ultima_recepcion ? fechaHora(f.ultima_recepcion) : undefined}>
                    {f.ultima_recepcion ? hace(f.ultima_recepcion) : "nunca"}
                    {f.minutos_sin_datos != null && f.estado !== "ok" && (
                      <span className="meta-dato">{entero(Math.round(f.minutos_sin_datos))} min sin datos</span>
                    )}
                  </td>
                  {SENALES.map((s) => {
                    const x = f.senales[s];
                    if (!x)
                      return (
                        <td key={s}>
                          <span className="apagado">no envia</span>
                        </td>
                      );
                    return (
                      <td key={s} title={fechaHora(x.ultima_recepcion)}>
                        {hace(x.ultima_recepcion)} · {entero(x.recibidas_5min)}
                        <span className="meta-dato">
                          {entero(x.recibidas_total)} en total
                          {x.errores_total > 0 ? ` · ${entero(x.errores_total)} errores en total` : ""}
                        </span>
                        {x.errores_1h > 0 && <span className="meta-dato texto-mal">{entero(x.errores_1h)} errores en 1 h</span>}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// -------------------------------------------------------------- huerfanos

function situacion(h: Huerfana) {
  if (h.asociada_a) return { texto: `Asociada a ${h.asociada_a}`, ayuda: h.asociada_en ? `Reasignada ${hace(h.asociada_en)}` : undefined };
  if (h.resuelve_a) return { texto: `Asociable a ${h.resuelve_a}`, ayuda: "La clave ya esta en el catálogo; falta reasignar la telemetría previa" };
  return { texto: "Sin activo en el catálogo", ayuda: "Ningún activo tiene esta clave" };
}

function TablaHuerfanos({ huerfanas }: { huerfanas: Huerfana[] }) {
  const pendientes = huerfanas.filter((h) => !h.asociada_a);
  return (
    <section className="panel panel-blanco" aria-labelledby="titulo-huerfanos">
      <h2 id="titulo-huerfanos">
        Telemetría huérfana <span className="cuenta">{pendientes.length}</span>
      </h2>
      <p className="nota">Orígenes que enviaron datos que no se pudieron asociar a ningún activo. El volumen es lo acumulado desde la primera recepción.</p>
      {huerfanas.length === 0 ? (
        <SinDatos titulo="Sin huérfanos" detalle="Toda la telemetría recibida se asoció a un activo del catálogo." />
      ) : (
        <div className="tabla-contenedor">
          <table className="tabla">
            <caption className="solo-lector">Orígenes huérfanos, el de recepción más reciente primero</caption>
            <thead>
              <tr>
                <th scope="col">Origen</th>
                <th scope="col">Proveedor declarado</th>
                <th scope="col">Volumen</th>
                <th scope="col">Última recepción</th>
                <th scope="col">Desde</th>
                <th scope="col">Situación</th>
              </tr>
            </thead>
            <tbody>
              {huerfanas.map((h) => {
                const s = situacion(h);
                return (
                  <tr key={h.clave} className={h.asociada_a ? "fila-atenuada" : undefined}>
                    <th scope="row">
                      {h.clave}
                      <span className="meta-dato">{h.tipo_clave}</span>
                    </th>
                    <td>{h.proveedor ? datosProveedor(h.proveedor).nombre : <span className="apagado">no lo declara</span>}</td>
                    <td>
                      {entero(h.metricas)} métricas
                      <span className="meta-dato">
                        {entero(h.registros)} registros · {entero(h.trazas)} trazas
                      </span>
                    </td>
                    <td title={fechaHora(h.ultima)}>{hace(h.ultima)}</td>
                    <td title={fechaHora(h.primera)}>{hace(h.primera)}</td>
                    <td title={s.ayuda}>{s.texto}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------- descubrimiento

const ESTADO_EJECUCION: Record<EjecucionDescubrimiento["estado"], { texto: string; simbolo: string; clase: string }> = {
  ok: { texto: "Correcta", simbolo: "✓", clase: "insignia-ok" },
  error: { texto: "Fallo", simbolo: "✕", clase: "insignia-degradado" },
  en_curso: { texto: "En curso", simbolo: "…", clase: "insignia-sin_datos" },
};

function FilaEjecucion({ e }: { e: EjecucionDescubrimiento }) {
  const est = ESTADO_EJECUCION[e.estado];
  return (
    <tr>
      <th scope="row">{datosProveedor(e.proveedor).nombre}</th>
      <td>
        <span className={`insignia ${est.clase}`}>
          <span aria-hidden="true">{est.simbolo}</span> {est.texto}
        </span>
        {e.error && <span className="meta-dato texto-mal">{e.error}</span>}
      </td>
      <td title={fechaHora(e.inicio)}>{hace(e.inicio)}</td>
      <td>{e.fin ? duracion(e.inicio, e.fin) : "sigue"}</td>
      <td>{entero(e.vistos)}</td>
      <td>{entero(e.nuevos)}</td>
      <td>{entero(e.retirados)}</td>
      <td>{entero(e.reactivados)}</td>
    </tr>
  );
}

const CABECERA_EJECUCION = (
  <thead>
    <tr>
      <th scope="col">Proveedor</th>
      <th scope="col">Resultado</th>
      <th scope="col">Empezó</th>
      <th scope="col">Duró</th>
      <th scope="col">Vistos</th>
      <th scope="col">Nuevos</th>
      <th scope="col">Retirados</th>
      <th scope="col">Reactivados</th>
    </tr>
  </thead>
);

// Historial: las pasadas mas recientes de todos los proveedores juntos, la
// mas nueva primero. Sirve para ver si un fallo es de una vez o se repite.
function HistorialDescubrimiento({ d }: { d: Descubrimientos }) {
  const recientes = d.recientes.slice().sort((a, b) => b.inicio.localeCompare(a.inicio));
  if (recientes.length === 0) return null;
  const fallidas = recientes.filter((e) => e.estado === "error").length;
  return (
    <section className="panel panel-blanco" aria-labelledby="titulo-historial">
      <h2 id="titulo-historial">
        Pasadas recientes del descubrimiento <span className="cuenta">{recientes.length}</span>
      </h2>
      <p className="nota">
        Todos los proveedores, la más reciente primero{fallidas ? `; ${fallidas} ${fallidas === 1 ? "falló" : "fallaron"}` : ""}.
      </p>
      <div className="tabla-contenedor tabla-alta">
        <table className="tabla">
          <caption className="solo-lector">Historial de ejecuciones del descubrimiento</caption>
          {CABECERA_EJECUCION}
          <tbody>
            {recientes.map((e) => (
              <FilaEjecucion key={e.id} e={e} />
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function TablaDescubrimiento({ d }: { d: Descubrimientos }) {
  const ultimas = d.ultimas.slice().sort((a, b) => a.proveedor.localeCompare(b.proveedor));
  return (
    <section className="panel panel-blanco" aria-labelledby="titulo-descubrimiento">
      <h2 id="titulo-descubrimiento">Última pasada del descubrimiento</h2>
      {ultimas.length === 0 ? (
        <SinDatos titulo="El descubrimiento no ha corrido" detalle="No hay ninguna ejecución registrada para ningún proveedor." />
      ) : (
        <div className="tabla-contenedor">
          <table className="tabla">
            <caption className="solo-lector">La última ejecución del descubrimiento en cada proveedor</caption>
            {CABECERA_EJECUCION}
            <tbody>
              {ultimas.map((e) => (
                <FilaEjecucion key={e.id} e={e} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function Cifras({ fuentes, huerfanas, d }: { fuentes: Fuente[] | null; huerfanas: Huerfana[] | null; d: Descubrimientos | null }) {
  const propias = fuentes?.filter((f) => !f.huerfana) ?? null;
  const cifras = [
    { n: propias?.filter((f) => f.estado === "degradado").length, texto: "Fuentes degradadas" },
    { n: propias?.filter((f) => f.estado === "sin_datos").length, texto: "Sin datos nunca" },
    { n: huerfanas?.filter((h) => !h.asociada_a).length, texto: "Orígenes huérfanos" },
    { n: d?.ultimas.filter((e) => e.estado === "error").length, texto: "Proveedores con descubrimiento fallido" },
  ];
  return (
    <ul className="cifras cifras-cuatro" aria-label="Resumen de fuentes">
      {cifras.map((c) => (
        <li key={c.texto} className={c.n ? "cifra-alerta" : undefined}>
          <span className="cifra">{c.n ?? "–"}</span>
          <span>{c.texto}</span>
        </li>
      ))}
    </ul>
  );
}

export default function Fuentes() {
  const { puede } = useSesion();
  const f = useCarga((s) => api.fuentes(s), [], REFRESCO_MS);
  const h = useCarga((s) => api.huerfanas(s), [], REFRESCO_MS);
  const d = useCarga((s) => api.descubrimientos(s), [], REFRESCO_MS);
  return (
    <Marco titulo="Fuentes">
      <ConColumna>
        <Cifras fuentes={listo(f)} huerfanas={listo(h)} d={listo(d)} />
        <SegunCarga r={f} que="las fuentes">
          {(x) => <TablaFuentes fuentes={x} />}
        </SegunCarga>
        <SegunCarga r={h} que="los huérfanos">
          {(x) => <TablaHuerfanos huerfanas={x} />}
        </SegunCarga>
        <SegunCarga r={d} que="el descubrimiento">
          {(x) => (
            <>
              <TablaDescubrimiento d={x} />
              <HistorialDescubrimiento d={x} />
            </>
          )}
        </SegunCarga>
        {puede("diagnostico:consumo") && <ConsumoDiagnostico />}
      </ConColumna>
    </Marco>
  );
}
