import { useState } from "react";
import { api } from "../api";
import { SinDatos } from "../componentes/Estados";
import Marco from "../componentes/Marco";
import SegunCarga from "../componentes/SegunCarga";
import type { Activo, EstadoActivo, Proveedor } from "../contrato";
import { Enlace } from "../enrutador";
import { ESTADO_ACTIVO, hace, ORDEN_ESTADO_ACTIVO, porcentaje, PROVEEDOR, REQUIERE_ATENCION } from "../formato";
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
  const p = PROVEEDOR[a.proveedor];
  const e = ESTADO_ACTIVO[a.estado];
  return (
    <li>
      <Enlace a={`/activos/${encodeURIComponent(a.id)}`} className={`tarjeta-activo estado-${a.estado}`}>
        <span className={`sigla sigla-${a.proveedor}`} title={p.nombre}>
          {p.sigla}
        </span>
        <span className="tarjeta-activo-cuerpo">
          <span className="tarjeta-activo-nombre">{a.nombre}</span>
          <span className="tarjeta-activo-meta">
            {a.id} · {p.nombre}
            {a.region ? ` · ${a.region}` : ""} · entorno {a.entorno}
          </span>
          <span className={`insignia insignia-${a.estado}`}>
            <span aria-hidden="true">{e.simbolo}</span> {e.texto}
          </span>
        </span>
        <span className="tarjeta-activo-uso">
          {/* el contrato solo trae CPU: promedio de los ultimos 5 min */}
          <Barra etiqueta="CPU 5 min" valor={a.cpu_reciente} />
        </span>
        <span className="tarjeta-activo-pie">
          <span>Ultima recepcion {hace(a.ultima_recepcion)}</span>
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

function ResumenHibrido({ activos }: { activos: Activo[] }) {
  const proveedores = (Object.keys(PROVEEDOR) as Proveedor[]).filter((p) => activos.some((a) => a.proveedor === p));
  return (
    <section className="panel panel-azul resumen-hibrido" aria-labelledby="titulo-hibrido">
      <div className="panel-blanco">
        <h2 id="titulo-hibrido">Inventario hibrido</h2>
        <ul>
          {proveedores.map((p) => {
            const suyos = activos.filter((a) => a.proveedor === p);
            return (
              <li key={p}>
                <span>
                  <span className="resumen-nombre">{PROVEEDOR[p].nombre}</span>
                  <span className="resumen-meta">
                    {suyos.length} {suyos.length === 1 ? "activo" : "activos"}
                  </span>
                </span>
                <span
                  className="cuadros"
                  role="img"
                  aria-label={ORDEN_ESTADO_ACTIVO.map(
                    (e) => `${suyos.filter((a) => a.estado === e).length} ${ESTADO_ACTIVO[e].plural.toLowerCase()}`,
                  ).join(", ")}
                >
                  {suyos
                    .slice()
                    .sort((x, y) => ORDEN_ESTADO_ACTIVO.indexOf(x.estado) - ORDEN_ESTADO_ACTIVO.indexOf(y.estado))
                    .map((a) => (
                      <span key={a.id} className={`cuadro cuadro-${a.estado}`} title={`${a.nombre}: ${ESTADO_ACTIVO[a.estado].texto}`} />
                    ))}
                </span>
              </li>
            );
          })}
        </ul>
      </div>
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
        titulo="Todavia no hay activos en el inventario"
        detalle="Agrega el nodo al catalogo (deploy/catalogo/catalogo.json) y espera su primera ingesta."
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
  const conProblemas = activos.filter((a) => REQUIERE_ATENCION.includes(a.estado)).length;

  return (
    <div className="rejilla-con-lateral">
      <div>
        <div className={`franja ${conProblemas ? "franja-alerta" : "franja-bien"}`} role="status">
          <span className="franja-punto" aria-hidden="true" />
          <span>
            <strong>
              {conProblemas ? `${conProblemas} de ${vigentes} activos requieren atencion` : `${vigentes} activos, operacion estable`}
            </strong>
            <br />
            {conProblemas
              ? "Abajo, primero lo que dejo de reportar y despues lo que tiene incidentes abiertos."
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
              {(Object.keys(PROVEEDOR) as Proveedor[]).map((p) => (
                <option key={p} value={p}>
                  {PROVEEDOR[p].nombre}
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
        {visibles.length === 0 && <SinDatos titulo="Ningun activo coincide" detalle="Cambia la busqueda o los filtros." />}
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
      </div>
      <ResumenHibrido activos={activos} />
    </div>
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
