import { useState } from "react";
import { api } from "../api";
import { SinDatos } from "../componentes/Estados";
import { InsigniaSeveridad } from "../componentes/Insignias";
import Marco from "../componentes/Marco";
import SegunCarga from "../componentes/SegunCarga";
import type { Hallazgo, Severidad } from "../contrato";
import { fechaHora, hace, SEVERIDAD } from "../formato";
import { useCarga } from "../useCarga";

// Pantalla 5, como tabla. Cada hallazgo dice la regla que lo produjo, los
// activos, los entornos que entraron en la comparacion y la severidad.

// Nombres legibles de las reglas que hoy produce la API. Una regla nueva se
// muestra con su identificador hasta que se agregue aqui.
const REGLA: Record<string, string> = {
  "puerto-expuesto-no-esperado": "Puerto expuesto no esperado",
  "version-agente-distinta": "Version del agente distinta",
  "intervalo-muestreo-distinto": "Intervalo de muestreo distinto",
  "contenedor-como-root": "Contenedor como root",
  "imagen-version-distinta": "Imagen en versiones distintas",
};

function Contenido({ hallazgos }: { hallazgos: Hallazgo[] }) {
  const [severidad, setSeveridad] = useState<Severidad | "">("");
  const [entorno, setEntorno] = useState("");
  if (hallazgos.length === 0)
    return (
      <SinDatos
        titulo="Sin hallazgos"
        detalle="Las ultimas instantaneas de los nodos cumplen todas las reglas. Si nunca se enviaron, corre scripts/instantanea-nodo.sh en cada nodo."
      />
    );
  const entornos = [...new Set(hallazgos.flatMap((h) => h.entornos))].sort();
  const visibles = hallazgos
    .filter((h) => (!severidad || h.severidad === severidad) && (!entorno || h.entornos.includes(entorno)))
    .sort((a, b) => SEVERIDAD[a.severidad].orden - SEVERIDAD[b.severidad].orden || b.detectado.localeCompare(a.detectado));
  const ultima = hallazgos
    .map((h) => h.detectado)
    .sort()
    .pop()!;

  return (
    <>
      <ul className="cifras" aria-label="Hallazgos por severidad">
        {(Object.keys(SEVERIDAD) as Severidad[]).map((s) => (
          <li key={s} className={s === "alta" && hallazgos.some((h) => h.severidad === s) ? "cifra-alerta" : undefined}>
            <span className="cifra">{hallazgos.filter((h) => h.severidad === s).length}</span>
            <span>Severidad {SEVERIDAD[s].texto.toLowerCase()}</span>
          </li>
        ))}
      </ul>
      <section className="panel panel-amarillo" aria-labelledby="titulo-hallazgos">
        <h2 id="titulo-hallazgos">
          Hallazgos <span className="cuenta">{visibles.length}</span>
        </h2>
        <form className="filtros-inventario" onSubmit={(e) => e.preventDefault()}>
          <label>
            Severidad
            <select value={severidad} onChange={(e) => setSeveridad(e.target.value as Severidad | "")}>
              <option value="">Todas</option>
              {(Object.keys(SEVERIDAD) as Severidad[]).map((s) => (
                <option key={s} value={s}>
                  {SEVERIDAD[s].texto}
                </option>
              ))}
            </select>
          </label>
          <label>
            Entorno
            <select value={entorno} onChange={(e) => setEntorno(e.target.value)}>
              <option value="">Todos</option>
              {entornos.map((x) => (
                <option key={x} value={x}>
                  {x}
                </option>
              ))}
            </select>
          </label>
        </form>
        {visibles.length === 0 ? (
          <p>Ningun hallazgo con esos filtros.</p>
        ) : (
          <div className="tabla-contenedor">
            <table className="tabla">
              <caption className="solo-lector">Hallazgos de configuracion, los mas severos primero</caption>
              <thead>
                <tr>
                  <th scope="col">Severidad</th>
                  <th scope="col">Regla y detalle</th>
                  <th scope="col">Activos</th>
                  <th scope="col">Entornos comparados</th>
                  <th scope="col">Detectado</th>
                </tr>
              </thead>
              <tbody>
                {visibles.map((h, i) => (
                  <tr key={`${h.regla}-${i}`}>
                    <td>
                      <InsigniaSeveridad severidad={h.severidad} />
                    </td>
                    <th scope="row">
                      {REGLA[h.regla] ?? h.regla}
                      <span className="detalle">{h.detalle}</span>
                    </th>
                    <td>{h.activos.join(", ")}</td>
                    <td>
                      <span className="entornos">
                        {h.entornos.map((e) => (
                          <span key={e} className="chip-entorno">
                            {e}
                          </span>
                        ))}
                      </span>
                    </td>
                    <td title={fechaHora(h.detectado)}>{hace(h.detectado)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="nota">Instantanea mas reciente: {fechaHora(ultima)}</p>
      </section>
    </>
  );
}

export default function Configuraciones() {
  const r = useCarga((s) => api.configuraciones(s), [], 60_000);
  return (
    <Marco titulo="Configuracion">
      <SegunCarga r={r} que="el reporte de configuraciones">
        {(hallazgos) => <Contenido hallazgos={hallazgos} />}
      </SegunCarga>
    </Marco>
  );
}
