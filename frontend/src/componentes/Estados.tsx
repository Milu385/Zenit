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
