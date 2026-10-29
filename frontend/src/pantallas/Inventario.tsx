import { useState } from "react";
import { api } from "../api";
import ConColumna from "../componentes/ColumnaDerecha";
import { SinDatos } from "../componentes/Estados";
import Marco from "../componentes/Marco";
import SegunCarga from "../componentes/SegunCarga";
import type { Activo, EstadoActivo, Proveedor } from "../contrato";
import { Enlace } from "../enrutador";
import { datosProveedor, ESTADO_ACTIVO, hace, ORDEN_ESTADO_ACTIVO, porcentaje, REQUIERE_ATENCION } from "../formato";
import { useCarga } from "../useCarga";

// Pantalla 1. Se organiza por ESTADO y no por proveedor: lo primero que ve el
// operador es lo que esta mal, venga de donde venga. El proveedor es un
// filtro y una etiqueta, nunca el agrupador.

const REFRESCO_MS = 15_000;

function Barra({ valor, etiqueta }: { valor: number | null; etiqueta: string }) {
  const alto = valor != null && valor >= 0.85;
  return (
    <div className="barra-uso">
      <span className="barra-etiqueta">{etiqueta}</span>
      <span className="barra" aria-hidden="true">
        <span className={alto ? "barra-relleno alto" : "barra-relleno"} style={{ width: `${Math.min(100, (valor ?? 0) * 100)}%` }} />
      </span>
      <span className="barra-valor">{porcentaje(valor)}</span>
    </div>
  );
}

function TarjetaActivo({ a }: { a: Activo }) {
  const p = datosProveedor(a.proveedor);
  const e = ESTADO_ACTIVO[a.estado];
  return (
    <li>
      <Enlace a={`/activos/${encodeURIComponent(a.id)}`} className={`tarjeta-activo estado-${a.estado}`}>
        <span className={`sigla sigla-${a.proveedor}`} title={p.nombre}>
          {p.sigla}
        </span>
        <span className="tarjeta-activo-cuerpo">
          <span className="tarjeta-activo-nombre">{a.nombre}</span>
          {/* sin el proveedor (ya lo dice la sigla) ni el estado (ya lo dice el
              grupo): solo lo que distingue a este activo de sus vecinos */}
          <span className="tarjeta-activo-meta">
            {a.id}
            {a.region ? ` · ${a.region}` : ""} · entorno {a.entorno}
          </span>
          <span className="solo-lector">{p.nombre}. Estado: {e.texto}.</span>
        </span>
        <span className="tarjeta-activo-uso">
          {/* el contrato solo trae CPU: promedio de los ultimos 5 min */}
          <Barra etiqueta="CPU 5 min" valor={a.cpu_reciente} />
        </span>
        <span className="tarjeta-activo-pie">
          <span>Última recepción {hace(a.ultima_recepcion)}</span>
          {a.incidentes_abiertos > 0 && (
            <span className="contador-incidentes">
              {a.incidentes_abiertos} {a.incidentes_abiertos === 1 ? "incidente abierto" : "incidentes abiertos"}
            </span>
          )}
        </span>
      </Enlace>
    </li>
  );
}

// "Servidores visualizados" del wireframe: elegir un proveedor deja ver solo
// sus activos (es el mismo filtro de proveedor de arriba); volver a pulsarlo
// muestra todos otra vez.
function ServidoresVisualizados({
  activos,
  elegido,
  alElegir,
}: {
  activos: Activo[];
  elegido: Proveedor | "";
  alElegir: (p: Proveedor | "") => void;
}) {
  const proveedores = [...new Set(activos.map((a) => a.proveedor))].sort();
  return (
    <section className="servidores-visualizados" aria-labelledby="titulo-visualizados">
      <h2 id="titulo-visualizados">Servidores visualizados</h2>
      <ul>
        {proveedores.map((p) => {
          const nombres = activos.filter((a) => a.proveedor === p).map((a) => a.nombre);
          const visible = !elegido || elegido === p;
          return (
            <li key={p}>
              <button
                type="button"
                className={visible ? "visualizado" : "visualizado oculto"}
                aria-pressed={elegido === p}
                onClick={() => alElegir(elegido === p ? "" : p)}
              >
                <span className="visualizado-nombre">{datosProveedor(p).nombre}</span>
                <span className="visualizado-meta">
                  {nombres.slice(0, 2).join(", ")}
                  {nombres.length > 2 ? ` y ${nombres.length - 2} más` : ""}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
      <p className="nota-columna">{elegido ? "Pulsa el proveedor otra vez para ver todos." : "Pulsa un proveedor para ver solo sus activos."}</p>
    </section>
  );
}

function Contenido({ activos }: { activos: Activo[] }) {
  const [proveedor, setProveedor] = useState<Proveedor | "">("");
  const [entorno, setEntorno] = useState("");
  const [estado, setEstado] = useState<EstadoActivo | "">("");
  const [texto, setTexto] = useState("");
  if (activos.length === 0)
    return (
      <SinDatos
        titulo="Todavía no hay activos en el inventario"
        detalle="El descubrimiento todavía no encontró nodos. Para admitir uno a mano: zenit.admin catalogo admitir."
      />
    );
  const q = texto.trim().toLowerCase();
  const entornos = [...new Set(activos.map((a) => a.entorno))].sort();
  const visibles = activos.filter(
    (a) =>
      (!proveedor || a.proveedor === proveedor) &&
      (!entorno || a.entorno === entorno) &&
      (!estado || a.estado === estado) &&
      (!q || a.nombre.toLowerCase().includes(q) || a.id.toLowerCase().includes(q)),
  );
  const grupos = ORDEN_ESTADO_ACTIVO.map((e) => ({ estado: e, lista: visibles.filter((a) => a.estado === e) }));
  // los retirados se muestran (marcados) pero no cuentan en el total vigente
  const vigentes = activos.filter((a) => a.estado !== "retirado").length;
  const problemas = activos
    .filter((a) => REQUIERE_ATENCION.includes(a.estado))
    .sort((x, y) => ORDEN_ESTADO_ACTIVO.indexOf(x.estado) - ORDEN_ESTADO_ACTIVO.indexOf(y.estado));
  const conProblemas = problemas.length;
  // como el wireframe ("Tu servidor Azure requiere atencion"): se nombran los
  // activos, los peores primero, hasta tres
  const nombrados = problemas.slice(0, 3).map((a) => a.nombre);
  const resto = conProblemas - nombrados.length;

  return (
    <ConColumna
      activos={activos}
      lateral={<ServidoresVisualizados activos={activos} elegido={proveedor} alElegir={setProveedor} />}
    >
      <div className={`franja ${conProblemas ? "franja-alerta" : "franja-bien"}`} role="status">
        <span className="franja-punto" aria-hidden="true" />
        <span>
          <strong>
            {conProblemas ? `${conProblemas} de ${vigentes} activos requieren atención` : `Operación estable: ${vigentes} activos`}
          </strong>
          <br />
          {conProblemas
            ? `${nombrados.join(", ")}${resto > 0 ? ` y ${resto} más` : ""}. Abajo, primero lo que dejó de reportar y después lo que tiene incidentes abiertos.`
            : "Todos los activos reportan y no tienen incidentes abiertos."}
        </span>
      </div>
      <form className="filtros-inventario" onSubmit={(e) => e.preventDefault()} role="search">
        <label>
          Buscar
          <input type="search" value={texto} onChange={(e) => setTexto(e.target.value)} placeholder="nombre o id" />
        </label>
        <label>
          Proveedor
          <select value={proveedor} onChange={(e) => setProveedor(e.target.value as Proveedor | "")}>
            <option value="">Todos</option>
            {[...new Set(activos.map((a) => a.proveedor))].sort().map((p) => (
              <option key={p} value={p}>
                {datosProveedor(p).nombre}
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
        <label>
          Estado
          <select value={estado} onChange={(e) => setEstado(e.target.value as EstadoActivo | "")}>
            <option value="">Todos</option>
            {ORDEN_ESTADO_ACTIVO.map((x) => (
              <option key={x} value={x}>
                {ESTADO_ACTIVO[x].texto}
              </option>
            ))}
          </select>
        </label>
      </form>
      {visibles.length === 0 && <SinDatos titulo="Ningún activo coincide" detalle="Cambia la búsqueda o los filtros." />}
      {grupos
        .filter((g) => g.lista.length > 0)
        .map(({ estado: e, lista }: { estado: EstadoActivo; lista: Activo[] }) => (
          <section key={e} className={`grupo-estado grupo-${e}`} aria-labelledby={`grupo-${e}`}>
            <h2 id={`grupo-${e}`}>
              <span className={`insignia insignia-${e}`} aria-hidden="true">
                {ESTADO_ACTIVO[e].simbolo}
              </span>
              {ESTADO_ACTIVO[e].plural} <span className="cuenta">{lista.length}</span>
            </h2>
            <ul className="lista-activos">
              {lista.map((a) => (
                <TarjetaActivo key={a.id} a={a} />
              ))}
            </ul>
          </section>
        ))}
    </ConColumna>
  );
}

export default function Inventario() {
  const r = useCarga((s) => api.activos(s), [], REFRESCO_MS);
  return (
    <Marco titulo="Infraestructura">
      <SegunCarga r={r} que="el inventario">
        {(activos) => <Contenido activos={activos} />}
      </SegunCarga>
    </Marco>
  );
}
