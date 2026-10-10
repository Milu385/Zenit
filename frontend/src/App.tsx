import { Cargando, SinDatos } from "./componentes/Estados";
import Marco from "./componentes/Marco";
import { coincide, Enlace, useRuta } from "./enrutador";
import Configuraciones from "./pantallas/Configuraciones";
import Costos from "./pantallas/Costos";
import DetalleActivo from "./pantallas/DetalleActivo";
import DetalleIncidente from "./pantallas/DetalleIncidente";
import Incidentes from "./pantallas/Incidentes";
import Inventario from "./pantallas/Inventario";
import Login from "./pantallas/Login";
import { ProveedorSesion, useSesion } from "./sesion";

// Rutas de las pantallas (pantallas-wireframe):
//   /                 1 inventario de activos
//   /activos/:id      2 detalle del activo
//   /incidentes       3 lista de incidentes
//   /incidentes/:id   4 detalle del incidente
//   /configuraciones  5 reporte de configuraciones
//   /costos           6 reporte de costos
//   (sin sesion)      9 inicio de sesion

function Rutas() {
  const { sesion } = useSesion();
  const { ruta } = useRuta();

  if (sesion.estado === "comprobando")
    return (
      <div className="pantalla-completa">
        <Cargando texto="Comprobando la sesion..." />
      </div>
    );
  if (sesion.estado === "anonimo") return <Login motivo={sesion.motivo} />;

  let p: Record<string, string> | null;
  if (ruta === "/") return <Inventario />;
  if ((p = coincide("/activos/:id", ruta))) return <DetalleActivo id={p.id} />;
  if (ruta === "/incidentes") return <Incidentes />;
  if ((p = coincide("/incidentes/:id", ruta))) return <DetalleIncidente id={p.id} />;
  if (ruta === "/configuraciones") return <Configuraciones />;
  if (ruta === "/costos") return <Costos />;
  return (
    <Marco titulo="No encontrada">
      <SinDatos titulo="Esta pagina no existe" detalle={`No hay nada en ${ruta}.`} />
      <p>
        <Enlace a="/">Volver a la infraestructura</Enlace>
      </p>
    </Marco>
  );
}

export default function App() {
  return (
    <ProveedorSesion>
      <Rutas />
    </ProveedorSesion>
  );
}
