import { useEffect, useMemo, useState } from "react";
import { api, ErrorApi } from "../api";
import { ErrorVista } from "../componentes/Estados";
import { InsigniaIncidente, InsigniaSeveridad } from "../componentes/Insignias";
import Marco from "../componentes/Marco";
import SegunCarga from "../componentes/SegunCarga";
import VistaSerie from "../componentes/VistaSerie";
import type { DetalleIncidente as Detalle, PeticionEstado } from "../contrato";
import { Enlace } from "../enrutador";
import { ABIERTO, duracion, fechaHora, hace, nombreMetrica, tituloIncidente } from "../formato";
import { useSesion } from "../sesion";
import { useCarga } from "../useCarga";

// Pantalla 4, la que define el proyecto. El diagnostico es una HIPOTESIS: se
// dice con esa palabra y con la advertencia, y el camino del ojo sigue de la
// explicacion a la evidencia, que esta al lado y no detras de un enlace.
// El texto del modelo se inserta como texto: nunca como HTML.

const MARGEN_MS = 10 * 60_000;
const REVISAR_COLA_MS = 15_000;

// Un elemento de la lista de evidencia: una metrica del activo en una ventana.
type Pieza = { clave: string; metrica: string; desde: string; hasta: string; origen: string; observacion?: string };

function piezas(d: Detalle): Pieza[] {
  const citadas: Pieza[] = (d.diagnostico?.evidencia_citada ?? []).map((e, i) => ({
    clave: `citada-${i}`,
    metrica: e.metrica,
    desde: e.desde,
    hasta: e.hasta,
    origen: "Citada por la hipotesis",
    observacion: e.observacion,
  }));
  const { desde, hasta } = d.evidencia.ventana;
  const detector: Pieza[] = [d.metrica, ...d.evidencia.metricas_relacionadas]
    .filter((m, i, todas) => todas.indexOf(m) === i)
    .map((m) => ({
      clave: `detector-${m}`,
      metrica: m,
      desde,
      hasta,
      origen: m === d.metrica ? "La metrica que se desvio" : "Relacionada, segun el detector",
    }));
  return [...citadas, ...detector];
}

function VistaPieza({ activo, p }: { activo: string; p: Pieza }) {
  const ventana = useMemo(
    () => ({ desde: new Date(new Date(p.desde).getTime() - MARGEN_MS), hasta: new Date(new Date(p.hasta).getTime() + MARGEN_MS) }),
    [p.desde, p.hasta],
  );
  return <VistaSerie activo={activo} metrica={p.metrica} ventana={ventana} titulo={`${nombreMetrica(p.metrica)} en ${activo}`} />;
}

const numeroCorto = (v: number) => v.toLocaleString("es-CO", { maximumFractionDigits: 3 });

function Contenido({ d, actualizar }: { d: Detalle; actualizar: (d: Detalle) => void }) {
  const { puede } = useSesion();
  const gestiona = puede("incidentes:gestionar");
  const lista = piezas(d);
  const [elegida, setElegida] = useState<string | null>(lista[0]?.clave ?? null);
  const [marcando, setMarcando] = useState<PeticionEstado["estado"] | null>(null);
  const [pidiendo, setPidiendo] = useState(false);
  const [enCola, setEnCola] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
    try {
      await api.cambiarEstado(d.id, estado);
      actualizar({ ...d, estado });
    } catch (e) {
      setError(`No se pudo marcar: ${e instanceof Error ? e.message : e}`);
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
          ? "Tu rol no puede pedir diagnosticos."
          : `No se pudo pedir la hipotesis: ${e instanceof Error ? e.message : e}`,
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
            En <Enlace a={`/activos/${encodeURIComponent(d.activo)}`}>{d.activo}</Enlace> · empezo {fechaHora(d.inicio)} ({hace(d.inicio)})
            · dura {duracion(d.inicio, d.fin)}
            {d.fin ? "" : " y sigue"} · puntaje {numeroCorto(d.puntaje)}
          </p>
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
      {error && (
        <p className="mensaje-error" role="alert">
          {error}
        </p>
      )}

      <section className="panel panel-blanco anomalia" aria-labelledby="titulo-anomalia">
        <h3 id="titulo-anomalia">Que se desvio</h3>
        <dl className="cifras-anomalia">
          <div>
            <dt>Metrica</dt>
            <dd>{nombreMetrica(d.metrica)}</dd>
          </div>
          <div>
            <dt>Observado</dt>
            <dd>{numeroCorto(ev.valor_observado)}</dd>
          </div>
          <div>
            <dt>Esperado</dt>
            <dd>{numeroCorto(ev.valor_esperado)}</dd>
          </div>
          <div>
            <dt>Ventana</dt>
            <dd>
              {fechaHora(ev.ventana.desde)} a {fechaHora(ev.ventana.hasta)}
            </dd>
          </div>
        </dl>
        <p className="meta">Metodo del detector: {ev.metodo}</p>
      </section>

      <div className="rejilla-incidente">
        <section className="panel panel-azul hipotesis" aria-labelledby="titulo-hipotesis">
          <div className="hipotesis-cabecera">
            <h3 id="titulo-hipotesis">Hipotesis de Zenit</h3>
          </div>
          {d.diagnostico ? (
            <>
              <p className="hipotesis-texto">{d.diagnostico.texto}</p>
              <p className="advertencia">
                Es una posible explicacion generada por un modelo, no una conclusion. Revisa la evidencia antes de actuar.
              </p>
              {citadas > 0 && (
                <button type="button" className="boton-texto ir-evidencia" onClick={() => elegir(lista[0].clave)}>
                  Se apoya en {citadas} {citadas === 1 ? "evidencia" : "evidencias"} →
                </button>
              )}
              <p className="hipotesis-pie">
                {d.diagnostico.modelo} · {hace(d.diagnostico.generado)}
              </p>
            </>
          ) : (
            <p className="hipotesis-texto">
              {enCola
                ? "La hipotesis esta en cola. Esta pagina la muestra cuando llegue."
                : "Todavia no hay una hipotesis para este incidente. La evidencia del detector esta al lado."}
            </p>
          )}
          {gestiona && !enCola && (
            <button type="button" className="boton-secundario" disabled={pidiendo} onClick={() => void pedirDiagnostico()}>
              {pidiendo ? "Pidiendo..." : d.diagnostico ? "Generar otra hipotesis" : "Pedir hipotesis"}
            </button>
          )}
        </section>

        <section className="panel panel-amarillo evidencia" aria-labelledby="titulo-evidencia">
          <h3 id="titulo-evidencia">Evidencia</h3>
          <ol className="lista-evidencia">
            {lista.map((p, i) => (
              <li key={p.clave}>
                <button
                  id={`evidencia-${p.clave}`}
                  type="button"
                  className={p.clave === elegida ? "item-evidencia elegida" : "item-evidencia"}
                  aria-pressed={p.clave === elegida}
                  onClick={() => setElegida(p.clave)}
                >
                  <span className="numero" aria-hidden="true">
                    {i + 1}
                  </span>
                  <span>
                    <span className="tipo">{p.origen}</span>
                    <span className="descripcion">
                      {nombreMetrica(p.metrica)}, {fechaHora(p.desde)} a {fechaHora(p.hasta)}
                    </span>
                    {p.observacion && <span className="descripcion observacion">{p.observacion}</span>}
                  </span>
                </button>
              </li>
            ))}
          </ol>
          <p className="nota">Registros y trazas todavia no llegan por la API (contrato v1).</p>
        </section>
      </div>

      {actual && (
        <section className="panel panel-blanco detalle-evidencia" aria-label={`Evidencia ${lista.indexOf(actual) + 1}`}>
          <h3>
            Evidencia {lista.indexOf(actual) + 1} · {nombreMetrica(actual.metrica)}
          </h3>
          {actual.observacion && <p className="observacion-citada">{actual.observacion}</p>}
          <p className="meta">
            {d.activo} · {fechaHora(actual.desde)} a {fechaHora(actual.hasta)}, con 10 min de margen a cada lado
          </p>
          <VistaPieza key={actual.clave} activo={d.activo} p={actual} />
        </section>
      )}
    </>
  );
}

export default function DetalleIncidente({ id }: { id: string }) {
  const n = Number(id);
  const valido = Number.isInteger(n) && n > 0;
  const r = useCarga((s) => (valido ? api.incidente(n, s) : Promise.reject(new Error("no existe"))), [n, valido]);
  const { reemplazar } = r;
  const actualizar = useMemo(() => (nuevo: Detalle) => reemplazar(() => nuevo), [reemplazar]);
  return (
    <Marco titulo={`Incidente ${valido ? n : ""}`}>
      {!valido ? (
        <ErrorVista mensaje={`"${id}" no es un numero de incidente.`} />
      ) : (
        <SegunCarga r={r} que="el incidente">
          {(d) => <Contenido key={d.id} d={d} actualizar={actualizar} />}
        </SegunCarga>
      )}
    </Marco>
  );
}
