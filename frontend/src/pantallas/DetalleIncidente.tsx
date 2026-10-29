import { useEffect, useMemo, useRef, useState } from "react";
import { api, ErrorApi } from "../api";
import EvidenciaSenales from "../componentes/EvidenciaSenales";
import { ErrorVista, SinDatos } from "../componentes/Estados";
import { InsigniaIncidente, InsigniaSeveridad } from "../componentes/Insignias";
import Marco from "../componentes/Marco";
import SegunCarga from "../componentes/SegunCarga";
import VistaSerie from "../componentes/VistaSerie";
import type { DetalleIncidente as Detalle, PeticionEstado } from "../contrato";
import { Enlace } from "../enrutador";
import { ABIERTO, claseDe, duracion, fechaHora, fechaValida, formatoValor, hace, nombreMetrica, rangoFechas, tituloIncidente } from "../formato";
import { useSesion } from "../sesion";
import { useCarga } from "../useCarga";

// Pantalla 4, la que define el proyecto. El diagnostico es una HIPOTESIS: se
// dice con esa palabra y con la advertencia, y el camino del ojo sigue de la
// explicacion a la evidencia, que esta al lado y no detras de un enlace.
// El texto del modelo se inserta como texto: nunca como HTML.

const MARGEN_MS = 10 * 60_000;
const REVISAR_COLA_MS = 15_000;

// Un elemento de la lista de evidencia: una metrica del activo en una ventana.
// `aproximada`: la cita no trae un intervalo legible y se usa la ventana del detector.
type Pieza = {
  clave: string;
  metrica: string;
  desde: string;
  hasta: string;
  origen: string;
  observacion?: string;
  aproximada?: boolean;
};

function piezas(d: Detalle): Pieza[] {
  const { desde, hasta } = d.evidencia.ventana;
  const citadas: Pieza[] = (d.diagnostico?.evidencia_citada ?? []).map((e, i) => {
    const legible = fechaValida(e.desde) && fechaValida(e.hasta);
    return {
      clave: `citada-${i}`,
      metrica: e.metrica,
      desde: legible ? e.desde : desde,
      hasta: legible ? e.hasta : hasta,
      origen: "Citada por la hipótesis",
      observacion: e.observacion,
      aproximada: !legible,
    };
  });
  const detector: Pieza[] = [d.metrica, ...d.evidencia.metricas_relacionadas]
    .filter((m, i, todas) => todas.indexOf(m) === i)
    .map((m) => ({
      clave: `detector-${m}`,
      metrica: m,
      desde,
      hasta,
      origen: m === d.metrica ? "La métrica que se desvió" : "Relacionada, según el detector",
    }));
  return [...citadas, ...detector];
}

// La serie de la metrica que se desvio abre filtrada con las dimensiones del
// incidente (device=sda1, direction=write...). Las relacionadas son otras
// metricas: esas dimensiones no les aplican.
function VistaPieza({ activo, p, filtros }: { activo: string; p: Pieza; filtros?: Record<string, string> }) {
  const ventana = useMemo(
    () => ({ desde: new Date(new Date(p.desde).getTime() - MARGEN_MS), hasta: new Date(new Date(p.hasta).getTime() + MARGEN_MS) }),
    [p.desde, p.hasta],
  );
  return (
    <VistaSerie
      activo={activo}
      metrica={p.metrica}
      ventana={ventana}
      filtros={filtros}
      titulo={`${nombreMetrica(p.metrica)} en ${activo}`}
    />
  );
}

const numeroCorto = (v: number | null) => (v == null ? "sin dato" : v.toLocaleString("es-CO", { maximumFractionDigits: 3 }));

function Contenido({ d, actualizar }: { d: Detalle; actualizar: (d: Detalle) => void }) {
  const { puede } = useSesion();
  const gestiona = puede("incidentes:gestionar");
  const lista = piezas(d);
  const [elegida, setElegida] = useState<string | null>(lista[0]?.clave ?? null);
  const [marcando, setMarcando] = useState<PeticionEstado["estado"] | null>(null);
  const [pidiendo, setPidiendo] = useState(false);
  const [enCola, setEnCola] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hecho, setHecho] = useState<string | null>(null);
  // H-054: el boton pulsado desaparece al marcar; el foco pasa al aviso
  const aviso = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    if (hecho || error) aviso.current?.focus();
  }, [hecho, error]);

  // Si el diagnostico quedo en cola (202), se vuelve a pedir el incidente
  // hasta que llegue. No hay otro aviso en el contrato.
  useEffect(() => {
    if (!enCola) return;
    const t = window.setInterval(() => {
      api
        .incidente(d.id)
        .then((nuevo) => {
          if (nuevo.diagnostico && nuevo.diagnostico.generado !== d.diagnostico?.generado) {
            setEnCola(false);
            actualizar(nuevo);
          }
        })
        .catch(() => {
          /* se intenta en la siguiente vuelta */
        });
    }, REVISAR_COLA_MS);
    return () => window.clearInterval(t);
  }, [enCola, d.id, d.diagnostico?.generado, actualizar]);

  const marcar = async (estado: PeticionEstado["estado"]) => {
    setMarcando(estado);
    setError(null);
    setHecho(null);
    try {
      const r = await api.cambiarEstado(d.id, estado);
      actualizar({ ...d, estado: r.estado, fin: r.fin, marcada_por: r.marcada_por, marcada_en: r.marcada_en });
      setHecho(estado === "confirmada" ? "Incidente confirmado como real." : "Incidente marcado como falso positivo.");
    } catch (e) {
      setError(
        e instanceof ErrorApi && e.estado === 409
          ? "Este incidente ya estaba cerrado o descartado. Recarga la página para ver su estado actual."
          : `No se pudo marcar: ${e instanceof Error ? e.message : e}`,
      );
    } finally {
      setMarcando(null);
    }
  };

  const pedirDiagnostico = async () => {
    setPidiendo(true);
    setError(null);
    try {
      const diag = await api.pedirDiagnostico(d.id);
      if (diag) actualizar({ ...d, diagnostico: diag, estado: d.estado === "detectada" ? "diagnosticada" : d.estado });
      else setEnCola(true);
    } catch (e) {
      setError(
        e instanceof ErrorApi && e.estado === 403
          ? "Tu rol no puede pedir diagnósticos."
          : e instanceof ErrorApi && (e.estado === 404 || e.estado === 405)
            ? "El servicio de diagnóstico todavía no está disponible. La evidencia de abajo no depende de él."
            : `No se pudo pedir la hipótesis: ${e instanceof Error ? e.message : e}`,
      );
    } finally {
      setPidiendo(false);
    }
  };

  const elegir = (clave: string) => {
    setElegida(clave);
    document.getElementById(`evidencia-${clave}`)?.focus();
  };

  const actual = lista.find((p) => p.clave === elegida) ?? null;
  const ev = d.evidencia;
  const clase = claseDe(d.metrica, ev.clase);
  // un falso positivo no "sigue" aunque el detector no le haya puesto fin:
  // para el operador termino cuando lo descarto
  const sigue = !d.fin && ABIERTO.includes(d.estado);
  const finEfectivo = d.fin ?? (sigue ? null : d.marcada_en);
  const dims = Object.entries(d.dimensiones ?? {});
  const citadas = d.diagnostico?.evidencia_citada.length ?? 0;
  return (
    <>
      <Enlace a="/incidentes" className="volver">
        ← Incidentes
      </Enlace>
      <header className="cabecera-incidente">
        <div>
          <p className="insignias">
            <InsigniaIncidente estado={d.estado} /> <InsigniaSeveridad severidad={d.severidad} />
          </p>
          <h2>{tituloIncidente(d)}</h2>
          <p className="meta">
            En <Enlace a={`/activos/${encodeURIComponent(d.activo)}`}>{d.activo}</Enlace> · empezó {fechaHora(d.inicio)} ({hace(d.inicio)})
            · {sigue ? "dura" : "duro"} {duracion(d.inicio, finEfectivo)}
            {sigue ? " y sigue" : ""} · puntaje {numeroCorto(d.puntaje)}
          </p>
          {d.marcada_por && d.marcada_en && (
            <p className="meta">
              {d.estado === "falso_positivo" ? "Descartado" : "Marcado"} por {d.marcada_por} {hace(d.marcada_en)}
            </p>
          )}
        </div>
        {gestiona && ABIERTO.includes(d.estado) && (
          <div className="acciones">
            {d.estado !== "confirmada" && (
              <button type="button" className="boton-primario" disabled={!!marcando} onClick={() => void marcar("confirmada")}>
                {marcando === "confirmada" ? "Confirmando..." : "Confirmar que es real"}
              </button>
            )}
            <button type="button" className="boton-secundario" disabled={!!marcando} onClick={() => void marcar("falso_positivo")}>
              {marcando === "falso_positivo" ? "Marcando..." : "Es un falso positivo"}
            </button>
          </div>
        )}
      </header>
      <p ref={aviso} tabIndex={-1} className={error ? "mensaje-error aviso-foco" : "mensaje-ok aviso-foco"} role="status">
        {error ?? hecho ?? ""}
      </p>

      <section className="panel panel-blanco anomalia" aria-labelledby="titulo-anomalia">
        <h3 id="titulo-anomalia">Qué se desvió</h3>
        <dl className="cifras-anomalia">
          <div>
            <dt>Métrica</dt>
            <dd>
              {nombreMetrica(d.metrica)}
              {dims.length > 0 && <span className="meta-dato">{dims.map(([k, v]) => `${k}=${v}`).join(", ")}</span>}
              {clase === "acumulada" && <span className="meta-dato">tasa por segundo</span>}
            </dd>
          </div>
          <div>
            <dt>Observado</dt>
            <dd>{formatoValor(d.metrica, ev.valor_observado, clase)}</dd>
          </div>
          <div>
            <dt>Esperado</dt>
            <dd>{formatoValor(d.metrica, ev.valor_esperado, clase)}</dd>
          </div>
          <div>
            <dt>Ventana</dt>
            <dd>
              {rangoFechas(ev.ventana.desde, ev.ventana.hasta)}
            </dd>
          </div>
        </dl>
        <p className="meta">
          Método del detector: {ev.metodo}
          {d.version_modelo && d.version_modelo !== ev.metodo ? ` · versión ${d.version_modelo}` : ""}
        </p>
      </section>

      <div className="rejilla-incidente">
        <section className="panel panel-azul hipotesis" aria-labelledby="titulo-hipotesis">
          <div className="hipotesis-cabecera">
            <h3 id="titulo-hipotesis">Hipótesis de Zenit</h3>
          </div>
          {d.diagnostico ? (
            <>
              <p className="hipotesis-texto">{d.diagnostico.texto}</p>
              <p className="advertencia">
                Es una posible explicación generada por un modelo, no una conclusión. Revisa la evidencia antes de actuar.
              </p>
              {citadas > 0 && (
                <>
                  <p className="ir-evidencia">
                    Se apoya en {citadas} {citadas === 1 ? "evidencia" : "evidencias"}:
                  </p>
                  <ol className="citas">
                    {lista
                      .filter((p) => p.clave.startsWith("citada-"))
                      .map((p, i) => (
                        <li key={p.clave}>
                          <button type="button" className="boton-texto" onClick={() => elegir(p.clave)}>
                            {i + 1}. {nombreMetrica(p.metrica)}, {rangoFechas(p.desde, p.hasta)} →
                          </button>
                        </li>
                      ))}
                  </ol>
                </>
              )}
              <p className="hipotesis-pie">
                {d.diagnostico.modelo} · {hace(d.diagnostico.generado)}
              </p>
            </>
          ) : (
            <p className="hipotesis-texto">
              {enCola
                ? "La hipótesis está en cola. Esta página la muestra cuando llegue."
                : "Todavía no hay una hipótesis para este incidente. La evidencia del detector está al lado."}
            </p>
          )}
          {gestiona && !enCola && (
            <button type="button" className="boton-secundario" disabled={pidiendo} onClick={() => void pedirDiagnostico()}>
              {pidiendo ? "Pidiendo..." : d.diagnostico ? "Generar otra hipótesis" : "Pedir hipótesis"}
            </button>
          )}
        </section>

        <section className="panel panel-amarillo evidencia" aria-labelledby="titulo-evidencia">
          <h3 id="titulo-evidencia">Evidencia</h3>
          {/* dos tandas: lo que cita la hipotesis y lo que senalo el detector.
              Las metricas del detector suelen repetir las citadas, por eso van
              aparte y mas compactas; la numeracion sigue siendo una sola. */}
          {[
            { titulo: "Citadas por la hipótesis", citadas: true },
            { titulo: citadas > 0 ? "Del detector" : null, citadas: false },
          ].map((g) => {
            const suyas = lista.filter((p) => p.clave.startsWith("citada-") === g.citadas);
            if (suyas.length === 0) return null;
            return (
              <div key={String(g.citadas)} className="tanda-evidencia">
                {g.titulo && <h4 className="tanda-titulo">{g.titulo}</h4>}
                <ol className="lista-evidencia" start={lista.indexOf(suyas[0]) + 1}>
                  {suyas.map((p) => {
                    const i = lista.indexOf(p);
                    return (
                      <li key={p.clave}>
                        <button
                          id={`evidencia-${p.clave}`}
                          type="button"
                          className={`item-evidencia${g.citadas ? "" : " item-evidencia-compacta"}${p.clave === elegida ? " elegida" : ""}`}
                          aria-pressed={p.clave === elegida}
                          onClick={() => setElegida(p.clave)}
                        >
                          <span className="numero" aria-hidden="true">
                            {i + 1}
                          </span>
                          <span>
                            {g.citadas ? <span className="solo-lector">{p.origen}. </span> : <span className="tipo">{p.origen}</span>}
                            <span className="descripcion">
                              {nombreMetrica(p.metrica)}, {rangoFechas(p.desde, p.hasta)}
                              {p.aproximada ? " (la cita no trae intervalo; ventana del detector)" : ""}
                            </span>
                            {p.observacion && <span className="descripcion observacion">{p.observacion}</span>}
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ol>
              </div>
            );
          })}
          <p className="nota">
            <a href="#titulo-ev-senales">Registros y trazas de la ventana, más abajo ↓</a>
          </p>
        </section>
      </div>

      {actual && (
        <section className="panel panel-blanco detalle-evidencia" aria-label={`Evidencia ${lista.indexOf(actual) + 1}`}>
          <h3>
            Evidencia {lista.indexOf(actual) + 1} · {nombreMetrica(actual.metrica)}
          </h3>
          {actual.observacion && <p className="observacion-citada">{actual.observacion}</p>}
          <p className="meta">
            {d.activo} · {rangoFechas(actual.desde, actual.hasta)}, con 10 min de margen a cada lado
          </p>
          <VistaPieza
            key={actual.clave}
            activo={d.activo}
            p={actual}
            filtros={actual.metrica === d.metrica ? d.dimensiones : undefined}
          />
        </section>
      )}

      <EvidenciaSenales id={d.id} />
    </>
  );
}

export default function DetalleIncidente({ id }: { id: string }) {
  const n = Number(id);
  const valido = Number.isInteger(n) && n > 0;
  // H-054: se refresca cada 30 s (asi existe el estado "desactualizado" si un
  // refresco falla) y un 404 es "no existe", no un error de carga
  const r = useCarga(
    (s) =>
      valido
        ? api.incidente(n, s).catch((e) => {
            if (e instanceof ErrorApi && e.estado === 404) return null;
            throw e;
          })
        : Promise.reject(new Error("no existe")),
    [n, valido],
    30_000,
  );
  const { reemplazar } = r;
  const actualizar = useMemo(() => (nuevo: Detalle) => reemplazar(() => nuevo), [reemplazar]);
  return (
    <Marco titulo={`Incidente ${valido ? n : ""}`}>
      {!valido ? (
        <ErrorVista mensaje={`"${id}" no es un número de incidente.`} />
      ) : (
        <SegunCarga r={r} que="el incidente">
          {(d) =>
            d ? (
              <Contenido key={d.id} d={d} actualizar={actualizar} />
            ) : (
              <SinDatos titulo={`No hay un incidente ${n}`} detalle="Puede que el número esté mal o que el incidente se haya borrado." />
            )
          }
        </SegunCarga>
      )}
    </Marco>
  );
}
