import { useState, type FormEvent } from "react";
import { ErrorApi } from "../api";
import { IconoOjo, Logo } from "../componentes/Iconos";
import { useSesion } from "../sesion";

// Pantalla 9. Del wireframe se quitan "Olvide mi contrasena" y "Crea una
// cuenta": las cuentas las crea el administrador (RF-GOB-02) y no hay correo
// para restablecer claves. Mostrarlos seria prometer algo que no existe.

export default function Login({ motivo }: { motivo?: string }) {
  const { entrar } = useSesion();
  const [usuario, setUsuario] = useState("");
  const [clave, setClave] = useState("");
  const [recordar, setRecordar] = useState(false);
  const [verClave, setVerClave] = useState(false);
  const [enviando, setEnviando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const enviar = async (e: FormEvent) => {
    e.preventDefault();
    if (!usuario || !clave) {
      setError("Escribe tu usuario y tu clave.");
      return;
    }
    setEnviando(true);
    setError(null);
    try {
      await entrar({ usuario: usuario.trim(), clave }, recordar);
    } catch (ex) {
      setError(
        ex instanceof ErrorApi && ex.estado === 401 ? "Usuario o clave incorrectos." : ex instanceof Error ? ex.message : String(ex),
      );
      setClave("");
    } finally {
      setEnviando(false);
    }
  };

  return (
    <div className="login">
      <section className="login-portada">
        <div className="login-anillos" aria-hidden="true">
          <span />
          <span />
          <span />
          <span />
        </div>
        <h1>
          Bienvenido
          <br />a zenit!
        </h1>
        <Logo className="login-logo" tallo="#fff" />
        <p className="login-lema">Monitorea y analiza tu infraestructura desde un solo lugar.</p>
        <p className="login-texto">
          Centraliza el estado, rendimiento, trafico y consumo de tus servidores locales y servicios en la nube.
        </p>
      </section>
      <section className="login-panel">
        <form className="login-tarjeta" onSubmit={enviar} noValidate>
          <h2>Iniciar sesion</h2>
          <p className="login-sub">Accede a tu panel de monitoreo.</p>
          {motivo && !error && (
            <p className="login-aviso" role="status">
              {motivo}
            </p>
          )}
          <label htmlFor="usuario">Usuario</label>
          <input
            id="usuario"
            autoComplete="username"
            value={usuario}
            onChange={(e) => setUsuario(e.target.value)}
            aria-invalid={!!error}
            autoFocus
          />
          <label htmlFor="clave">Clave</label>
          <div className="login-clave">
            <input
              id="clave"
              type={verClave ? "text" : "password"}
              autoComplete="current-password"
              value={clave}
              onChange={(e) => setClave(e.target.value)}
              aria-invalid={!!error}
            />
            <button
              type="button"
              className="login-ojo"
              onClick={() => setVerClave((v) => !v)}
              aria-label={verClave ? "Ocultar clave" : "Mostrar clave"}
              aria-pressed={verClave}
            >
              <IconoOjo abierto={!verClave} />
            </button>
          </div>
          <label className="login-recordar">
            <input type="checkbox" checked={recordar} onChange={(e) => setRecordar(e.target.checked)} />
            Mantener la sesion iniciada en este equipo
          </label>
          {error && (
            <p className="login-error" role="alert">
              {error}
            </p>
          )}
          <button type="submit" className="boton-primario" disabled={enviando}>
            {enviando ? "Entrando..." : "Iniciar sesion"}
          </button>
          <p className="login-pie">Las cuentas las crea el administrador de Zenit.</p>
        </form>
      </section>
    </div>
  );
}
