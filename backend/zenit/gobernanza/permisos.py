"""Matriz de permisos por rol (RF-GOB-01, RNF-SEG-04), seccion 2 del
contrato de la API v1.1.

Denegacion por defecto en dos niveles:
- un rol solo tiene los permisos que aparecen aqui;
- una ruta de la API que no declara permiso responde 403 a todos, tambien al
  administrador (ver zenit.api.autorizacion).
"""
ROLES = ("administrador", "operador", "seguridad", "finanzas")

# permiso -> roles que lo tienen. Las filas siguen la tabla del contrato.
MATRIZ: dict[str, frozenset[str]] = {
    # Activos, series, metricas
    "activos:ver": frozenset({"administrador", "operador", "seguridad", "finanzas"}),
    # Incidentes: ver
    "incidentes:ver": frozenset({"administrador", "operador", "seguridad"}),
    # Incidentes: confirmar, descartar, pedir diagnostico
    "incidentes:gestionar": frozenset({"administrador", "operador"}),
    # Reporte de configuraciones
    "configuraciones:ver": frozenset({"administrador", "seguridad"}),
    # Reporte de costos
    "costos:ver": frozenset({"administrador", "finanzas"}),
    # Verdad de referencia del laboratorio
    "laboratorio:ver": frozenset({"administrador"}),
    # Usuarios
    "usuarios:gestionar": frozenset({"administrador"}),
    # --- fuera de la tabla del contrato: entradas de datos de la gobernanza.
    # Se proponen al equipo (NOTAS.md); mientras tanto, solo quien ya ve el reporte.
    "configuraciones:enviar": frozenset({"administrador"}),
    "costos:importar": frozenset({"administrador", "finanzas"}),
}

PERMISOS = tuple(MATRIZ)


def permitido(rol: str, permiso: str) -> bool:
    return rol in MATRIZ.get(permiso, frozenset())


def permisos_de(rol: str) -> list[str]:
    return sorted(p for p, roles in MATRIZ.items() if rol in roles)
