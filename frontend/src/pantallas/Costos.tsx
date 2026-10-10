import { useRef, useState, type FormEvent } from "react";
import { api } from "../api";
import { SinDatos } from "../componentes/Estados";
import Marco from "../componentes/Marco";
import SegunCarga from "../componentes/SegunCarga";
import type { Activo, Proveedor, ReporteCostos, ResumenImportacion } from "../contrato";
import { Enlace } from "../enrutador";
import { dinero, PROVEEDOR } from "../formato";
import { useSesion } from "../sesion";
import { useCarga } from "../useCarga";

// Pantalla 6, como tablas. Costo atribuido a cada activo, y lo no asignable
// aparte (RF-GOB-07): nunca se reparte a ojo. La tendencia y los activos
// subutilizados no estan en el contrato v1 (NOTAS.md).

function Importar({ alTerminar }: { alTerminar: () => void }) {
  const archivo = useRef<HTMLInputElement>(null);
  const [enviando, setEnviando] = useState(false);
  const [resultado, setResultado] = useState<ResumenImportacion | null>(null);
  const [error, setError] = useState<string | null>(null);

  const enviar = async (e: FormEvent) => {
    e.preventDefault();
    const f = archivo.current?.files?.[0];
    if (!f) {
      setError("Elige el CSV de la factura.");
      return;
    }
    setEnviando(true);
    setError(null);
    setResultado(null);
    try {
      setResultado(await api.importarFactura("digitalocean", f));
      if (archivo.current) archivo.current.value = "";
      alTerminar();
    } catch (ex) {
      setError(ex instanceof Error ? ex.message : String(ex));
    } finally {
      setEnviando(false);
    }
  };

  return (
    <form className="panel panel-azul importar" onSubmit={enviar}>
      <h2>Importar factura</h2>
      <p>Exporta la factura de DigitalOcean en CSV (Billing, Download CSV) y subela aqui.</p>
      <label>
        Archivo CSV
        <input ref={archivo} type="file" accept=".csv,text/csv" />
      </label>
      <button type="submit" className="boton-primario" disabled={enviando}>
        {enviando ? "Importando..." : "Importar"}
      </button>
      <div aria-live="polite">
        {error && <p className="mensaje-error">{error}</p>}
        {resultado && (
          <p className="mensaje-ok">
            {resultado.nueva ? "Factura importada" : "Esa factura ya estaba importada; no se duplico"}: {resultado.lineas} lineas,{" "}
            {resultado.asignadas} atribuidas, {resultado.sin_asignar} sin asignar, total {dinero(resultado.total)}.
          </p>
        )}
      </div>
      {resultado && resultado.lineas_sin_asignar.length > 0 && (
        <details className="sin-asignar-importacion" open>
          <summary>Lineas que no se atribuyeron y por que</summary>
          <ul className="lista-simple">
            {resultado.lineas_sin_asignar.map((l, i) => (
              <li key={i}>
                {l.producto}: {l.concepto} · {dinero(l.monto)}. {l.motivo}
              </li>
            ))}
          </ul>
        </details>
      )}
    </form>
  );
}

// suma por una llave; las filas sin llave caen en "desconocido"
function agrupar<K extends string>(filas: { costo: number }[], llave: (i: number) => K | undefined) {
  const m = new Map<K | "desconocido", number>();
  filas.forEach((f, i) => {
    const k = llave(i) ?? "desconocido";
    m.set(k, (m.get(k) ?? 0) + f.costo);
  });
  return [...m.entries()].sort((a, b) => b[1] - a[1]);
}

function Reporte({ r, activos }: { r: ReporteCostos; activos: Activo[] | null }) {
  const atribuido = r.por_activo.reduce((n, a) => n + a.costo, 0);
  const total = atribuido + r.sin_asignar;
  if (r.por_activo.length === 0 && r.sin_asignar === 0)
    return (
      <SinDatos
        titulo={`No hay cargos entre ${r.periodo.desde} y ${r.periodo.hasta}`}
        detalle="Importa la factura del proveedor o cambia el periodo."
      />
    );
  const filas = [...r.por_activo].sort((a, b) => b.costo - a.costo);
  const entornoDe = new Map(activos?.map((a) => [a.id, a.entorno]));
  const nombreDe = new Map(activos?.map((a) => [a.id, a.nombre]));
  const porProveedor = agrupar<Proveedor>(filas, (i) => filas[i].proveedor);
  const porEntorno = activos ? agrupar<string>(filas, (i) => entornoDe.get(filas[i].activo)) : null;

  return (
    <>
      <ul className="cifras" aria-label="Totales">
        <li>
          <span className="cifra">{dinero(total, r.moneda)}</span>
          <span>Total del periodo</span>
        </li>
        <li>
          <span className="cifra">{dinero(atribuido, r.moneda)}</span>
          <span>Atribuido a activos</span>
        </li>
        <li className={r.sin_asignar > 0 ? "cifra-alerta" : undefined}>
          <span className="cifra">{dinero(r.sin_asignar, r.moneda)}</span>
          <span>Sin asignar</span>
        </li>
      </ul>

      <section className="panel panel-amarillo" aria-labelledby="titulo-por-activo">
        <h2 id="titulo-por-activo">Costo por activo</h2>
        <div className="tabla-contenedor">
          <table className="tabla">
            <thead>
              <tr>
                <th scope="col">Activo</th>
                <th scope="col">Proveedor</th>
                {activos && <th scope="col">Entorno</th>}
                <th scope="col" className="numero">
                  Costo
                </th>
                <th scope="col">Fuente</th>
              </tr>
            </thead>
            <tbody>
              {filas.map((a) => (
                <tr key={`${a.activo}-${a.proveedor}`}>
                  <th scope="row">
                    <Enlace a={`/activos/${encodeURIComponent(a.activo)}`}>{nombreDe.get(a.activo) ?? a.activo}</Enlace>
                    {nombreDe.has(a.activo) && <span className="detalle">{a.activo}</span>}
                  </th>
                  <td>{PROVEEDOR[a.proveedor]?.nombre ?? a.proveedor}</td>
                  {activos && <td>{entornoDe.get(a.activo) ?? "fuera del catalogo"}</td>}
                  <td className="numero">{dinero(a.costo, r.moneda)}</td>
                  <td>{a.fuente}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {r.sin_asignar > 0 && (
          <p className="nota">
            {dinero(r.sin_asignar, r.moneda)} no corresponden a un solo activo del catalogo y no se reparten. El detalle por linea se ve al
            importar la factura.
          </p>
        )}
      </section>

      <div className="rejilla-dos">
        <Totales
          titulo="Por proveedor"
          filas={porProveedor.map(([k, v]) => [k === "desconocido" ? "Desconocido" : PROVEEDOR[k].nombre, v])}
          moneda={r.moneda}
        />
        {porEntorno && (
          <Totales
            titulo="Por entorno"
            filas={porEntorno.map(([k, v]) => [k === "desconocido" ? "Fuera del catalogo" : k, v])}
            moneda={r.moneda}
          />
        )}
      </div>
    </>
  );
}

function Totales({ titulo, filas, moneda }: { titulo: string; filas: [string, number][]; moneda: string }) {
  return (
    <section className="panel panel-blanco">
      <h2>{titulo}</h2>
      <table className="tabla">
        <tbody>
          {filas.map(([k, v]) => (
            <tr key={k}>
              <th scope="row">{k}</th>
              <td className="numero">{dinero(v, moneda)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="nota">Solo lo atribuido; lo sin asignar queda fuera.</p>
    </section>
  );
}

export default function Costos() {
  const { puede } = useSesion();
  const [desde, setDesde] = useState("");
  const [hasta, setHasta] = useState("");
  const invertido = !!desde && !!hasta && desde > hasta;
  const r = useCarga((s) => (invertido ? new Promise<never>(() => {}) : api.costos(desde, hasta, s)), [desde, hasta, invertido]);
  // el entorno sale del inventario; si no carga, la pantalla sigue sin esa columna
  const inv = useCarga((s) => api.activos(s), []);
  const activos = inv.carga.estado === "listo" ? inv.carga.datos : null;
  return (
    <Marco titulo="Costos">
      <div className="rejilla-con-lateral">
        <div>
          {r.carga.estado !== "sin_permiso" && (
            <form className="filtros-inventario" onSubmit={(e) => e.preventDefault()}>
              <label>
                Desde
                <input type="date" value={desde} max={hasta || undefined} onChange={(e) => setDesde(e.target.value)} />
              </label>
              <label>
                Hasta
                <input type="date" value={hasta} min={desde || undefined} onChange={(e) => setHasta(e.target.value)} />
              </label>
              {(desde || hasta) && (
                <button
                  type="button"
                  className="boton-texto"
                  onClick={() => {
                    setDesde("");
                    setHasta("");
                  }}
                >
                  Periodo por defecto
                </button>
              )}
            </form>
          )}
          {invertido ? (
            <p className="mensaje-error">La fecha inicial es posterior a la final.</p>
          ) : (
            <SegunCarga r={r} que="los costos">
              {(datos) => (
                <>
                  <p className="meta">
                    Periodo: {datos.periodo.desde} a {datos.periodo.hasta}
                    {!desde && !hasta ? " (por defecto: todo lo importado o, si no hay nada, el mes en curso)" : ""}
                  </p>
                  <Reporte r={datos} activos={activos} />
                </>
              )}
            </SegunCarga>
          )}
        </div>
        {puede("costos:importar") && <Importar alTerminar={r.reintentar} />}
      </div>
    </Marco>
  );
}
