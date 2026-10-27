"""Matriz de permisos contra la API desplegada (P-GOB-01, repetida con las rutas nuevas).

    python3 scripts/seguridad/probar_permisos.py --base https://20.3.217.87 --ca tls/ca.crt \
        --usuario administrador=juanjose --usuario operador=lucia \
        --usuario seguridad=seguridad1 --usuario finanzas=finanzas1

Pide la clave de cada usuario sin mostrarla. Para cada ruta y cada rol revisa
que un rol sin el permiso reciba 403, que un rol con el permiso no lo reciba,
y que sin sesion la respuesta sea 401. Las rutas que escriben (POST) se
prueban solo con los roles que NO tienen permiso: el 403 sale antes de tocar
nada, asi que la prueba no cambia datos. Solo lectura sobre la plataforma
propia; no se usa contra sistemas de terceros.
"""
import argparse
import getpass
import json
import ssl
import sys
import urllib.error
import urllib.request

# (metodo, ruta, permiso). {activo} e {incidente} se llenan con ids reales.
RUTAS = [
    ("GET", "/api/activos", "activos:ver"),
    ("GET", "/api/activos/{activo}", "activos:ver"),
    ("GET", "/api/activos/{activo}/metricas", "activos:ver"),
    ("GET", "/api/activos/{activo}/series/system_cpu_utilization", "activos:ver"),
    ("GET", "/api/activos/{activo}/registros", "registros:ver"),
    ("GET", "/api/activos/{activo}/trazas", "registros:ver"),
    ("GET", "/api/activos/{activo}/correlacion", "registros:ver"),
    ("GET", "/api/fuentes", "fuentes:ver"),
    ("GET", "/api/fuentes/huerfanas", "fuentes:ver"),
    ("GET", "/api/catalogo/descubrimientos", "fuentes:ver"),
    ("GET", "/api/incidentes", "incidentes:ver"),
    ("GET", "/api/incidentes/{incidente}", "incidentes:ver"),
    ("GET", "/api/incidentes/{incidente}/evidencia", "incidentes:ver"),
    ("POST", "/api/incidentes/{incidente}/estado", "incidentes:gestionar"),
    ("POST", "/api/incidentes/{incidente}/diagnostico", "incidentes:gestionar"),
    ("GET", "/api/diagnostico/consumo", "diagnostico:consumo"),
    ("GET", "/api/gobernanza/configuraciones", "configuraciones:ver"),
    ("GET", "/api/gobernanza/costos", "costos:ver"),
    ("GET", "/api/gobernanza/costos/analisis", "costos:ver"),
]
# copia de backend/zenit/gobernanza/permisos.py: si cambia alla, cambia aqui
MATRIZ = {
    "activos:ver": {"administrador", "operador", "seguridad", "finanzas"},
    "incidentes:ver": {"administrador", "operador", "seguridad"},
    "incidentes:gestionar": {"administrador", "operador"},
    "configuraciones:ver": {"administrador", "seguridad"},
    "costos:ver": {"administrador", "finanzas"},
    "registros:ver": {"administrador", "operador", "seguridad"},
    "fuentes:ver": {"administrador"},
    "diagnostico:consumo": {"administrador"},
}


def pedir(base, ctx, metodo, ruta, token=None, cuerpo=None):
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else (b"{}" if metodo == "POST" else None)
    cab = {"Content-Type": "application/json"}
    if token:
        cab["Authorization"] = "Bearer " + token
    req = urllib.request.Request(base + ruta, data=datos, method=metodo, headers=cab)
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def principal():
    p = argparse.ArgumentParser()
    p.add_argument("--base", required=True)
    p.add_argument("--ca", help="certificado de la CA del servidor (tls/ca.crt)")
    p.add_argument("--usuario", action="append", required=True, help="rol=usuario, uno por rol")
    a = p.parse_args()
    ctx = ssl.create_default_context(cafile=a.ca) if a.ca else ssl.create_default_context()
    tokens = {}
    for par in a.usuario:
        rol, usuario = par.split("=", 1)
        clave = getpass.getpass(f"clave de {usuario} ({rol}): ")
        estado, cuerpo = pedir(a.base, ctx, "POST", "/api/sesion", cuerpo={"usuario": usuario, "clave": clave})
        if estado != 200:
            sys.exit(f"no se pudo entrar como {usuario}: {estado}")
        tokens[rol] = json.loads(cuerpo)["token"]
    admin = tokens.get("administrador") or next(iter(tokens.values()))
    _, cuerpo = pedir(a.base, ctx, "GET", "/api/activos", admin)
    activo = json.loads(cuerpo)[0]["id"]
    _, cuerpo = pedir(a.base, ctx, "GET", "/api/incidentes", admin)
    lista = json.loads(cuerpo)
    incidente = lista[0]["id"] if lista else 1

    fallos = 0
    print(f"{'ruta':<62} {'sin sesion':>10} " + " ".join(f"{r:>13}" for r in tokens))
    for metodo, plantilla, permiso in RUTAS:
        ruta = plantilla.format(activo=activo, incidente=incidente)
        fila = []
        e, _ = pedir(a.base, ctx, metodo, ruta)
        ok = e == 401
        fallos += not ok
        fila.append(f"{e}{'' if ok else ' !'}")
        for rol, token in tokens.items():
            permitido = rol in MATRIZ[permiso]
            if metodo == "POST" and permitido:
                fila.append("(no se prueba)")
                continue
            e, _ = pedir(a.base, ctx, metodo, ruta, token)
            ok = (e != 403) if permitido else (e == 403)
            fallos += not ok
            fila.append(f"{e}{'' if ok else ' !'}")
        print(f"{metodo + ' ' + plantilla:<62} {fila[0]:>10} " + " ".join(f"{x:>13}" for x in fila[1:]))
    print(f"\n{fallos} combinaciones fuera de lo esperado" if fallos else "\nTodo como se espera: 403 sin permiso, 401 sin sesion.")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(principal())
