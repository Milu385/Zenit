import { useCallback, useEffect, useRef, useState } from "react";
import { api, ErrorApi, type RespuestaSerie } from "../api";
import { nombreMetrica } from "../formato";
import { Cargando, ErrorVista, SinDatos, SinPermiso } from "./Estados";
import Grafica from "./Grafica";
import TablaSerie from "./TablaSerie";

// Vista de serie (H-007), ahora como componente: la usan el detalle del activo
// (pantalla 2) y la evidencia del detalle del incidente (pantalla 4).
// - activo: fijo, lo decide la pantalla.
// - metrica: si viene, se fija y no se muestra el selector.
// - ventana: si viene, se fija el rango (sin "en vivo"); si no, rango relativo.

const RANGOS = [
  { minutos: 15, texto: "15 min" },
  { minutos: 60, texto: "1 h" },
  { minutos: 360, texto: "6 h" },
  { minutos: 1440, texto: "24 h" },
];
const REFRESCO_MS = 10_000;

type Carga<T> =
  | { estado: "cargando" }
  | { estado: "error"; mensaje: string }
  | { estado: "listo"; datos: T };

function mensaje(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}
const abortado = (e: unknown) => e instanceof Error && e.name === "AbortError";

type Props = {
  activo: string;
  metrica?: string;
  ventana?: { desde: Date; hasta: Date };
  titulo?: string;
};

export default function VistaSerie({ activo, metrica: metricaFija, ventana, titulo }: Props) {
  const [intento, setIntento] = useState(0);
  const reintentar = useCallback(() => setIntento((n) => n + 1), []);

  const [metricas, setMetricas] = useState<Carga<string[]>>({ estado: "cargando" });
  const [metrica, setMetrica] = useState(metricaFija ?? "");
  const [minutos, setMinutos] = useState(60);
  const [sinPermiso, setSinPermiso] = useState(false);
  // rango de la consulta: la ventana fija, o los ultimos `minutos`
  // se depende de los instantes y no del objeto, que puede ser nuevo en cada render
  const vDesde = ventana?.desde.getTime();
  const vHasta = ventana?.hasta.getTime();
  const rango = useCallback(() => {
    if (vDesde != null && vHasta != null) return { desde: new Date(vDesde), hasta: new Date(vHasta) };
    const hasta = new Date();
    return { desde: new Date(hasta.getTime() - minutos * 60_000), hasta };
  }, [vDesde, vHasta, minutos]);
  const [dimensiones, setDimensiones] = useState<Record<string, string[]>>({});
  // Para que activo y metrica se resolvio la agrupacion. La serie no se pide
  // hasta que coincide: si no, al cambiar de metrica se pediria con la
  // agrupacion de la anterior y la API respondería 422.
  const [dimsDe, setDimsDe] = useState("");
  const [agrupar, setAgrupar] = useState("");
  const [serie, setSerie] = useState<Carga<RespuestaSerie>>({ estado: "cargando" });
  const [refrescando, setRefrescando] = useState(false);
  const [enVivo, setEnVivo] = useState(!ventana);
  const [verTabla, setVerTabla] = useState(false);

  // ---- metricas del activo
  useEffect(() => {
    if (!activo) return;
    const ctl = new AbortController();
    setMetricas({ estado: "cargando" });
    api
      .metricas(activo, ctl.signal)
      .then((datos) => {
        setMetricas({ estado: "listo", datos });
        if (metricaFija) return;
        setMetrica((actual) =>
          datos.includes(actual) ? actual : datos.includes("system_cpu_utilization") ? "system_cpu_utilization" : datos[0] ?? "",
        );
      })
      // Un fallo aqui es un error, no "sin datos": la vista lo tiene que decir.
      .catch((e) => {
        if (abortado(e)) return;
        if (e instanceof ErrorApi && e.estado === 403) setSinPermiso(true);
        setMetricas({ estado: "error", mensaje: mensaje(e) });
      });
    return () => ctl.abort();
  }, [activo, metricaFija, intento]);

  useEffect(() => {
    if (metricaFija) setMetrica(metricaFija);
  }, [metricaFija]);

  // ---- dimensiones de la metrica: deciden la agrupacion por defecto
  useEffect(() => {
    if (!activo || !metrica) return;
    const ctl = new AbortController();
    const { desde, hasta } = rango();
    api
      .dimensiones(activo, metrica, desde, hasta, ctl.signal)
      .then((d) => {
        setDimensiones(d);
        setAgrupar((actual) => (actual in d ? actual : "state" in d ? "state" : ""));
        setDimsDe(`${activo}|${metrica}`);
      })
      .catch((e) => {
        if (abortado(e)) return;
        setDimensiones({});
        setAgrupar("");
        setDimsDe(`${activo}|${metrica}`);
      });
    return () => ctl.abort();
  }, [activo, metrica, rango, intento]);

  // ---- la serie, con refresco periodico que conserva el cuadro
  const peticion = useRef(0);
  const enCurso = useRef<AbortController | null>(null);
  const listaLaAgrupacion = dimsDe === `${activo}|${metrica}`;
  const cargarSerie = useCallback(
    (primera: boolean) => {
      if (!activo || !metrica || !listaLaAgrupacion) return;
      // una consulta lenta no se apila con la siguiente
      enCurso.current?.abort();
      const ctl = new AbortController();
      enCurso.current = ctl;
      const id = ++peticion.current;
      const { desde, hasta } = rango();
      if (primera) setSerie({ estado: "cargando" });
      else setRefrescando(true);
      api
        .serie(activo, metrica, desde, hasta, agrupar, ctl.signal)
        .then((datos) => id === peticion.current && setSerie({ estado: "listo", datos }))
        .catch((e) => !abortado(e) && id === peticion.current && setSerie({ estado: "error", mensaje: mensaje(e) }))
        .finally(() => id === peticion.current && setRefrescando(false));
    },
    [activo, metrica, rango, agrupar, listaLaAgrupacion],
  );

  useEffect(() => {
    cargarSerie(true);
    return () => enCurso.current?.abort();
  }, [cargarSerie, intento]);

  useEffect(() => {
    if (!enVivo) return;
    const t = window.setInterval(() => cargarSerie(false), REFRESCO_MS);
    return () => window.clearInterval(t);
  }, [enVivo, cargarSerie]);

  // ---- render
  const cuerpo = (() => {
    if (sinPermiso) return <SinPermiso que="las series de este activo" />;
    if (metricas.estado === "cargando") return <Cargando texto="Buscando metricas del activo..." />;
    if (metricas.estado === "error") return <ErrorVista mensaje={metricas.mensaje} reintentar={reintentar} />;
    // con metrica fija (evidencia de un incidente) la ventana puede ser vieja
    // y la metrica no estar entre las de la ultima hora: no es "sin datos"
    if (!metricaFija && metricas.datos.length === 0)
      return (
        <SinDatos
          titulo="Este activo no ha reportado en la ultima hora"
          detalle="Revisa que el agente del nodo este arriba y que la ingesta no lo marque como huerfano."
        />
      );
    if (serie.estado === "cargando" || !listaLaAgrupacion) return <Cargando />;
    if (serie.estado === "error") return <ErrorVista mensaje={serie.mensaje} reintentar={reintentar} />;
    const r = serie.datos;
    if (r.series.every((s) => s.puntos.length === 0))
      return (
        <SinDatos
          titulo="Sin datos en este rango"
          detalle={
            ventana
              ? `${metrica} no tiene puntos para ${activo} entre ${ventana.desde.toLocaleTimeString()} y ${ventana.hasta.toLocaleTimeString()}.`
              : `${metrica} no tiene puntos para ${activo} en los ultimos ${minutos} minutos.`
          }
        />
      );
    return (
      <>
        <Grafica respuesta={r} refrescando={refrescando} />
        <p className="pie">
          Resolucion {r.resolucion} · {r.intervalos_esperados - r.intervalos_vacios} de {r.intervalos_esperados}{" "}
          intervalos con datos
          {r.intervalos_vacios > 0 && ` · ${r.intervalos_vacios} vacios`}
          {r.series_omitidas > 0 && ` · ${r.series_omitidas} series no mostradas (maximo 8)`}
        </p>
        {verTabla && <TablaSerie respuesta={r} />}
      </>
    );
  })();

  const listos = metricas.estado === "listo";
  return (
    <div className="vista-serie">
      <form className="filtros" onSubmit={(e) => e.preventDefault()} aria-label="Filtros de la serie">
        {!ventana && (
          <label>
            Rango
            <select value={minutos} onChange={(e) => setMinutos(Number(e.target.value))}>
              {RANGOS.map((r) => (
                <option key={r.minutos} value={r.minutos}>
                  {r.texto}
                </option>
              ))}
            </select>
          </label>
        )}
        {!metricaFija && (
          <label>
            Metrica
            <select value={metrica} onChange={(e) => setMetrica(e.target.value)} disabled={!listos}>
              {metricas.estado === "listo" &&
                metricas.datos.map((m) => (
                  <option key={m} value={m}>
                    {nombreMetrica(m)} ({m})
                  </option>
                ))}
            </select>
          </label>
        )}
        <label>
          Separar por
          <select value={agrupar} onChange={(e) => setAgrupar(e.target.value)} disabled={!listos}>
            <option value="">(promedio de todo)</option>
            {Object.keys(dimensiones).map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </label>
        {!ventana && (
          <label className="casilla">
            <input type="checkbox" checked={enVivo} onChange={(e) => setEnVivo(e.target.checked)} />
            En vivo
          </label>
        )}
        <label className="casilla">
          <input type="checkbox" checked={verTabla} onChange={(e) => setVerTabla(e.target.checked)} />
          Ver tabla
        </label>
      </form>

      <div className="contenido">
        {metrica && listos && <h3 className="titulo-serie">{titulo ?? `${nombreMetrica(metrica)} · ${metrica}`}</h3>}
        {cuerpo}
      </div>
    </div>
  );
}
