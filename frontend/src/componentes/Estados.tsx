// Los cuatro estados de la vista (criterio 4 de H-007): carga, error, sin
// datos y datos. Cada uno se distingue por texto, no solo por color.

export function Cargando({ texto = "Cargando la serie..." }: { texto?: string }) {
  return (
    <div className="estado" role="status" aria-live="polite">
      <span className="giro" aria-hidden="true" />
      <p>{texto}</p>
    </div>
  );
}

export function ErrorVista({ mensaje, reintentar }: { mensaje: string; reintentar?: () => void }) {
  return (
    <div className="estado estado-error" role="alert">
      <p className="estado-titulo">No se pudo cargar</p>
      <p>{mensaje}</p>
      {reintentar && (
        <button type="button" onClick={reintentar}>
          Reintentar
        </button>
      )}
    </div>
  );
}

export function SinDatos({ titulo, detalle }: { titulo: string; detalle: string }) {
  return (
    <div className="estado" role="status">
      <p className="estado-titulo">{titulo}</p>
      <p>{detalle}</p>
    </div>
  );
}

// Los otros dos estados transversales de pantallas-wireframe: sin permiso y
// datos desactualizados. Vacio es SinDatos con el texto de cada pantalla.

export function SinPermiso({ que, mensaje }: { que: string; mensaje?: string }) {
  return (
    <div className="estado estado-sin-permiso" role="status">
      <span className="estado-icono" aria-hidden="true">
        &#128274;
      </span>
      <p className="estado-titulo">Tu rol no da acceso a {que}</p>
      <p>{mensaje ?? "Si lo necesitas, pidele al administrador que revise tu rol."}</p>
    </div>
  );
}

export function AvisoDesactualizado({ desde, motivo, reintentar }: { desde: Date | null; motivo: string; reintentar?: () => void }) {
  return (
    <div className="aviso-desactualizado" role="status">
      <span aria-hidden="true">&#9888;</span>
      <span>
        <strong>Datos desactualizados.</strong> Lo que ves es de {desde ? desde.toLocaleTimeString() : "antes"}; no se
        pudo actualizar: {motivo}
      </span>
      {reintentar && (
        <button type="button" className="boton-texto" onClick={reintentar}>
          Reintentar
        </button>
      )}
    </div>
  );
}
