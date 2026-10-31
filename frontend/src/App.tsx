import { Cargando, SinDatos } from "./componentes/Estados";
import Marco from "./componentes/Marco";
import { coincide, Enlace, useRuta } from "./enrutador";
import Configuraciones from "./pantallas/Configuraciones";
import Costos from "./pantallas/Costos";
import DetalleActivo from "./pantallas/DetalleActivo";
import DetalleIncidente from "./pantallas/DetalleIncidente";
import Fuentes from "./pantallas/Fuentes";
import Incidentes from "./pantallas/Incidentes";
import Inventario from "./pantallas/Inventario";
import Login from "./pantallas/Login";
import Privacidad from "./pantallas/Privacidad";
import { ProveedorSesion, useSesion } from "./sesion";

// Rutas de las pantallas (pantallas-wireframe):
//   /                 1 inventario de activos
//   /activos/:id      2 detalle del activo
//   /incidentes       3 lista de incidentes
//   /incidentes/:id   4 detalle del incidente
//   /configuraciones  5 reporte de configuraciones
//   /costos           6 reporte de costos
//   /fuentes          7 fuentes, huerfanos y descubrimiento (H-053)
//   (sin sesion)      9 inicio de sesion
//   /privacidad       politica de tratamiento de datos, publica (Ley 1581)

function Rutas() {
  const { sesion } = useSesion();
  const { ruta } = useRuta();

  if (ruta === "/privacidad") return <Privacidad />; // publica, va antes de mirar la sesion
  if (sesion.estado === "comprobando")
    return (
      <div className="pantalla-completa">
        <Cargando texto="Comprobando la sesión..." />
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
  if (ruta === "/fuentes") return <Fuentes />;
  return (
    <Marco titulo="No encontrada">
      <SinDatos titulo="Esta página no existe" detalle={`No hay nada en ${ruta}.`} />
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
