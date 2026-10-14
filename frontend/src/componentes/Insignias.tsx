import type { EstadoIncidente, Severidad } from "../contrato";
import { ESTADO_INCIDENTE, SEVERIDAD } from "../formato";

// Los cinco estados de un incidente se distinguen de un vistazo por forma,
// texto y color a la vez (pantalla 3).
export function InsigniaIncidente({ estado }: { estado: EstadoIncidente }) {
  const e = ESTADO_INCIDENTE[estado];
  return (
    <span className={`insignia insignia-inc-${estado}`} title={e.ayuda}>
      <span aria-hidden="true">{e.simbolo}</span> {e.texto}
    </span>
  );
}

export function InsigniaSeveridad({ severidad }: { severidad: Severidad }) {
  return <span className={`severidad severidad-${severidad}`}>{SEVERIDAD[severidad].texto}</span>;
}
