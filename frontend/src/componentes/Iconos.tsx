// Iconos de trazo, dibujados aqui para no cargar imagenes ni una libreria.
// Siguen los de "diseños zenit/Iconos secciones": linea fina y un acento.

type P = { className?: string };
const base = { width: 22, height: 22, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.6, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true };

export function Logo({ className, tallo = "currentColor" }: P & { tallo?: string }) {
  return (
    <svg className={className} viewBox="0 0 100 100" aria-hidden="true">
      <ellipse cx="50" cy="56" rx="26" ry="10" transform="rotate(-18 50 56)" fill="none" stroke="var(--azul)" strokeWidth="3.2" />
      <line x1="50" y1="27" x2="50" y2="52" stroke={tallo} strokeWidth="2.6" />
      <circle cx="50" cy="27" r="6.5" fill="var(--amarillo)" />
      <circle cx="50" cy="52" r="3" fill={tallo} />
    </svg>
  );
}

export function IconoInfraestructura({ className }: P) {
  return (
    <svg {...base} className={className}>
      <rect x="3" y="4" width="18" height="6" rx="3" />
      <rect x="3" y="14" width="18" height="6" rx="3" />
      <circle cx="7" cy="7" r="1" fill="var(--amarillo)" stroke="none" />
      <circle cx="7" cy="17" r="1" fill="var(--amarillo)" stroke="none" />
    </svg>
  );
}

export function IconoIncidentes({ className }: P) {
  return (
    <svg {...base} className={className}>
      <path d="M3 18 8 12l4 3 4-7 5 5" />
      <path d="M3 21h18" />
      <circle cx="16" cy="8" r="1.4" fill="var(--amarillo)" stroke="none" />
    </svg>
  );
}

export function IconoConfiguracion({ className }: P) {
  return (
    <svg {...base} className={className}>
      <circle cx="12" cy="12" r="3.2" />
      <path d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3M5.3 5.3l2.1 2.1M16.6 16.6l2.1 2.1M5.3 18.7l2.1-2.1M16.6 7.4l2.1-2.1" />
    </svg>
  );
}

export function IconoCostos({ className }: P) {
  return (
    <svg {...base} className={className}>
      <ellipse cx="9" cy="17" rx="5" ry="2" />
      <path d="M4 17v-3c0-1.1 2.2-2 5-2s5 .9 5 2v3" />
      <rect x="12" y="3" width="9" height="11" rx="1.5" />
      <path d="M14.5 10l2-2 1.5 1.5L20 7" stroke="var(--amarillo)" />
    </svg>
  );
}

export function IconoUsuario({ className }: P) {
  return (
    <svg {...base} className={className} width={34} height={34}>
      <circle cx="12" cy="12" r="10" />
      <circle cx="12" cy="10" r="3.4" />
      <path d="M5.5 18.5c1.6-2.4 3.9-3.6 6.5-3.6s4.9 1.2 6.5 3.6" />
    </svg>
  );
}

export function IconoOjo({ abierto }: { abierto: boolean }) {
  return (
    <svg {...base}>
      <path d="M2 12s3.6-6.5 10-6.5S22 12 22 12s-3.6 6.5-10 6.5S2 12 2 12Z" />
      <circle cx="12" cy="12" r="2.8" />
      {!abierto && <path d="M4 20 20 4" />}
    </svg>
  );
}
