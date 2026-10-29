import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { api, ErrorApi } from "../api";
import ConColumna from "../componentes/ColumnaDerecha";
import { SinDatos } from "../componentes/Estados";
import { InsigniaIncidente, InsigniaSeveridad } from "../componentes/Insignias";
import Marco from "../componentes/Marco";
import SegunCarga from "../componentes/SegunCarga";
import type { EstadoIncidente, Incidente } from "../contrato";
import { Enlace, navegar, useRuta } from "../enrutador";
import { ABIERTO, duracion, ESTADO_INCIDENTE, hace, ORDEN_ESTADO_INCIDENTE, SEVERIDAD, tituloIncidente } from "../formato";
import { useSesion } from "../sesion";
import { useCarga } from "../useCarga";

// Pantalla 3. Los cinco estados se distinguen de un vistazo y marcar un falso
// positivo es una accion de un clic en la misma fila.

const REFRESCO_MS = 15_000;
type Filtro = "abiertos" | "todos" | EstadoIncidente;

function ordenar(a: Incidente, b: Incidente) {
  const abiertoA = ABIERTO.includes(a.estado) ? 0 : 1;
  const abiertoB = ABIERTO.includes(b.estado) ? 0 : 1;
  return abiertoA - abiertoB || SEVERIDAD[a.severidad].orden - SEVERIDAD[b.severidad].orden || b.inicio.localeCompare(a.inicio);
}

function Lista({ incidentes, actualizar }: { incidentes: Incidente[]; actualizar: (i: Incidente) => void }) {
  const { puede } = useSesion();
  const { consulta } = useRuta();
  const filtro = (consulta.get("ver") as Filtro) || "abiertos";
  const [marcando, setMarcando] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hecho, setHecho] = useState<string | null>(null);
  // H-054: al marcar, el boton desaparece de la fila; el foco pasa al mensaje
  // para que el teclado no lo pierda (vuelve al cuerpo de la pagina si no)
  const aviso = useRef<HTMLDivElement>(null);
  // despues de pintar el mensaje (useEffect, no requestAnimationFrame: este no
  // corre con la pestana en segundo plano)
  useEffect(() => {
    if (hecho || error) aviso.current?.focus();
  }, [hecho, error]);
  const abrirConTeclado = (e: KeyboardEvent<HTMLTableRowElement>, id: number) => {
    if (e.target !== e.currentTarget) return; // Enter sobre el boton de la fila es del boton
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      navegar(`/incidentes/${id}`);
    }
  };

  const cuenta = (f: Filtro) =>
    f === "todos"
      ? incidentes.length
      : f === "abiertos"
        ? incidentes.filter((i) => ABIERTO.includes(i.estado)).length
        : incidentes.filter((i) => i.estado === f).length;
  const visibles = incidentes
    .filter((i) => filtro === "todos" || (filtro === "abiertos" ? ABIERTO.includes(i.estado) : i.estado === filtro))
    .sort(ordenar);

  const falsoPositivo = async (i: Incidente) => {
    setMarcando(i.id);
    setError(null);
    setHecho(null);
    try {
      const { marcada_por: _p, marcada_en: _e, ...nuevo } = await api.cambiarEstado(i.id, "falso_positivo");
      actualizar(nuevo);
      setHecho(`Incidente ${i.id} marcado como falso positivo.`);
    } catch (e) {
      setError(
        e instanceof ErrorApi && e.estado === 409
          ? `El incidente ${i.id} ya estaba cerrado o descartado; la lista se pone al día en el siguiente refresco.`
          : `No se pudo marcar el incidente ${i.id}: ${e instanceof Error ? e.message : e}`,
      );
    } finally {
      setMarcando(null);
    }
  };

  const filtros: Filtro[] = ["abiertos", ...ORDEN_ESTADO_INCIDENTE, "todos"];
  const altas = visibles.filter((i) => i.severidad === "alta").length;
  // como "Requiere tu atencion" del panel general del wireframe: un panel
  // amarillo con la cuenta arriba a la derecha y una barra de severidad en
  // cada fila
  const titulo =
    filtro === "abiertos" ? "Requieren tu atención" : filtro === "todos" ? "Todos los incidentes" : `Incidentes en estado «${ESTADO_INCIDENTE[filtro].texto.toLowerCase()}»`;
  return (
    <section className="panel panel-amarillo panel-incidentes" aria-labelledby="titulo-lista-incidentes">
      <div className="panel-cabecera">
        <h2 id="titulo-lista-incidentes">{titulo}</h2>
        <p className="panel-resumen">
          {visibles.length} {visibles.length === 1 ? "incidente" : "incidentes"}
          {altas > 0 && ` · ${altas} de severidad alta`}
        </p>
      </div>
      <nav className="filtros-chips" aria-label="Filtrar por estado">
        {filtros.map((f) => (
          <Enlace
            key={f}
            a={f === "abiertos" ? "/incidentes" : `/incidentes?ver=${f}`}
            className={filtro === f ? "chip activo" : "chip"}
            aria-current={filtro === f ? "true" : undefined}
          >
            {f === "abiertos" ? "Abiertos" : f === "todos" ? "Todos" : ESTADO_INCIDENTE[f].texto}
            <span className="cuenta">{cuenta(f)}</span>
          </Enlace>
        ))}
      </nav>
      <div aria-live="polite" ref={aviso} tabIndex={-1} className="aviso-foco">
        {error && <p className="mensaje-error">{error}</p>}
        {hecho && <p className="mensaje-ok">{hecho}</p>}
      </div>
      {visibles.length === 0 ? (
        <SinDatos
          titulo={filtro === "abiertos" ? "No hay incidentes abiertos" : "No hay incidentes en este estado"}
          detalle={filtro === "abiertos" ? "Ninguna señal está fuera de lo normal en este momento." : "Prueba con otro filtro."}
        />
      ) : (
        <div className="tabla-contenedor">
          <table className="tabla tabla-incidentes">
            <caption className="solo-lector">Incidentes, los abiertos y más severos primero</caption>
            <thead>
              <tr>
                <th scope="col">Estado</th>
                <th scope="col">Severidad</th>
                <th scope="col">Que pasa</th>
                <th scope="col">Activo</th>
                <th scope="col">Desde</th>
                <th scope="col">Duración</th>
                <th scope="col">Hipótesis</th>
                <th scope="col">
                  <span className="solo-lector">Acciones</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {visibles.map((i) => (
                <tr
                  key={i.id}
                  className={`fila-inc fila-${i.estado} fila-sev-${i.severidad}`}
                  tabIndex={0}
                  onClick={() => navegar(`/incidentes/${i.id}`)}
                  onKeyDown={(e) => abrirConTeclado(e, i.id)}
                >
                  <td>
                    <InsigniaIncidente estado={i.estado} />
                  </td>
                  <td>
                    <InsigniaSeveridad severidad={i.severidad} />
                  </td>
                  <th scope="row">
                    {/* la fila ya es el destino del teclado: el enlace queda para el raton y para abrir en otra pestana */}
                    <Enlace a={`/incidentes/${i.id}`} onClick={(e) => e.stopPropagation()} tabIndex={-1}>
                      {tituloIncidente(i)}
                    </Enlace>
                  </th>
                  <td>{i.activo}</td>
                  <td title={new Date(i.inicio).toLocaleString()}>{hace(i.inicio)}</td>
                  <td>{duracion(i.inicio, i.fin)}</td>
                  <td>{i.tiene_diagnostico ? "Lista" : <span className="apagado">Aún no</span>}</td>
                  <td className="celda-accion" onClick={(e) => e.stopPropagation()}>
                    {puede("incidentes:gestionar") && ABIERTO.includes(i.estado) && (
                      <button
                        type="button"
                        className="boton-secundario"
                        disabled={marcando === i.id}
                        onClick={() => void falsoPositivo(i)}
                        aria-label={`Marcar el incidente ${i.id} como falso positivo`}
                      >
                        {marcando === i.id ? "Marcando..." : "Falso positivo"}
                      </button>
                    )}
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

export default function Incidentes() {
  const r = useCarga((s) => api.incidentes({}, s), [], REFRESCO_MS);
  const actualizar = (nuevo: Incidente) => r.reemplazar((lista) => lista.map((i) => (i.id === nuevo.id ? nuevo : i)));
  return (
    <Marco titulo="Incidentes">
      <ConColumna>
        <SegunCarga r={r} que="los incidentes">
          {(incidentes) => <Lista incidentes={incidentes} actualizar={actualizar} />}
        </SegunCarga>
      </ConColumna>
    </Marco>
  );
}
