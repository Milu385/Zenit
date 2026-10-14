import { useState } from "react";
import { api } from "../api";
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
      await api.cambiarEstado(i.id, "falso_positivo");
      // el contrato no fija la respuesta: se refleja el cambio aqui y el
      // siguiente refresco trae lo que diga la API
      actualizar({ ...i, estado: "falso_positivo" });
      setHecho(`Incidente ${i.id} marcado como falso positivo.`);
    } catch (e) {
      setError(`No se pudo marcar el incidente ${i.id}: ${e instanceof Error ? e.message : e}`);
    } finally {
      setMarcando(null);
    }
  };

  const filtros: Filtro[] = ["abiertos", ...ORDEN_ESTADO_INCIDENTE, "todos"];
  return (
    <>
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
      <div aria-live="polite">
        {error && <p className="mensaje-error">{error}</p>}
        {hecho && <p className="mensaje-ok">{hecho}</p>}
      </div>
      {visibles.length === 0 ? (
        <SinDatos
          titulo={filtro === "abiertos" ? "No hay incidentes abiertos" : "No hay incidentes en este estado"}
          detalle={filtro === "abiertos" ? "Ninguna senal esta fuera de lo normal en este momento." : "Prueba con otro filtro."}
        />
      ) : (
        <div className="tabla-contenedor">
          <table className="tabla tabla-incidentes">
            <caption className="solo-lector">Incidentes, los abiertos y mas severos primero</caption>
            <thead>
              <tr>
                <th scope="col">Estado</th>
                <th scope="col">Severidad</th>
                <th scope="col">Que pasa</th>
                <th scope="col">Activo</th>
                <th scope="col">Desde</th>
                <th scope="col">Duracion</th>
                <th scope="col">Hipotesis</th>
                <th scope="col">
                  <span className="solo-lector">Acciones</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {visibles.map((i) => (
                <tr key={i.id} className={`fila-inc fila-${i.estado}`} onClick={() => navegar(`/incidentes/${i.id}`)}>
                  <td>
                    <InsigniaIncidente estado={i.estado} />
                  </td>
                  <td>
                    <InsigniaSeveridad severidad={i.severidad} />
                  </td>
                  <th scope="row">
                    <Enlace a={`/incidentes/${i.id}`} onClick={(e) => e.stopPropagation()}>
                      {tituloIncidente(i)}
                    </Enlace>
                  </th>
                  <td>{i.activo}</td>
                  <td title={new Date(i.inicio).toLocaleString()}>{hace(i.inicio)}</td>
                  <td>{duracion(i.inicio, i.fin)}</td>
                  <td>{i.tiene_diagnostico ? "Lista" : <span className="apagado">Aun no</span>}</td>
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
    </>
  );
}

export default function Incidentes() {
  const r = useCarga((s) => api.incidentes({}, s), [], REFRESCO_MS);
  const actualizar = (nuevo: Incidente) => r.reemplazar((lista) => lista.map((i) => (i.id === nuevo.id ? nuevo : i)));
  return (
    <Marco titulo="Incidentes">
      <SegunCarga r={r} que="los incidentes">
        {(incidentes) => <Lista incidentes={incidentes} actualizar={actualizar} />}
      </SegunCarga>
    </Marco>
  );
}
