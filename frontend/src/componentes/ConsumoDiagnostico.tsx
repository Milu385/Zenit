import { api } from "../api";
import { fechaDia, periodoDias } from "../formato";
import { useCarga } from "../useCarga";
import { SinDatos } from "./Estados";
import SegunCarga from "./SegunCarga";

// H-042: cuantos diagnosticos se generaron y cuantos tokens usaron, por dia y
// por modelo, con la version configurada. Va en la pantalla de fuentes y solo
// la ve quien tiene diagnostico:consumo (administrador).

const entero = (n: number) => n.toLocaleString("es-CO");

export default function ConsumoDiagnostico() {
  const r = useCarga((s) => api.consumoDiagnostico(s), [], 60_000);
  return (
    <section className="panel panel-blanco" aria-labelledby="titulo-consumo">
      <h2 id="titulo-consumo">Consumo del diagnóstico</h2>
      <SegunCarga r={r} que="el consumo del diagnóstico">
        {(c) => {
          // con un solo modelo, y el mismo que esta configurado, la columna
          // repetiria en cada fila lo que ya dice el subtitulo
          const modelos = new Set(c.por_dia.map((d) => d.modelo));
          const verModelo = modelos.size > 1 || (modelos.size === 1 && !modelos.has(c.modelo_configurado ?? ""));
          return (
          <>
            <p className="nota">
              Modelo configurado: <code>{c.modelo_configurado ?? "sin configurar"}</code> · {periodoDias(c.periodo.desde, c.periodo.hasta)} (UTC)
            </p>
            {c.por_dia.length === 0 ? (
              <SinDatos titulo="Todavía no hay diagnósticos" detalle="Cuando se genere una hipótesis, aquí se verán sus tokens." />
            ) : (
              <div className="tabla-contenedor">
                <table className="tabla">
                  <caption className="solo-lector">Diagnósticos y tokens por día y por modelo</caption>
                  <thead>
                    <tr>
                      <th scope="col">Día</th>
                      {verModelo && <th scope="col">Modelo</th>}
                      <th scope="col" className="numero">
                        Diagnósticos
                      </th>
                      <th scope="col" className="numero">
                        Tokens de entrada
                      </th>
                      <th scope="col" className="numero">
                        Tokens de salida
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {c.por_dia
                      .slice()
                      .reverse()
                      .map((d) => (
                        <tr key={`${d.dia}|${d.modelo}`}>
                          <td>{fechaDia(d.dia)}</td>
                          {verModelo && (
                            <td>
                              <code>{d.modelo}</code>
                            </td>
                          )}
                          <td className="numero">{entero(d.diagnosticos)}</td>
                          <td className="numero">{entero(d.tokens_entrada)}</td>
                          <td className="numero">{entero(d.tokens_salida)}</td>
                        </tr>
                      ))}
                  </tbody>
                  <tfoot>
                    {c.por_modelo.map((t) => (
                      <tr key={t.modelo}>
                        <th scope="row">Total</th>
                        {verModelo && (
                          <td>
                            <code>{t.modelo}</code>
                          </td>
                        )}
                        <td className="numero">{entero(t.diagnosticos)}</td>
                        <td className="numero">{entero(t.tokens_entrada)}</td>
                        <td className="numero">{entero(t.tokens_salida)}</td>
                      </tr>
                    ))}
                  </tfoot>
                </table>
              </div>
            )}
          </>
          );
        }}
      </SegunCarga>
    </section>
  );
}
