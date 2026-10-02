import { useEffect, useMemo, useRef } from "react";
import uPlot from "uplot";
import type { RespuestaSerie } from "../api";

// Ocho colores categoricos en orden fijo, validados para daltonismo en modo
// claro y oscuro. Se leen de las variables CSS --serie-1..8 para que el tema
// los cambie en un solo lugar.
const MAXIMO = 8;

export function formatearValor(metrica: string, v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "sin dato";
  if (metrica.endsWith("_utilization")) return `${(v * 100).toFixed(1)} %`;
  const abs = Math.abs(v);
  if (abs >= 1e9) return `${(v / 1e9).toFixed(2)} G`;
  if (abs >= 1e6) return `${(v / 1e6).toFixed(2)} M`;
  if (abs >= 1e3) return `${(v / 1e3).toFixed(2)} k`;
  return Number.isInteger(v) ? String(v) : v.toFixed(3);
}

export function nombreSerie(etiquetas: Record<string, string>, metrica: string): string {
  const partes = Object.entries(etiquetas).map(([k, v]) => `${k}=${v}`);
  return partes.length ? partes.join(", ") : metrica;
}

const PASO_MS = 10_000;
// Igual que en la API: dos muestras a mas de 20 s son un hueco. El agente
// muestrea cada 10 s sin alinear a la epoca, asi que un intervalo vacio
// suelto no es perdida de datos.
const HUECO_MS = 2 * PASO_MS;

/**
 * Alinea las series en un solo eje x (segundos), con null donde falta.
 * Donde no hay datos por mas de 20 s se inserta un x con null en todas las
 * series: sin eso uPlot une los dos extremos y un corte se ve como linea.
 */
export function alinear(r: RespuestaSerie): uPlot.AlignedData {
  const series = r.series.slice(0, MAXIMO);
  const presentes = Array.from(new Set(series.flatMap((s) => s.puntos.map((p) => p[0])))).sort((a, b) => a - b);
  const xs: number[] = [];
  presentes.forEach((x, i) => {
    if (i > 0 && x - presentes[i - 1] > HUECO_MS) xs.push(presentes[i - 1] + PASO_MS);
    xs.push(x);
  });
  const indice = new Map(xs.map((x, i) => [x, i]));
  const ys = series.map((s) => {
    const fila: (number | null)[] = new Array(xs.length).fill(null);
    for (const [t, v] of s.puntos) fila[indice.get(t)!] = v;
    return fila;
  });
  return [xs.map((x) => x / 1000), ...ys];
}

/** Etiquetas del eje y con los decimales que pide la separacion entre marcas. */
function etiquetasY(metrica: string, vals: number[]): string[] {
  const pct = metrica.endsWith("_utilization");
  const escala = pct ? 0.01 : (() => {
    const max = Math.max(...vals.map(Math.abs));
    return max >= 1e9 ? 1e9 : max >= 1e6 ? 1e6 : max >= 1e3 ? 1e3 : 1;
  })();
  const sufijo = pct ? " %" : escala === 1e9 ? " G" : escala === 1e6 ? " M" : escala === 1e3 ? " k" : "";
  const paso = vals.length > 1 ? Math.abs(vals[1] - vals[0]) / escala : 1;
  const decimales = paso > 0 ? Math.min(6, Math.max(0, Math.ceil(-Math.log10(paso)))) : 0;
  return vals.map((v) => (v / escala).toFixed(decimales) + sufijo);
}

/** Etiquetas del eje x: con segundos cuando las marcas estan a menos de un minuto. */
function etiquetasX(vals: number[]): string[] {
  const paso = vals.length > 1 ? vals[1] - vals[0] : 60;
  const opciones: Intl.DateTimeFormatOptions =
    paso < 60 ? { hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" }
              : { hour: "2-digit", minute: "2-digit", hourCycle: "h23" };
  return vals.map((v) => new Date(v * 1000).toLocaleTimeString([], opciones));
}

function variable(el: HTMLElement, nombre: string): string {
  return getComputedStyle(el).getPropertyValue(nombre).trim();
}

function tooltip(raiz: HTMLElement, metrica: string, nombres: string[]): uPlot.Plugin {
  const caja = document.createElement("div");
  caja.className = "tooltip";
  caja.hidden = true;
  return {
    hooks: {
      init: (u) => {
        u.over.appendChild(caja);
      },
      setCursor: (u) => {
        const i = u.cursor.idx;
        if (i == null || u.cursor.left == null || u.cursor.left < 0) {
          caja.hidden = true;
          return;
        }
        caja.replaceChildren();
        const hora = document.createElement("div");
        hora.className = "tooltip-hora";
        hora.textContent = new Date((u.data[0][i] as number) * 1000).toLocaleTimeString();
        caja.appendChild(hora);
        nombres.forEach((nombre, s) => {
          const fila = document.createElement("div");
          fila.className = "tooltip-fila";
          const clave = document.createElement("span");
          clave.className = "tooltip-clave";
          clave.style.background = variable(raiz, `--serie-${s + 1}`);
          const valor = document.createElement("strong");
          valor.textContent = formatearValor(metrica, u.data[s + 1][i] as number | null);
          const etiqueta = document.createElement("span");
          etiqueta.className = "tooltip-etiqueta";
          etiqueta.textContent = nombre;
          fila.append(clave, valor, etiqueta);
          caja.appendChild(fila);
        });
        caja.hidden = false;
        const ancho = u.over.clientWidth;
        const x = u.cursor.left;
        caja.style.left = `${x > ancho / 2 ? x - caja.offsetWidth - 12 : x + 12}px`;
        caja.style.top = `8px`;
      },
    },
  };
}

export default function Grafica({ respuesta, refrescando }: { respuesta: RespuestaSerie; refrescando: boolean }) {
  const contenedor = useRef<HTMLDivElement>(null);
  const grafica = useRef<uPlot | null>(null);
  const datos = useMemo(() => alinear(respuesta), [respuesta]);
  const nombres = useMemo(
    () => respuesta.series.slice(0, MAXIMO).map((s) => nombreSerie(s.etiquetas, respuesta.metrica)),
    [respuesta],
  );
  // La grafica se rehace solo si cambia la forma (metrica o lineas). Un
  // refresco con los mismos nombres actualiza los datos y conserva el cuadro.
  const forma = `${respuesta.metrica}|${nombres.join("|")}`;

  useEffect(() => {
    const el = contenedor.current;
    if (!el) return;
    const crear = () => {
      grafica.current?.destroy();
      const ejes = variable(el, "--texto-secundario");
      const rejilla = variable(el, "--rejilla");
      const opciones: uPlot.Options = {
        width: el.clientWidth,
        height: 360,
        legend: { show: false },
        cursor: { drag: { x: false, y: false }, points: { size: 8 } },
        scales: { x: { time: true } },
        axes: [
          {
            stroke: ejes,
            grid: { stroke: rejilla, width: 1 },
            ticks: { show: false },
            space: 70,
            values: (_u, vals) => etiquetasX(vals),
          },
          {
            stroke: ejes,
            grid: { stroke: rejilla, width: 1 },
            ticks: { show: false },
            size: 70,
            values: (_u, vals) => etiquetasY(respuesta.metrica, vals),
          },
        ],
        series: [
          {},
          ...nombres.map((nombre, i) => ({
            label: nombre,
            stroke: variable(el, `--serie-${i + 1}`),
            width: 2,
            spanGaps: false, // un hueco en los datos se ve como hueco
            points: { show: false },
          })),
        ],
        plugins: [tooltip(el, respuesta.metrica, nombres)],
      };
      grafica.current = new uPlot(opciones, datos, el);
    };
    crear();

    const tamano = new ResizeObserver(() => grafica.current?.setSize({ width: el.clientWidth, height: 360 }));
    tamano.observe(el);
    const tema = window.matchMedia("(prefers-color-scheme: dark)");
    tema.addEventListener("change", crear);
    return () => {
      tamano.disconnect();
      tema.removeEventListener("change", crear);
      grafica.current?.destroy();
      grafica.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [forma]);

  useEffect(() => {
    grafica.current?.setData(datos);
  }, [datos]);

  return (
    <figure className={`grafica${refrescando ? " grafica-refrescando" : ""}`}>
      <div ref={contenedor} className="grafica-lienzo" aria-hidden="true" />
      {nombres.length > 1 && (
        <ul className="leyenda" aria-label="Leyenda">
          {nombres.map((n, i) => (
            <li key={n}>
              <span className="leyenda-linea" style={{ background: `var(--serie-${i + 1})` }} />
              {n}
            </li>
          ))}
        </ul>
      )}
    </figure>
  );
}
