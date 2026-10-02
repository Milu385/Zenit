"""Doble de prueba de InfluxDB 3 Core.

No es InfluxDB. Imita los cuatro endpoints que usa Zenit con dos piezas
reales detras:

- un analizador estricto del protocolo de linea, que rechaza con 400 lo que
  el motor rechazaria (etiqueta vacia, flotante invalido, cambio de tipo de
  una columna, linea sin campo);
- DataFusion, que es el motor de consulta que usa InfluxDB 3 por dentro, asi
  que el SQL que escribe la capa de repositorio se ejecuta de verdad,
  parametros incluidos.

Las reglas del analizador se contrastaron con core/influxdb_line_protocol del
repositorio de InfluxDB: escapes, barra invertida final, tabulador, rango de
la marca de tiempo y largo de las etiquetas. No es el analizador del motor;
es una aproximacion estricta.

Lo que este doble NO verifica: retencion, persistencia, rendimiento ni los
detalles del formato JSON de salida. Eso se verifica contra el motor real en
el nodo de plataforma (ver README, seccion de verificacion).
"""
import json
import re
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pyarrow as pa
from datafusion import SessionConfig, SessionContext

_DICCIONARIO = pa.dictionary(pa.int32(), pa.string())


class ErrorLinea(ValueError):
    pass


def _leer_hasta(texto: str, i: int, paradas: str, especiales: str):
    """Lee un token con escapes. Devuelve (token, indice de la parada)."""
    salida = []
    while i < len(texto):
        c = texto[i]
        if c == "\\" and i + 1 < len(texto) and (texto[i + 1] in especiales or texto[i + 1] == "\\"):
            salida.append(texto[i + 1])
            i += 2
            continue
        if c in paradas:
            break
        salida.append(c)
        i += 1
    token = "".join(salida)
    if token.endswith("\\"):
        # mismo comportamiento que el motor: EndsWithBackslash
        raise ErrorLinea(f"termina en barra invertida: {token!r}")
    return token, i


_FLOTANTE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")


def analizar_linea(linea: str):
    """(medicion, etiquetas, campos, tiempo_ns) o ErrorLinea."""
    if "\n" in linea:
        raise ErrorLinea("salto de linea dentro de una linea")
    if "\t" in linea:
        # el motor toma el tabulador como separador y no admite escaparlo
        raise ErrorLinea("tabulador dentro de una linea")
    medicion, i = _leer_hasta(linea, 0, ", ", ", ")
    if not medicion:
        raise ErrorLinea("medicion vacia")
    etiquetas = {}
    while i < len(linea) and linea[i] == ",":
        clave, i = _leer_hasta(linea, i + 1, "=", ",= ")
        if i >= len(linea) or linea[i] != "=":
            raise ErrorLinea(f"etiqueta sin '=': {clave!r}")
        valor, i = _leer_hasta(linea, i + 1, ", ", ",= ")
        if not clave or not valor:
            raise ErrorLinea(f"etiqueta vacia: {clave!r}={valor!r}")
        etiquetas[clave] = valor
    if i >= len(linea) or linea[i] != " ":
        raise ErrorLinea("falta el conjunto de campos")
    campos = {}
    i += 1
    while True:
        clave, i = _leer_hasta(linea, i, "=", ",= ")
        if i >= len(linea) or linea[i] != "=":
            raise ErrorLinea(f"campo sin '=': {clave!r}")
        crudo, i = _leer_hasta(linea, i + 1, ", ", "")
        if crudo.endswith("i") and re.match(r"^[+-]?\d+i$", crudo):
            campos[clave] = ("entero", int(crudo[:-1]))
        elif _FLOTANTE.match(crudo):
            campos[clave] = ("flotante", float(crudo))
        else:
            raise ErrorLinea(f"valor de campo no soportado por el doble: {crudo!r}")
        if i < len(linea) and linea[i] == ",":
            i += 1
            continue
        break
    tiempo = None
    if i < len(linea):
        if linea[i] != " ":
            raise ErrorLinea("basura despues de los campos")
        resto = linea[i + 1:]
        if not re.match(r"^-?\d+$", resto):
            raise ErrorLinea(f"marca de tiempo invalida: {resto!r}")
        tiempo = int(resto)
        if not -(2**63) <= tiempo < 2**63:
            raise ErrorLinea("marca de tiempo fuera del rango de 64 bits")
    for v in etiquetas.values():
        if len(v.encode()) > 64 * 1024:
            raise ErrorLinea("valor de etiqueta de mas de 64 KiB")
    return medicion, etiquetas, campos, tiempo


class Almacen:
    def __init__(self, token: str):
        self.token = token
        self.bases: dict = {}            # bd -> {"retencion": str, "tablas": {tabla: [filas]}}
        self.tipos: dict = {}            # (bd, tabla, columna) -> tipo
        self.escrituras = 0
        self.fallar_proximas = 0         # para simular un almacen caido (503)
        self._candado = threading.Lock()

    # ------------------------------------------------------------ escritura
    def escribir(self, bd: str, cuerpo: str):
        lineas = [l for l in cuerpo.split("\n") if l.strip()]
        filas = []
        for n, l in enumerate(lineas, 1):
            try:
                med, etq, campos, t = analizar_linea(l)
            except ErrorLinea as e:
                return 400, f"linea {n}: {e}"
            if t is None:
                return 400, f"linea {n}: Zenit siempre envia marca de tiempo"
            for c, (tipo, _) in campos.items():
                previo = self.tipos.get((bd, med, c))
                if previo and previo != tipo:
                    return 400, f"linea {n}: la columna {c} es {previo} y llega {tipo}"
            filas.append((med, etq, campos, t))
        with self._candado:
            base = self.bases.setdefault(bd, {"retencion": None, "tablas": {}})
            for med, etq, campos, t in filas:
                for c, (tipo, _) in campos.items():
                    self.tipos[(bd, med, c)] = tipo
                fila = {"time": t, **etq, **{c: v for c, (_, v) in campos.items()}}
                base["tablas"].setdefault(med, []).append(fila)
            self.escrituras += 1
        return 204, ""

    # ------------------------------------------------------------ consulta
    def _contexto(self, bd: str) -> SessionContext:
        ctx = SessionContext(SessionConfig().with_information_schema(True))
        base = self.bases.get(bd, {"tablas": {}})
        for tabla, filas in base["tablas"].items():
            columnas = sorted({k for f in filas for k in f})
            datos = {}
            for col in columnas:
                valores = [f.get(col) for f in filas]
                if col == "time":
                    datos[col] = pa.array(valores, pa.timestamp("ns"))
                elif any(isinstance(v, float) for v in valores):
                    datos[col] = pa.array(valores, pa.float64())
                elif any(isinstance(v, int) for v in valores):
                    datos[col] = pa.array(valores, pa.int64())
                else:
                    datos[col] = pa.array(valores, pa.string()).cast(_DICCIONARIO)
            ctx.register_record_batches(tabla, [pa.table(datos).to_batches()])
        return ctx

    def consultar(self, bd: str, sql: str, params: dict):
        if bd not in self.bases:
            return 404, json.dumps({"error": f"database not found: {bd}"})
        with self._candado:
            ctx = self._contexto(bd)
        try:
            filas = ctx.sql(sql, param_values=params or None).to_pylist()
        except Exception as e:  # el motor real responde 400 en errores de plan
            return 400, json.dumps({"error": str(e)})
        salida = []
        for f in filas:
            limpio = {}
            for k, v in f.items():
                if v is None:
                    continue  # el escritor JSON de Arrow omite los nulos
                if hasattr(v, "isoformat"):
                    v = v.isoformat()
                limpio[k] = v
            salida.append(limpio)
        return 200, json.dumps(salida)


def levantar(token: str = "apiv3_prueba", puerto: int = 0):
    """Arranca el doble en un hilo. Devuelve (almacen, url, servidor)."""
    almacen = Almacen(token)

    class Manejador(BaseHTTPRequestHandler):
        def _autorizado(self):
            if self.headers.get("Authorization") != f"Bearer {almacen.token}":
                self._responder(401, '{"error":"unauthorized"}')
                return False
            return True

        def _responder(self, estado, cuerpo="", tipo="application/json"):
            datos = cuerpo.encode()
            self.send_response(estado)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(datos)))
            self.end_headers()
            self.wfile.write(datos)

        def _cuerpo(self):
            n = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(n).decode("utf-8")

        def do_GET(self):
            ruta = urlparse(self.path)
            if ruta.path in ("/health", "/ping"):
                return self._responder(200, "OK", "text/plain")
            self._responder(404)

        def do_POST(self):
            ruta = urlparse(self.path)
            if not self._autorizado():
                return
            if ruta.path == "/api/v3/write_lp":
                if almacen.fallar_proximas > 0:
                    almacen.fallar_proximas -= 1
                    return self._responder(503, '{"error":"no disponible"}')
                q = parse_qs(ruta.query)
                bd = q.get("db", [""])[0]
                if bd not in almacen.bases:
                    return self._responder(404, json.dumps({"error": f"database not found: {bd}"}))
                estado, texto = almacen.escribir(bd, self._cuerpo())
                return self._responder(estado, json.dumps({"error": texto}) if texto else "")
            if ruta.path == "/api/v3/query_sql":
                cuerpo = json.loads(self._cuerpo())
                estado, texto = almacen.consultar(cuerpo["db"], cuerpo["q"], cuerpo.get("params"))
                return self._responder(estado, texto)
            if ruta.path == "/api/v3/configure/database":
                cuerpo = json.loads(self._cuerpo())
                if cuerpo["db"] in almacen.bases:
                    return self._responder(409, '{"error":"already exists"}')
                almacen.bases[cuerpo["db"]] = {"retencion": cuerpo.get("retention_period"), "tablas": {}}
                return self._responder(200, "")
            self._responder(404)

        def do_PUT(self):
            if not self._autorizado():
                return
            if urlparse(self.path).path == "/api/v3/configure/database":
                cuerpo = json.loads(self._cuerpo())
                if cuerpo["db"] not in almacen.bases:
                    return self._responder(404, "")
                almacen.bases[cuerpo["db"]]["retencion"] = cuerpo.get("retention_period")
                return self._responder(200, "")
            self._responder(404)

        def log_message(self, *_):
            pass

    servidor = ThreadingHTTPServer(("127.0.0.1", puerto), Manejador)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    return almacen, f"http://127.0.0.1:{servidor.server_address[1]}", servidor


def ahora_ns() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1e9)


if __name__ == "__main__":
    # Uso manual: python pruebas/falso_influx.py 18181
    import sys
    import time as _t

    _, url, _ = levantar(puerto=int(sys.argv[1]) if len(sys.argv) > 1 else 18181)
    print("doble de InfluxDB en", url, "token apiv3_prueba", flush=True)
    while True:
        _t.sleep(3600)
