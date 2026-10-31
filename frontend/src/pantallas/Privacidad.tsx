import { useEffect, useRef, type MouseEvent } from "react";
import { Logo } from "../componentes/Iconos";

// Politica de tratamiento de datos personales (Ley 1581 de 2012, art. 17 y 18;
// Decreto 1377 de 2013, art. 13 a 15). Pagina publica: App.tsx la sirve antes
// de mirar la sesion. El texto es el de textos-legales.md, seccion 1, tal cual.
// Sin estilos ni scripts externos: la plataforma tiene politica de seguridad
// de contenido.

const CONSERVACION: [string, string][] = [
  ["Registros y trazas", "7 días"],
  ["Métricas detalladas y su resumen por minuto", "30 días"],
  ["Resumen por hora", "180 días"],
  ["Hipótesis y evidencia redactada", "Hasta el cierre del proyecto, 12 de noviembre de 2026"],
];

const CORREO = "juanjose.tamayo@upb.edu.co";

export default function Privacidad() {
  const titulo = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    document.title = "Política de tratamiento de datos · Zenit";
    titulo.current?.focus({ preventScroll: true });
    return () => {
      document.title = "Zenit";
    };
  }, []);

  // "← Volver": a la pagina anterior si se llego desde Zenit; si no, al inicio
  const volver = (e: MouseEvent<HTMLAnchorElement>) => {
    if (window.history.length > 1 && document.referrer.startsWith(window.location.origin)) {
      e.preventDefault();
      window.history.back();
    }
  };

  return (
    <div className="privacidad">
      <header className="privacidad-cabecera">
        <a href="/" className="privacidad-logo" aria-label="Zenit, inicio">
          <Logo tallo="#fff" />
        </a>
        <a href="/" className="privacidad-volver" onClick={volver}>
          ← Volver
        </a>
      </header>
      <main className="privacidad-texto">
        <h1 ref={titulo} tabIndex={-1}>
          Política de tratamiento de datos personales de Zenit
        </h1>
        <p>Vigente desde el 30 de octubre de 2026.</p>

        <h2>1. Quién trata los datos</h2>
        <p>
          Zenit es un proyecto académico de la Facultad de Ingeniería de la Universidad Pontificia Bolivariana, sede
          Medellín, desarrollado por Juan José Tamayo Ospina, María Lucía Cadavid Martínez y Daniel Cardona González,
          quienes actúan como responsables del tratamiento. Contacto: <a href={`mailto:${CORREO}`}>{CORREO}</a>.
        </p>

        <h2>2. Qué datos tratamos</h2>
        <ul>
          <li>
            De los usuarios de la plataforma: nombre de usuario, rol, una derivación segura de la contraseña (nunca la
            contraseña en texto claro) y la fecha de sus acciones sobre los incidentes.
          </li>
          <li>
            De los servidores observados: métricas de funcionamiento, registros y trazas. Estos registros pueden contener
            direcciones IP, nombres de usuario del sistema u otros identificadores.
          </li>
        </ul>

        <h2>3. Para qué</h2>
        <p>
          Para monitorear la infraestructura, detectar comportamientos anómalos, proponer hipótesis sobre sus causas,
          atribuir costos y controlar el acceso a la plataforma. No usamos los datos para fines comerciales ni para
          perfilar a personas.
        </p>

        <h2>4. Proveedores y datos fuera de Colombia</h2>
        <p>
          La plataforma funciona en Microsoft Azure, en Estados Unidos. Para proponer hipótesis enviamos fragmentos de
          evidencia al servicio de modelos de lenguaje de Anthropic, también en Estados Unidos. Antes de enviarlos, Zenit
          reemplaza por marcadores los nombres de los equipos, las direcciones IP, los identificadores de la nube, los
          correos y las credenciales. Ambos proveedores actúan como encargados y tratan los datos por cuenta nuestra,
          según sus condiciones de protección de datos. Según sus condiciones comerciales, Anthropic no usa estos datos
          para entrenar sus modelos.
        </p>

        <h2>5. Cuánto tiempo los guardamos</h2>
        <table className="privacidad-tabla">
          <caption className="solo-lector">Tiempo de conservación de cada tipo de dato</caption>
          <thead>
            <tr>
              <th scope="col">Datos</th>
              <th scope="col">Tiempo</th>
            </tr>
          </thead>
          <tbody>
            {CONSERVACION.map(([dato, tiempo]) => (
              <tr key={dato}>
                <th scope="row">{dato}</th>
                <td>{tiempo}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p>Al terminar el proyecto, la plataforma y sus datos se eliminan.</p>

        <h2>6. Cómo los protegemos</h2>
        <ul>
          <li>Conexiones cifradas, y los servidores se autentican con certificados.</li>
          <li>Acceso por roles y contraseñas derivadas.</li>
          <li>Las credenciales de la nube solo permiten lectura.</li>
          <li>Los datos se borran automáticamente al vencer su plazo.</li>
        </ul>

        <h2>7. Sus derechos</h2>
        <p>
          Como titular puede conocer, actualizar, rectificar y solicitar la supresión de sus datos, revocar la
          autorización y presentar quejas ante la Superintendencia de Industria y Comercio (Ley 1581 de 2012, art. 8).
        </p>

        <h2>8. Cómo ejercerlos</h2>
        <p>
          Escriba a <a href={`mailto:${CORREO}`}>{CORREO}</a> indicando su nombre, la solicitud y un medio de respuesta.
        </p>
        <ul>
          <li>
            <strong>Consultas:</strong> respondemos en un máximo de diez días hábiles.
          </li>
          <li>
            <strong>Reclamos:</strong> respondemos en un máximo de quince días hábiles.
          </li>
        </ul>
        <p>Son los plazos de los artículos 14 y 15 de la Ley 1581 de 2012.</p>
      </main>
    </div>
  );
}
