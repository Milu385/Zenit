import { api, ErrorApi } from "../api";
import { SinDatos } from "../componentes/Estados";
import { InsigniaIncidente, InsigniaSeveridad } from "../componentes/Insignias";
import Marco from "../componentes/Marco";
import SegunCarga from "../componentes/SegunCarga";
import VistaSerie from "../componentes/VistaSerie";
import type { DetalleActivo as Detalle } from "../contrato";
import { Enlace } from "../enrutador";
import { ABIERTO, dinero, ESTADO_ACTIVO, hace, porcentaje, PROVEEDOR, tituloIncidente } from "../formato";
import { useSesion } from "../sesion";
import { useCarga } from "../useCarga";

// Pantalla 2. Ficha del activo, sus incidentes abiertos, su costo si el rol lo
// ve, y la vista de serie de H-007 reutilizada con el activo fijo.

function IncidentesDelActivo({ activo }: { activo: string }) {
  const r = useCarga((s) => api.incidentes({ activo }, s), [activo]);
  if (r.carga.estado !== "listo") return null; // la ficha no depende de esto
  const abiertos = r.carga.datos.filter((i) => ABIERTO.includes(i.estado));
  if (abiertos.length === 0) return <p className="nota">Sin incidentes abiertos en este activo.</p>;
  return (
    <ul className="incidentes-activo">
      {abiertos.map((i) => (
        <li key={i.id}>
          <InsigniaIncidente estado={i.estado} /> <InsigniaSeveridad severidad={i.severidad} />{" "}
          <Enlace a={`/incidentes/${i.id}`}>{tituloIncidente(i)}</Enlace> <span className="meta">{hace(i.inicio)}</span>
        </li>
      ))}
    </ul>
  );
}

function CostoDelActivo({ activo }: { activo: string }) {
  // sin fechas, la API toma su periodo por defecto
  const r = useCarga((s) => api.costos("", "", s), []);
  if (r.carga.estado !== "listo") return null;
  const { moneda, periodo, por_activo } = r.carga.datos;
  const suyos = por_activo.filter((c) => c.activo === activo);
  const total = suyos.reduce((n, c) => n + c.costo, 0);
  return (
    <div>
      <dt>Costo atribuido</dt>
      <dd>
        {suyos.length ? dinero(total, moneda) : "sin cargos"}
        <span className="meta-dato">
          {periodo.desde} a {periodo.hasta}
          {suyos.length ? ` · ${[...new Set(suyos.map((c) => c.fuente))].join(", ")}` : ""}
        </span>
      </dd>
    </div>
  );
}

function Ficha({ a }: { a: Detalle }) {
  const { puede } = useSesion();
  const e = ESTADO_ACTIVO[a.estado];
  return (
    <>
      <Enlace a="/" className="volver">
        ← Infraestructura
      </Enlace>
      <section className="panel panel-blanco ficha-activo">
        <span className={`sigla sigla-${a.proveedor}`}>{PROVEEDOR[a.proveedor].sigla}</span>
        <div>
          <h2>{a.nombre}</h2>
          <p className="meta">
            {a.id} · {PROVEEDOR[a.proveedor].nombre}
            {a.region ? ` · ${a.region}` : ""} · entorno {a.entorno}
          </p>
          <p className="meta">Claves nativas: {a.claves.join(", ") || "ninguna"}</p>
        </div>
        <dl className="ficha-datos">
          <div>
            <dt>Estado</dt>
            <dd>
              <span className={`insignia insignia-${a.estado}`}>
                <span aria-hidden="true">{e.simbolo}</span> {e.texto}
              </span>
            </dd>
          </div>
          <div>
            <dt>CPU, ultimos 5 min</dt>
            <dd>{porcentaje(a.cpu_reciente)}</dd>
          </div>
          <div>
            <dt>Ultima recepcion</dt>
            <dd>{hace(a.ultima_recepcion)}</dd>
          </div>
          {puede("costos:ver") && <CostoDelActivo activo={a.id} />}
        </dl>
      </section>
      {puede("incidentes:ver") && (
        <section className="panel panel-amarillo">
          <h3>Incidentes abiertos</h3>
          <IncidentesDelActivo activo={a.id} />
        </section>
      )}
      <section className="panel panel-blanco">
        <h3>Senales</h3>
        <VistaSerie activo={a.id} />
      </section>
    </>
  );
}

export default function DetalleActivo({ id }: { id: string }) {
  // 404 no es un error de carga: es un activo que no esta en el catalogo
  const r = useCarga(
    (s) =>
      api.activo(id, s).catch((e) => {
        if (e instanceof ErrorApi && e.estado === 404) return null;
        throw e;
      }),
    [id],
    30_000,
  );
  return (
    <Marco titulo="Detalle del activo">
      <SegunCarga r={r} que="el activo">
        {(a) =>
          a ? (
            <Ficha a={a} />
          ) : (
            <SinDatos titulo={`No hay un activo "${id}" en el inventario`} detalle="Puede que lo hayan sacado del catalogo." />
          )
        }
      </SegunCarga>
    </Marco>
  );
}
