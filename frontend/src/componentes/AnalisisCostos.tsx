import type { AnalisisCostos as Analisis, UsoActivo } from "../contrato";
import { Enlace } from "../enrutador";
import { dinero, porcentaje } from "../formato";
import { SinDatos } from "./Estados";

// H-047, pantalla 6. Fuera del contrato (NOTAS.md).
// Subutilizados: el costo de cada activo junto a su CPU y memoria del resumen
// por hora, con el criterio escrito arriba de la tabla. Es un dato para
// revisar: no se sugiere apagar ni redimensionar nada.
// Tendencia: el gasto de cada mes, por entorno y por activo.

const MES = new Intl.DateTimeFormat("es-CO", { month: "short", year: "numeric", timeZone: "UTC" });
const nombreMes = (m: string) => MES.format(new Date(`${m}-01T00:00:00Z`));

function Marca({ a }: { a: UsoActivo }) {
  if (a.subutilizado === true)
    return (
      <span className="insignia insignia-atencion">
        <span aria-hidden="true">!</span> Subutilizado
      </span>
    );
  if (a.subutilizado === false)
    return (
      <span className="insignia insignia-ok">
        <span aria-hidden="true">✓</span> En uso
      </span>
    );
  return (
    <span className="insignia insignia-sin_datos">
      <span aria-hidden="true">?</span> Sin datos suficientes
    </span>
  );
}

function Subutilizados({ a }: { a: Analisis }) {
  const c = a.criterio;
  const marcados = a.activos.filter((x) => x.subutilizado === true);
  return (
    <section className="panel panel-blanco" aria-labelledby="titulo-subutilizados">
      <h2 id="titulo-subutilizados">
        Costo contra uso <span className="cuenta">
          {marcados.length} {marcados.length === 1 ? "subutilizado" : "subutilizados"}
        </span>
      </h2>
      <p className="criterio">
        <strong>Criterio:</strong> se marca subutilizado un activo con costo en el periodo, al menos {c.horas_minimas} h de uso medidas,
        CPU promedio menor a {porcentaje(c.cpu_promedio_max)}, ninguna hora con CPU de {porcentaje(c.cpu_pico_max)} o mas, y memoria
        promedio menor a {porcentaje(c.memoria_promedio_max)}. Uso tomado del resumen por hora.
      </p>
      {!a.uso.disponible && <p className="mensaje-error">El almacen de series no respondio: no se pudo medir el uso, solo se muestra el costo.</p>}
      {a.activos.length === 0 ? (
        <SinDatos titulo="Nada que cruzar" detalle="No hay costos ni uso medido en el periodo." />
      ) : (
        <div className="tabla-contenedor">
          <table className="tabla">
            <caption className="solo-lector">Costo y uso de cada activo, los subutilizados primero</caption>
            <thead>
              <tr>
                <th scope="col">Situacion</th>
                <th scope="col">Activo</th>
                <th scope="col">Entorno</th>
                <th scope="col" className="numero">
                  Costo
                </th>
                <th scope="col" className="numero">
                  CPU promedio
                </th>
                <th scope="col" className="numero">
                  Hora de mas CPU
                </th>
                <th scope="col" className="numero">
                  Memoria promedio
                </th>
                <th scope="col">Por que</th>
              </tr>
            </thead>
            <tbody>
              {a.activos.map((x) => (
                <tr key={x.activo}>
                  <td>
                    <Marca a={x} />
                  </td>
                  <th scope="row">
                    <Enlace a={`/activos/${encodeURIComponent(x.activo)}`}>{x.activo}</Enlace>
                  </th>
                  <td>{x.entorno}</td>
                  <td className="numero">{dinero(x.costo, a.moneda)}</td>
                  <td className="numero">{porcentaje(x.cpu_promedio)}</td>
                  <td className="numero">{porcentaje(x.cpu_pico)}</td>
                  <td className="numero">{porcentaje(x.memoria_promedio)}</td>
                  <td>{x.motivo}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function variacion(montos: number[]) {
  if (montos.length < 2) return null;
  const [antes, ahora] = montos.slice(-2);
  if (antes === 0) return ahora === 0 ? "igual" : "nuevo";
  const v = (ahora - antes) / antes;
  if (Math.abs(v) < 0.005) return "igual";
  return `${v > 0 ? "▲ sube" : "▼ baja"} ${Math.abs(Math.round(v * 100))} %`;
}

function TablaTendencia({
  titulo,
  columna,
  meses,
  filas,
  moneda,
}: {
  titulo: string;
  columna: string;
  meses: string[];
  filas: { nombre: string; enlace?: string; montos: number[] }[];
  moneda: string;
}) {
  const maximo = Math.max(1e-9, ...filas.flatMap((f) => f.montos));
  return (
    <div className="tabla-contenedor">
      <table className="tabla tabla-tendencia">
        <caption>{titulo}</caption>
        <thead>
          <tr>
            <th scope="col">{columna}</th>
            {meses.map((m) => (
              <th key={m} scope="col" className="numero">
                {nombreMes(m)}
              </th>
            ))}
            <th scope="col">Ultimo mes contra el anterior</th>
          </tr>
        </thead>
        <tbody>
          {filas.map((f) => (
            <tr key={f.nombre}>
              <th scope="row">{f.enlace ? <Enlace a={f.enlace}>{f.nombre}</Enlace> : f.nombre}</th>
              {f.montos.map((v, i) => (
                <td key={meses[i]} className="numero">
                  {dinero(v, moneda)}
                  <span className="barra-mes" aria-hidden="true">
                    <span style={{ width: `${(v / maximo) * 100}%` }} />
                  </span>
                </td>
              ))}
              <td>{variacion(f.montos) ?? <span className="apagado">un solo mes</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Tendencia({ a }: { a: Analisis }) {
  const t = a.tendencia;
  return (
    <section className="panel panel-blanco" aria-labelledby="titulo-tendencia">
      <h2 id="titulo-tendencia">Tendencia del gasto</h2>
      {t.meses.length === 0 ? (
        <SinDatos titulo="Sin gasto en el periodo" detalle="No hay cargos importados que empiecen en estas fechas." />
      ) : (
        <>
          <p className="nota">Cada cargo cuenta en el mes en que empieza, igual que en el reporte de arriba.</p>
          <TablaTendencia
            titulo="Por entorno"
            columna="Entorno"
            meses={t.meses}
            moneda={a.moneda}
            filas={t.por_entorno.map((e) => ({ nombre: e.entorno, montos: e.montos }))}
          />
          <TablaTendencia
            titulo="Por activo"
            columna="Activo"
            meses={t.meses}
            moneda={a.moneda}
            filas={t.por_activo.map((x) => ({ nombre: x.activo, enlace: `/activos/${encodeURIComponent(x.activo)}`, montos: x.montos }))}
          />
        </>
      )}
    </section>
  );
}

export default function AnalisisCostos({ a }: { a: Analisis }) {
  return (
    <>
      <Tendencia a={a} />
      <Subutilizados a={a} />
    </>
  );
}
