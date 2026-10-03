"""Cliente HTTP minimo para InfluxDB 3 Core.

Solo los cuatro endpoints que usa Zenit, verificados contra el codigo fuente
del motor (influxdb3_server/src/all_paths.rs e influxdb3_types/src/http.rs):

- POST /api/v3/write_lp?db=&precision=      escritura en protocolo de linea
- POST /api/v3/query_sql                     {db, q, format, params}
- POST /api/v3/configure/database            {db, retention_period}
- PUT  /api/v3/configure/database            {db, retention_period}

Toda la comunicacion con el motor pasa por esta clase. La capa de repositorio
la usa para consultar y el punto de entrada para escribir; nadie mas habla
con InfluxDB directamente.
"""
from typing import Any, Mapping, Optional

import httpx


class ErrorInflux(Exception):
    """El motor respondio con error o no respondio."""

    def __init__(self, mensaje: str, estado: Optional[int] = None):
        super().__init__(mensaje)
        self.estado = estado


class ClienteInflux:
    def __init__(self, url: str, token: str, tiempo_limite: float = 10.0):
        self._http = httpx.Client(
            base_url=url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=tiempo_limite,
        )

    def cerrar(self) -> None:
        self._http.close()

    # ------------------------------------------------------------ escritura
    def escribir(self, bd: str, lineas: str) -> None:
        """Escribe un lote. Lanza ErrorInflux si el motor no lo acepta entero.

        accept_partial=false: si una linea es invalida se rechaza el lote
        completo, en vez de aceptar una parte en silencio. Un lote rechazado se
        ve en los contadores; una perdida parcial no se veria en ningun lado.
        """
        try:
            r = self._http.post(
                "/api/v3/write_lp",
                params={"db": bd, "precision": "ns", "accept_partial": "false"},
                content=lineas.encode("utf-8"),
                headers={"Content-Type": "text/plain; charset=utf-8"},
            )
        except httpx.HTTPError as e:
            raise ErrorInflux(f"sin respuesta del almacen: {e}") from e
        if r.status_code >= 300:
            raise ErrorInflux(f"escritura rechazada: {r.status_code} {r.text[:300]}", r.status_code)

    # ------------------------------------------------------------ consulta
    def consultar(
        self, bd: str, sql: str, parametros: Optional[Mapping[str, Any]] = None
    ) -> list[dict]:
        cuerpo: dict[str, Any] = {"db": bd, "q": sql, "format": "json"}
        if parametros:
            cuerpo["params"] = dict(parametros)
        try:
            r = self._http.post("/api/v3/query_sql", json=cuerpo)
        except httpx.HTTPError as e:
            raise ErrorInflux(f"sin respuesta del almacen: {e}") from e
        if r.status_code == 404 and "not found" in r.text.lower():
            # base o tabla inexistente: para quien consulta es "sin datos"
            return []
        if r.status_code >= 300:
            raise ErrorInflux(f"consulta rechazada: {r.status_code} {r.text[:300]}", r.status_code)
        if not r.content.strip():
            return []
        return r.json()

    # ------------------------------------------------------------ administracion
    def crear_base(self, bd: str, retencion: str) -> bool:
        """Crea la base. Devuelve False si ya existia (409), sin error."""
        r = self._http.post(
            "/api/v3/configure/database",
            json={"db": bd, "retention_period": retencion},
        )
        if r.status_code == 409:
            return False
        if r.status_code >= 300:
            raise ErrorInflux(f"no se pudo crear {bd}: {r.status_code} {r.text[:300]}", r.status_code)
        return True

    def fijar_retencion(self, bd: str, retencion: str) -> None:
        """PUT /api/v3/configure/database. Presente en Core al menos desde la 3.4:
        la retencion ya no queda fija al crear la base."""
        r = self._http.put(
            "/api/v3/configure/database",
            json={"db": bd, "retention_period": retencion},
        )
        if r.status_code >= 300:
            raise ErrorInflux(f"no se pudo fijar la retencion de {bd}: {r.status_code} {r.text[:300]}", r.status_code)

    def disponible(self) -> bool:
        try:
            return self._http.get("/health").status_code == 200
        except httpx.HTTPError:
            return False
