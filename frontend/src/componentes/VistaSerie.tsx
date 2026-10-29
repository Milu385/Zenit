import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ErrorApi, type RespuestaSerie } from "../api";
import { claseDe, nombreMetrica } from "../formato";
import { Cargando, ErrorVista, SinDatos, SinPermiso } from "./Estados";
import Grafica from "./Grafica";
import TablaSerie from "./TablaSerie";

// Vista de serie (H-007), ahora como componente: la usan el detalle del activo
// (pantalla 2) y la evidencia del detalle del incidente (pantalla 4).
// - activo: fijo, lo decide la pantalla.
// - metrica: si viene, se fija y no se muestra el selector.
// - ventana: si viene, se fija el rango (sin "en vivo"); si no, rango relativo.
// - filtros: dimensiones con las que abre filtrada (las del incidente, p. ej.
//   device=sda1). Solo se aplican las que la metrica tiene; se pueden quitar.
// - Las metricas acumuladas (contadores) se dibujan como tasa por segundo,
//   calculada aqui; un salto negativo (el contador se reinicio) se descarta.

const RANGOS = [
  { minutos: 15, texto: "15 min" },
  { minutos: 60, texto: "1 h" },
  { minutos: 360, texto: "6 h" },
  { minutos: 1440, texto: "24 h" },
  // desde aqui la API responde con los resumenes por minuto (H-019)
  { minutos: 7 * 1440, texto: "7 días" },
  { minutos: 30 * 1440, texto: "30 días" },
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
  filtros?: Record<string, string>;
};

/** Contador -> tasa por segundo, serie por serie. Los puntos van en milisegundos. */
export function aTasa(r: RespuestaSerie): RespuestaSerie {
  return {
    ...r,
    series: r.series.map((s) => {
      const puntos: [number, number][] = [];
      for (let i = 1; i < s.puntos.length; i++) {
        const [t0, v0] = s.puntos[i - 1];
        const [t1, v1] = s.puntos[i];
        const segundos = (t1 - t0) / 1000;
        if (segundos <= 0 || v0 == null || v1 == null) continue;
        const delta = v1 - v0;
        if (delta < 0) continue; // el contador se reinicio: ese punto no dice cuanto paso
        puntos.push([t1, delta / segundos]);
      }
      return { ...s, puntos };
    }),
  };
}

const SIN_FILTROS: Record<string, string> = {};

export default function VistaSerie({ activo, metrica: metricaFija, ventana, titulo, filtros = SIN_FILTROS }: Props) {
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
  const hayFiltros = Object.keys(filtros).length > 0;
  const [filtrar, setFiltrar] = useState(hayFiltros);
  const [verTasa, setVerTasa] = useState(true);
  const acumulada = !!metrica && claseDe(metrica) === "acumulada";
  // solo las dimensiones que esta metrica tiene: otra metrica relacionada puede no tener device
  const filtrosAplicables = useMemo(() => {
    if (!filtrar) return SIN_FILTROS;
    const salida: Record<string, string> = {};
    for (const [k, v] of Object.entries(filtros)) if (k in dimensiones) salida[k] = v;
    return Object.keys(salida).length ? salida : SIN_FILTROS;
  }, [filtrar, filtros, dimensiones]);
  const claveFiltros = JSON.stringify(filtrosAplicables);

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
        // filtrada por las dimensiones del incidente, abre con esa sola serie
        const filtradas = filtrar ? Object.keys(filtros).filter((k) => k in d) : [];
        setAgrupar((actual) =>
          actual in d && !filtradas.includes(actual) ? actual : filtradas.length ? "" : "state" in d ? "state" : "",
        );
        setDimsDe(`${activo}|${metrica}`);
      })
      .catch((e) => {
        if (abortado(e)) return;
        setDimensiones({});
        setAgrupar("");
        setDimsDe(`${activo}|${metrica}`);
      });
    return () => ctl.abort();
  }, [activo, metrica, rango, intento, filtrar, filtros]);

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
        .serie(activo, metrica, desde, hasta, agrupar, ctl.signal, JSON.parse(claveFiltros))
        .then((datos) => id === peticion.current && setSerie({ estado: "listo", datos }))
        .catch((e) => !abortado(e) && id === peticion.current && setSerie({ estado: "error", mensaje: mensaje(e) }))
        .finally(() => id === peticion.current && setRefrescando(false));
    },
    [activo, metrica, rango, agrupar, listaLaAgrupacion, claveFiltros],
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
    if (metricas.estado === "cargando") return <Cargando texto="Buscando métricas del activo..." />;
    if (metricas.estado === "error") return <ErrorVista mensaje={metricas.mensaje} reintentar={reintentar} />;
    // con metrica fija (evidencia de un incidente) la ventana puede ser vieja
    // y la metrica no estar entre las de la ultima hora: no es "sin datos"
    if (!metricaFija && metricas.datos.length === 0)
      return (
        <SinDatos
          titulo="Este activo no ha reportado en la última hora"
          detalle="Revisa que el agente del nodo esté arriba y que la ingesta no lo marque como huérfano."
        />
      );
    if (serie.estado === "cargando" || !listaLaAgrupacion) return <Cargando />;
    if (serie.estado === "error") return <ErrorVista mensaje={serie.mensaje} reintentar={reintentar} />;
    const tasa = acumulada && verTasa;
    const r = tasa ? aTasa(serie.datos) : serie.datos;
    if (r.series.every((s) => s.puntos.length === 0))
      return (
        <SinDatos
          titulo="Sin datos en este rango"
          detalle={
            ventana
              ? `${metrica} no tiene puntos para ${activo} entre ${ventana.desde.toLocaleTimeString()} y ${ventana.hasta.toLocaleTimeString()}.`
              : `${metrica} no tiene puntos para ${activo} en las últimas ${RANGOS.find((x) => x.minutos === minutos)?.texto ?? `${minutos} min`}.`
          }
        />
      );
    return (
      <>
        {r.fuente_degradada && (
          <p className="aviso-desactualizado" role="alert">
            Datos desactualizados: la fuente no reporta desde{" "}
            {r.ultima_recepcion ? new Date(r.ultima_recepcion).toLocaleString("es-CO") : "hace más de 5 minutos"}. Lo
            que ves puede no reflejar el estado actual del activo.
          </p>
        )}
        <Grafica respuesta={r} refrescando={refrescando} tasa={tasa} />
        <p className="pie">
          Resolución {r.resolucion}
          {r.intervalo_s && r.intervalo_s > 10 ? ` (un punto cada ${r.intervalo_s / 60} min)` : ""} ·{" "}
          {r.intervalos_esperados - r.intervalos_vacios} de {r.intervalos_esperados} intervalos con datos
          {r.intervalos_vacios > 0 && ` · ${r.intervalos_vacios} vacíos`}
          {(r.intervalos_incompletos ?? 0) > 0 && ` · ${r.intervalos_incompletos} incompletos`}
          {r.series_omitidas > 0 && ` · ${r.series_omitidas} series no mostradas (máximo 8)`}
        </p>
        {verTabla && <TablaSerie respuesta={r} tasa={tasa} />}
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
            Métrica
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
            <option value="">{Object.keys(filtrosAplicables).length ? "(solo la serie filtrada)" : "(promedio de todo)"}</option>
            {Object.keys(dimensiones)
              .filter((d) => !(d in filtrosAplicables))
              .map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </label>
        {hayFiltros && (
          <label className="casilla">
            <input type="checkbox" checked={filtrar} onChange={(e) => setFiltrar(e.target.checked)} />
            Solo {Object.entries(filtros)
              .map(([k, v]) => `${k}=${v}`)
              .join(", ")}
          </label>
        )}
        {acumulada && (
          <label className="casilla">
            <input type="checkbox" checked={verTasa} onChange={(e) => setVerTasa(e.target.checked)} />
            Como tasa por segundo
          </label>
        )}
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
        {metrica && listos && (
          <h3 className="titulo-serie">
            {/* el nombre en lenguaje normal; el de la metrica, pequeno al lado, para quien lo busque */}
            {titulo ?? nombreMetrica(metrica)}
            {!titulo && <span className="metrica-tecnica">{metrica}</span>}
          </h3>
        )}
        {cuerpo}
      </div>
    </div>
  );
}
