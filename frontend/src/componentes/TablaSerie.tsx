import type { RespuestaSerie } from "../api";
import { formatearValor, nombreSerie } from "./Grafica";

// Vista en tabla: los mismos datos sin depender del color ni del cursor.
export default function TablaSerie({ respuesta }: { respuesta: RespuestaSerie }) {
  const filas = respuesta.series.map((s) => {
    const valores = s.puntos.map((p) => p[1]);
    const ultimo = s.puntos[s.puntos.length - 1];
    return {
      nombre: nombreSerie(s.etiquetas, respuesta.metrica),
      ultimo: ultimo ? ultimo[1] : null,
      hora: ultimo ? new Date(ultimo[0]).toLocaleTimeString() : "",
      minimo: valores.length ? Math.min(...valores) : null,
      maximo: valores.length ? Math.max(...valores) : null,
      promedio: valores.length ? valores.reduce((a, b) => a + b, 0) / valores.length : null,
      n: valores.length,
    };
  });
  const m = respuesta.metrica;
  return (
    <table className="tabla">
      <caption>Resumen del rango, a resolucion de {respuesta.resolucion}</caption>
      <thead>
        <tr>
          <th scope="col">Serie</th>
          <th scope="col">Ultimo valor</th>
          <th scope="col">Hora</th>
          <th scope="col">Minimo</th>
          <th scope="col">Promedio</th>
          <th scope="col">Maximo</th>
          <th scope="col">Intervalos</th>
        </tr>
      </thead>
      <tbody>
        {filas.map((f) => (
          <tr key={f.nombre}>
            <th scope="row">{f.nombre}</th>
            <td>{formatearValor(m, f.ultimo)}</td>
            <td>{f.hora}</td>
            <td>{formatearValor(m, f.minimo)}</td>
            <td>{formatearValor(m, f.promedio)}</td>
            <td>{formatearValor(m, f.maximo)}</td>
            <td>{f.n}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
