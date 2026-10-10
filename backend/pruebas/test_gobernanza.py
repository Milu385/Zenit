"""Gobernanza: roles (P-GOB-01, P-GOB-02, P-GOB-06), configuraciones (P-GOB-04)
y costos (P-GOB-05)."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from sesion_prueba import CLAVE, SECRETO, con_sesion, opciones, usuarios_de_prueba
from zenit.api.main import crear_app
from zenit.gobernanza import claves, configuraciones, costos
from zenit.gobernanza.permisos import MATRIZ, ROLES
from zenit.gobernanza.sesiones import Sesiones
from zenit.gobernanza.usuarios import UsuariosMemoria, main as cli_usuarios
from zenit.repositorio import ErrorConsulta


class RepoNulo:
    def metricas_de(self, activo):
        return []

    def dimensiones(self, activo, metrica, desde, hasta):
        return {}

    def serie(self, *a, **k):
        raise ErrorConsulta("sin datos en esta prueba")

    def disponible(self):
        return True


@pytest.fixture
def catalogo(tmp_path):
    ruta = tmp_path / "catalogo.json"
    ruta.write_text(json.dumps({
        "zenit-nodo-do": "activo-do-nodo-01",
        "zenit-nodo-aws": "activo-aws-nodo-01",
        "zenit-plataforma": "activo-do-plataforma",
    }))
    return ruta


@pytest.fixture
def app(catalogo):
    return crear_app(RepoNulo(), catalogo, token_nodos="token-de-nodo-de-prueba", **opciones())


# --------------------------------------------------------------- sesion

def test_inicio_de_sesion_y_yo_con_las_formas_del_contrato(app):
    c = TestClient(app)
    r = c.post("/api/sesion", json={"usuario": "seguridad", "clave": CLAVE})
    assert r.status_code == 200
    cuerpo = r.json()
    assert set(cuerpo) == {"token", "usuario", "rol", "expira"}
    assert cuerpo["rol"] == "seguridad"
    assert datetime.fromisoformat(cuerpo["expira"]).tzinfo is not None
    assert "set-cookie" not in r.headers
    yo = c.get("/api/yo", headers={"Authorization": f"Bearer {cuerpo['token']}"}).json()
    assert set(yo) == {"usuario", "rol", "permisos"}
    assert "configuraciones:ver" in yo["permisos"] and "costos:ver" not in yo["permisos"]
    assert c.get("/api/yo").status_code == 401
    assert c.get("/api/yo", headers={"Authorization": "Bearer basura"}).status_code == 401


def test_clave_incorrecta_y_usuario_inexistente_responden_igual(app):
    c = TestClient(app)
    a = c.post("/api/sesion", json={"usuario": "operador", "clave": "otra-clave-cualquiera"})
    b = c.post("/api/sesion", json={"usuario": "nadie", "clave": "otra-clave-cualquiera"})
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


def test_bloqueo_tras_intentos_fallidos(app):
    c = TestClient(app)
    for _ in range(5):
        assert c.post("/api/sesion", json={"usuario": "operador", "clave": "mala-mala-mala"}).status_code == 401
    assert c.post("/api/sesion", json={"usuario": "operador", "clave": CLAVE}).status_code == 429


def test_ficha_alterada_o_vencida_no_vale():
    s = Sesiones(SECRETO)
    ficha, _ = s.emitir("operador")
    cuerpo, firma = ficha.split(".")
    assert s.leer(ficha).usuario == "operador"
    assert s.leer(cuerpo + "." + firma[:-2] + "AA") is None
    assert s.leer(ficha, ahora=datetime.now().timestamp() + 9 * 3600) is None
    assert Sesiones(b"o" * 32).leer(ficha) is None


def test_usuario_borrado_pierde_el_acceso_con_la_ficha_vigente(catalogo):
    usuarios = usuarios_de_prueba()
    app = crear_app(RepoNulo(), catalogo, usuarios=usuarios, secreto_sesion=SECRETO)
    c = con_sesion(app, "operador")
    assert c.get("/api/yo").status_code == 200
    usuarios.borrar("operador")
    assert c.get("/api/yo").status_code == 401


# ------------------------------------------------- matriz de permisos

def _rutas(app):
    for r in app.routes:
        if isinstance(r, APIRoute):
            for metodo in r.methods:
                yield metodo, r.path, getattr(r.endpoint, "__zenit_permiso__", None)


def _ruta_concreta(path):
    return (path.replace("{activo}", "activo-do-nodo-01").replace("{metrica}", "system_cpu_utilization"))


# La tabla de la seccion 2 del contrato v1.1, escrita aparte de permisos.py
# para que un cambio en la matriz que no siga al contrato rompa esta prueba.
CONTRATO = {
    "activos:ver": {"administrador", "operador", "seguridad", "finanzas"},
    "incidentes:ver": {"administrador", "operador", "seguridad"},
    "incidentes:gestionar": {"administrador", "operador"},
    "configuraciones:ver": {"administrador", "seguridad"},
    "costos:ver": {"administrador", "finanzas"},
    "laboratorio:ver": {"administrador"},
    "usuarios:gestionar": {"administrador"},
}


def test_matriz_sigue_la_tabla_del_contrato():
    for permiso, roles in CONTRATO.items():
        assert set(MATRIZ[permiso]) == roles, permiso


def test_toda_ruta_declara_permiso(app):
    for metodo, path, permiso in _rutas(app):
        assert permiso is not None, f"{metodo} {path} no declara permiso"


@pytest.mark.parametrize("rol", ROLES)
def test_matriz_completa_de_permisos(app, rol):
    """P-GOB-01: cada combinacion prohibida responde 403 y sin sesion 401."""
    anonimo = TestClient(app)
    c = con_sesion(app, rol)
    for metodo, path, permiso in _rutas(app):
        if not isinstance(permiso, str):
            continue  # publica o basta con sesion
        url = _ruta_concreta(path)
        assert anonimo.request(metodo, url).status_code == 401, f"{metodo} {path} sin sesion"
        r = c.request(metodo, url)
        if rol in MATRIZ[permiso]:
            assert r.status_code not in (401, 403), f"{rol} deberia poder {metodo} {path}"
        else:
            assert r.status_code == 403, f"{rol} no deberia poder {metodo} {path}: {r.status_code}"


def test_ruta_sin_permiso_declarado_se_niega_incluso_al_administrador(app):
    @app.get("/api/olvidada")
    def olvidada():
        return {"no": "deberia verse"}

    c = con_sesion(app, "administrador")
    assert c.get("/api/olvidada").status_code == 403
    assert TestClient(app).get("/api/olvidada").status_code == 401


def test_token_de_nodo_solo_sirve_para_instantaneas(app):
    c = TestClient(app, headers={"Authorization": "Bearer token-de-nodo-de-prueba"})
    assert c.get("/api/gobernanza/configuraciones").status_code == 403
    assert c.get("/api/gobernanza/costos").status_code == 403
    assert c.get("/api/yo").status_code == 403
    assert c.post("/api/gobernanza/configuraciones/instantaneas", json=_instantanea("zenit-nodo-do")).status_code == 201


# --------------------------------------------------------------- claves

def test_claves_derivadas_nunca_en_claro():
    """P-GOB-02."""
    h = claves.derivar("una-clave-bastante-larga")
    assert h.startswith("$2") and "una-clave" not in h
    assert claves.verificar("una-clave-bastante-larga", h)
    assert not claves.verificar("otra", h)
    with pytest.raises(claves.ClaveInvalida):
        claves.derivar("corta")
    with pytest.raises(claves.ClaveInvalida):
        claves.derivar("x" * 73)


def test_cli_crea_usuario_y_entra(catalogo, capsys):
    """P-GOB-06: el administrador crea un usuario, le asigna un rol y entra."""
    repo = UsuariosMemoria()
    assert cli_usuarios(["crear", "juanjo", "finanzas"], repo, pedir_clave=lambda: "clave-de-juanjo-123") == 0
    assert repo.obtener("juanjo").clave_hash != "clave-de-juanjo-123"
    assert cli_usuarios(["crear", "juanjo", "operador"], repo, pedir_clave=lambda: "x") == 1
    app = crear_app(RepoNulo(), catalogo, usuarios=repo, secreto_sesion=SECRETO)
    c = TestClient(app)
    r = c.post("/api/sesion", json={"usuario": "juanjo", "clave": "clave-de-juanjo-123"})
    assert r.status_code == 200
    c.headers["Authorization"] = f"Bearer {r.json()['token']}"
    assert c.get("/api/gobernanza/costos").status_code == 200
    assert c.get("/api/gobernanza/configuraciones").status_code == 403
    assert cli_usuarios(["rol", "juanjo", "seguridad"], repo) == 0
    assert c.get("/api/gobernanza/costos").status_code == 403
    assert c.get("/api/gobernanza/configuraciones").status_code == 200


# ------------------------------------------------------ configuraciones

AHORA = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)


def _instantanea(nodo, entorno="lab", version="0.116.1", intervalo=10, puertos=None, contenedores=None):
    return {
        "nodo": nodo,
        "entorno": entorno,
        "tomada_en": AHORA.isoformat(),
        "version_agente": version,
        "intervalo_muestreo_s": intervalo,
        "puertos": puertos if puertos is not None else [
            {"puerto": 22, "protocolo": "tcp", "direccion": "0.0.0.0", "proceso": "sshd"},
            {"puerto": 8888, "protocolo": "tcp", "direccion": "127.0.0.1", "proceso": "otelcol"},
        ],
        "contenedores": contenedores if contenedores is not None else [
            {"nombre": "agente", "imagen": "otel/opentelemetry-collector-contrib", "version": version, "usuario": "0:0"},
            {"nombre": "pedidos", "imagen": "ghcr.io/zenit/zenit-pedidos", "version": "0.2.0", "usuario": "1000"},
        ],
    }


POLITICA = configuraciones.Politica(
    puertos_esperados={"*": [22], "plataforma": [443, 4317]},
    root_permitido={"agente": "lee /proc de todos los procesos del anfitrion"},
)


def test_puerto_no_esperado_aparece_con_regla_activo_entorno_y_severidad(app):
    """P-GOB-04, con la forma de GET /api/gobernanza/configuraciones."""
    nodo = TestClient(app, headers={"Authorization": "Bearer token-de-nodo-de-prueba"})
    abierto = _instantanea("zenit-nodo-do", puertos=[
        {"puerto": 22, "protocolo": "tcp", "direccion": "0.0.0.0", "proceso": "sshd"},
        {"puerto": 5432, "protocolo": "tcp", "direccion": "0.0.0.0", "proceso": "postgres"},
    ])
    r = nodo.post("/api/gobernanza/configuraciones/instantaneas", json=abierto)
    assert r.json()["activo"] == "activo-do-nodo-01"
    nodo.post("/api/gobernanza/configuraciones/instantaneas", json=_instantanea("zenit-nodo-aws"))
    hallazgos = con_sesion(app, "seguridad").get("/api/gobernanza/configuraciones").json()
    assert isinstance(hallazgos, list)
    assert all(set(h) == {"regla", "severidad", "activos", "entornos", "detalle", "detectado"} for h in hallazgos)
    puerto = [h for h in hallazgos if h["regla"] == "puerto-expuesto-no-esperado"]
    assert len(puerto) == 1
    assert puerto[0]["severidad"] == "alta"
    assert puerto[0]["activos"] == ["activo-do-nodo-01"] and puerto[0]["entornos"] == ["lab"]
    assert "5432" in puerto[0]["detalle"]
    assert puerto[0]["detectado"] == AHORA.isoformat()


def test_loopback_y_esperados_no_son_hallazgo():
    i = configuraciones.leer_instantanea(_instantanea("n1"))
    assert [h for h in configuraciones.evaluar([i], POLITICA) if h.regla == "puerto-expuesto-no-esperado"] == []
    plataforma = configuraciones.leer_instantanea(_instantanea("p", entorno="plataforma", puertos=[
        {"puerto": 443, "protocolo": "tcp", "direccion": "::", "proceso": "nginx"}]))
    assert configuraciones.evaluar([plataforma], POLITICA) == []


def test_version_de_agente_e_intervalo_distintos_entre_entornos():
    ins = [
        configuraciones.leer_instantanea(_instantanea("a", "aws")),
        configuraciones.leer_instantanea(_instantanea("b", "azure")),
        configuraciones.leer_instantanea(_instantanea("c", "onprem", version="0.110.0", intervalo=30)),
    ]
    reglas = {h.regla: h for h in configuraciones.evaluar(ins, POLITICA)}
    assert reglas["version-agente-distinta"].entornos == ["onprem"]
    assert "0.110.0" in reglas["version-agente-distinta"].detalle
    assert reglas["intervalo-muestreo-distinto"].entornos == ["onprem"]
    assert "imagen-version-distinta" in reglas
    assert reglas["imagen-version-distinta"].entornos == ["aws", "azure", "onprem"]


def test_contenedor_como_root_salvo_excepcion_declarada():
    i = configuraciones.leer_instantanea(_instantanea("a", contenedores=[
        {"nombre": "agente", "imagen": "otel", "version": "1", "usuario": "0:0"},
        {"nombre": "inyector", "imagen": "ghcr.io/zenit/zenit-inyector", "version": "1", "usuario": ""},
        {"nombre": "web", "imagen": "nginx", "version": "1", "usuario": "nginx"},
    ]))
    root = [h for h in configuraciones.evaluar([i], POLITICA) if h.regla == "contenedor-como-root"]
    assert len(root) == 1 and "inyector" in root[0].detalle
    assert root[0].severidad == "alta"


@pytest.mark.parametrize("cambio", [
    {"tomada_en": "ayer"},
    {"intervalo_muestreo_s": 0},
    {"intervalo_muestreo_s": "10"},
    {"puertos": [{"puerto": 70000, "protocolo": "tcp", "direccion": "0.0.0.0"}]},
    {"nodo": ""},
    {"contenedores": "nada"},
])
def test_instantanea_mal_formada_responde_422(app, cambio):
    nodo = TestClient(app, headers={"Authorization": "Bearer token-de-nodo-de-prueba"})
    r = nodo.post("/api/gobernanza/configuraciones/instantaneas", json={**_instantanea("zenit-nodo-do"), **cambio})
    assert r.status_code == 422


def test_cuenta_la_ultima_instantanea_de_cada_nodo():
    repo = configuraciones.InstantaneasMemoria()
    vieja = _instantanea("a", version="0.100.0")
    vieja["tomada_en"] = (AHORA - timedelta(hours=1)).isoformat()
    repo.guardar(configuraciones.leer_instantanea(_instantanea("a")))
    repo.guardar(configuraciones.leer_instantanea(vieja))
    assert [i.version_agente for i in repo.ultimas()] == ["0.116.1"]


# --------------------------------------------------------------- costos

FACTURA_DO = """product,group_description,description,hours,start,end,USD,project_name,category
Droplets,,zenit-nodo-do (s-1vcpu-2gb),744,2026-09-01 00:00:00 +0000,2026-10-01 00:00:00 +0000,$12.00,zenit,iaas
Droplets,,zenit-plataforma (s-2vcpu-4gb),744,2026-09-01 00:00:00 +0000,2026-10-01 00:00:00 +0000,$24.00,zenit,iaas
Backups,,zenit-plataforma (s-2vcpu-4gb),,2026-09-01 00:00:00 +0000,2026-10-01 00:00:00 +0000,$4.80,zenit,iaas
Volumes,,volumen-sin-nombre (10 GiB),744,2026-09-01 00:00:00 +0000,2026-10-01 00:00:00 +0000,$1.00,zenit,iaas
Bandwidth,,Bandwidth overage,,2026-09-01 00:00:00 +0000,2026-10-01 00:00:00 +0000,$0.37,zenit,iaas
"""


def test_factura_de_digitalocean_se_atribuye_por_activo(app):
    """P-GOB-05: el costo queda atribuido a cada activo y lo no asignable aparte."""
    c = con_sesion(app, "finanzas")
    r = c.post("/api/gobernanza/costos/facturas?proveedor=digitalocean", content=FACTURA_DO.encode(),
               headers={"Content-Type": "text/csv"})
    assert r.status_code == 201
    resumen = r.json()
    assert (resumen["lineas"], resumen["asignadas"], resumen["sin_asignar"], resumen["total"]) == (5, 3, 2, 42.17)
    assert {x["motivo"] for x in resumen["lineas_sin_asignar"]} == {"ningun recurso del catalogo aparece en la linea"}
    reporte = c.get("/api/gobernanza/costos").json()
    assert set(reporte) == {"moneda", "periodo", "por_activo", "sin_asignar"}
    assert reporte["periodo"] == {"desde": "2026-09-01", "hasta": "2026-10-01"}
    assert reporte["por_activo"] == [
        {"activo": "activo-do-plataforma", "proveedor": "digitalocean", "costo": 28.8, "fuente": "factura exportada 2026-09"},
        {"activo": "activo-do-nodo-01", "proveedor": "digitalocean", "costo": 12.0, "fuente": "factura exportada 2026-09"},
    ]
    assert reporte["sin_asignar"] == 1.37


def test_importar_dos_veces_no_duplica(app):
    c = con_sesion(app, "finanzas")
    for nueva in (True, False):
        r = c.post("/api/gobernanza/costos/facturas?proveedor=digitalocean", content=FACTURA_DO.encode())
        assert r.json()["nueva"] is nueva
    assert sum(a["costo"] for a in c.get("/api/gobernanza/costos").json()["por_activo"]) == 40.8


def test_filtro_por_periodo(app):
    c = con_sesion(app, "finanzas")
    c.post("/api/gobernanza/costos/facturas?proveedor=digitalocean", content=FACTURA_DO.encode())
    sept = c.get("/api/gobernanza/costos?desde=2026-09-01&hasta=2026-09-30").json()
    assert len(sept["por_activo"]) == 2 and sept["sin_asignar"] == 1.37
    agosto = c.get("/api/gobernanza/costos?desde=2026-08-01&hasta=2026-08-31").json()
    assert agosto["por_activo"] == [] and agosto["sin_asignar"] == 0.0
    assert c.get("/api/gobernanza/costos?desde=septiembre").status_code == 422
    assert c.get("/api/gobernanza/costos?desde=2026-10-01&hasta=2026-09-01").status_code == 422


def test_sin_facturas_el_periodo_es_el_mes_en_curso():
    desde, hasta = costos.periodo_por_defecto([], hoy=datetime(2026, 2, 10).date())
    assert (desde.isoformat(), hasta.isoformat()) == ("2026-02-01", "2026-02-28")


@pytest.mark.parametrize("contenido,proveedor", [
    (b"", "digitalocean"),
    (b"a,b,c\n1,2,3\n", "digitalocean"),
    (b"product,description,USD\nDroplets,x,doce\n", "digitalocean"),
    (FACTURA_DO.encode(), "aws"),
    ("product,description,USD\n".encode("utf-16"), "digitalocean"),
])
def test_factura_invalida_responde_422(app, contenido, proveedor):
    c = con_sesion(app, "finanzas")
    assert c.post(f"/api/gobernanza/costos/facturas?proveedor={proveedor}", content=contenido).status_code == 422


def test_linea_que_coincide_con_dos_activos_no_se_asigna():
    activo, motivo = costos.atribuir(["zenit-nodo-do y zenit-nodo-aws"], {"zenit-nodo-do": "a", "zenit-nodo-aws": "b"})
    assert activo is None and "varios" in motivo


def test_nombre_parecido_no_cuenta_como_el_activo():
    activo, _ = costos.atribuir(["zenit-nodo-do-viejo (s-1vcpu)"], {"zenit-nodo-do": "a"})
    assert activo is None
