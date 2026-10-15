"""Cliente HTTP minimo para ClickHouse, el motor de registros y trazas (ADR-007).

Dos operaciones por la interfaz HTTP (puerto 8123), que es la misma que usaria
el exportador del Collector:

- INSERT ... FORMAT JSONEachRow, un objeto JSON por linea;
- SELECT con parametros tipados ({nombre:Tipo} en el SQL, param_nombre en la
  URL): los valores del usuario nunca se interpolan.

Como ClienteInflux, es la unica puerta al motor. La ingesta escribe con el y
la capa de repositorio (zenit/repositorio/analitico.py) consulta con el.
"""
from typing import Any, Mapping, Optional

import httpx

from zenit.influx import ErrorAlmacen

BASE = "zenit"
# Errores de ClickHouse que significan "lo que mandaste esta mal", no "el
# servidor no puede": se rechaza el lote en vez de reintentarlo para siempre.
# CANNOT_PARSE_*, INCORRECT_DATA, TYPE_MISMATCH, UNKNOWN_FIELD...
_DATOS_INVALIDOS = {6, 7, 25, 26, 27, 38, 41, 53, 72, 117, 130, 131}


class ErrorClickHouse(ErrorAlmacen):
    pass


class ClienteClickHouse:
    def __init__(self, url: str, usuario: str = "default", clave: str = "", tiempo_limite: float = 30.0):
        self._http = httpx.Client(
            base_url=url.rstrip("/"),
            headers={"X-ClickHouse-User": usuario, "X-ClickHouse-Key": clave},
            timeout=tiempo_limite,
        )

    def cerrar(self) -> None:
        self._http.close()

    def _enviar(self, sql: str, cuerpo: Optional[bytes] = None, parametros: Optional[Mapping[str, Any]] = None,
                base: str = BASE):
        params = {"database": base, "output_format_json_quote_64bit_integers": "0"}
        for k, v in (parametros or {}).items():
            params[f"param_{k}"] = str(v)
        try:
            if cuerpo is None:
                r = self._http.post("/", params=params, content=sql.encode("utf-8"))
            else:
                r = self._http.post("/", params={**params, "query": sql}, content=cuerpo)
        except httpx.HTTPError as e:
            raise ErrorClickHouse(f"sin respuesta de ClickHouse: {e}") from e
        if r.status_code >= 300:
            codigo = r.headers.get("X-ClickHouse-Exception-Code")
            estado = 400 if codigo and codigo.isdigit() and int(codigo) in _DATOS_INVALIDOS else r.status_code
            if r.status_code in (401, 403, 516):         # credenciales: configuracion, no datos
                estado = 401
            raise ErrorClickHouse(f"ClickHouse respondio {r.status_code} ({codigo}): {r.text[:300]}", estado)
        return r

    # ------------------------------------------------------------ escritura
    def escribir(self, tabla: str, filas_json: str) -> None:
        """Inserta un lote de lineas JSONEachRow en zenit.<tabla>. Todo o nada:
        input_format_allow_errors_num queda en 0."""
        self._enviar(f"INSERT INTO {BASE}.{tabla} FORMAT JSONEachRow", filas_json.encode("utf-8"))

    # ------------------------------------------------------------ consulta
    def consultar(self, sql: str, parametros: Optional[Mapping[str, Any]] = None) -> list[dict]:
        r = self._enviar(sql + "\nFORMAT JSON", parametros=parametros)
        return r.json().get("data", [])

    def ejecutar(self, sql: str, parametros: Optional[Mapping[str, Any]] = None) -> None:
        self._enviar(sql, parametros=parametros)

    def aplicar_esquema(self, sql: str) -> int:
        """Ejecuta un archivo de esquema sentencia por sentencia (la interfaz
        HTTP admite una por peticion). Devuelve cuantas ejecuto."""
        sentencias = [x.strip() for x in quitar_comentarios(sql).split(";") if x.strip()]
        for sentencia in sentencias:
            self._enviar(sentencia, base="default")
        return len(sentencias)

    def disponible(self) -> bool:
        try:
            return self._http.get("/ping").status_code == 200
        except httpx.HTTPError:
            return False


def quitar_comentarios(sql: str) -> str:
    return "\n".join(l.split("--", 1)[0] if not l.lstrip().startswith("--") else "" for l in sql.splitlines())
