"""Catalogo canonico y descubrimiento (H-009a, b, c, e).

Los proveedores se prueban contra respuestas grabadas con la forma de sus APIs
(httpx.MockTransport y el Stubber de botocore): ninguna prueba sale a internet.
Lo que toca PostgreSQL necesita ZENIT_PG_PRUEBAS, igual que test_verdad.
"""
import json
import os

import httpx
import pytest

from conftest import preparar_pg
from zenit import catalogo
from zenit.catalogo import Recurso
from zenit.descubrimiento import proveedores_desde_entorno
from zenit.descubrimiento.__main__ import pasada
from zenit.descubrimiento.aws import AWS
from zenit.descubrimiento.digitalocean import DigitalOcean
from zenit.descubrimiento.proxmox import Proxmox

PG = os.environ.get("ZENIT_PG_PRUEBAS")
con_pg = pytest.mark.skipif(not PG, reason="sin ZENIT_PG_PRUEBAS")


@pytest.fixture
def pg():
    conexion = preparar_pg(PG)
    yield conexion
    conexion.close()


def _droplet(i, nombre, region="nyc3"):
    return {"id": i, "name": nombre, "status": "active", "size_slug": "s-1vcpu-2gb",
            "region": {"slug": region}, "tags": ["zenit", "zenit-nodo"]}


class ProveedorFijo:
    def __init__(self, nombre, recursos=None, error=None):
        self.nombre, self.recursos, self.error = nombre, recursos or [], error

    def listar(self):
        if self.error:
            raise self.error
        return list(self.recursos)


# ------------------------------------------------------------------ proveedores
def test_digitalocean_pagina_filtra_por_etiqueta_y_manda_el_token():
    vistas = []

    def responder(peticion):
        vistas.append(peticion)
        assert peticion.headers["Authorization"] == "Bearer dop_v1_lectura"
        if peticion.url.params.get("page") == "2":
            return httpx.Response(200, json={"droplets": [_droplet(2, "zenit-nodo-do-b")], "links": {}})
        assert peticion.url.params["tag_name"] == "zenit-nodo"
        return httpx.Response(200, json={
            "droplets": [_droplet(1, "zenit-nodo-do")],
            "links": {"pages": {"next": "https://api.digitalocean.com/v2/droplets?page=2&per_page=200&tag_name=zenit-nodo"}},
        })

    do = DigitalOcean("dop_v1_lectura", http=httpx.Client(base_url="https://api.digitalocean.com",
                                                          transport=httpx.MockTransport(responder)))
    recursos = do.listar()
    assert [(r.clave, r.nombre, r.region) for r in recursos] == [
        ("digitalocean:droplet/1", "zenit-nodo-do", "nyc3"),
        ("digitalocean:droplet/2", "zenit-nodo-do-b", "nyc3"),
    ]
    assert len(vistas) == 2


def test_digitalocean_con_error_lanza_y_no_devuelve_lista_parcial():
    do = DigitalOcean("x", http=httpx.Client(base_url="https://api.digitalocean.com",
                                             transport=httpx.MockTransport(lambda _: httpx.Response(401))))
    with pytest.raises(httpx.HTTPStatusError):
        do.listar()


def test_proxmox_usa_el_token_de_api_y_filtra_etiqueta_y_plantillas():
    def responder(peticion):
        assert peticion.headers["Authorization"] == "PVEAPIToken=zenit@pve!descubrimiento=abc-123"
        assert peticion.url.path == "/api2/json/cluster/resources"
        return httpx.Response(200, json={"data": [
            {"id": "qemu/105", "vmid": 105, "name": "zenit-nodo-onprem", "node": "pve", "status": "running",
             "type": "qemu", "tags": "zenit-nodo;lab"},
            {"id": "qemu/106", "vmid": 106, "name": "otra-vm", "node": "pve", "status": "running", "type": "qemu"},
            {"id": "qemu/900", "vmid": 900, "name": "plantilla", "node": "pve", "template": 1, "tags": "zenit-nodo"},
        ]})

    px = Proxmox("https://pve:8006", "zenit@pve!descubrimiento=abc-123",
                 http=httpx.Client(base_url="https://pve:8006", transport=httpx.MockTransport(responder)))
    recursos = px.listar()
    assert [(r.clave, r.nombre, r.region, r.atributos["nodo"]) for r in recursos] == [
        ("proxmox:qemu/105", "zenit-nodo-onprem", "local", "pve")]


def test_proxmox_rechaza_un_token_mal_formado():
    with pytest.raises(ValueError):
        Proxmox("https://pve:8006", "solo-el-secreto")


def test_aws_pide_instancias_con_la_etiqueta_y_excluye_terminadas():
    import boto3
    from botocore.stub import Stubber

    ec2 = boto3.client("ec2", region_name="us-east-1", aws_access_key_id="x", aws_secret_access_key="y")
    with Stubber(ec2) as stub:
        stub.add_response(
            "describe_instances",
            {"Reservations": [{"Instances": [{
                "InstanceId": "i-0abc", "InstanceType": "t3.small", "State": {"Name": "stopped", "Code": 80},
                "Placement": {"AvailabilityZone": "us-east-1a"},
                "Tags": [{"Key": "Name", "Value": "zenit-nodo-aws"}, {"Key": "zenit:rol", "Value": "nodo"}],
            }]}]},
            {"Filters": [{"Name": "tag:zenit:rol", "Values": ["nodo"]},
                         {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]}]},
        )
        recursos = AWS("us-east-1", cliente=ec2).listar()
    assert [(r.clave, r.nombre, r.atributos["estado"]) for r in recursos] == [
        ("aws:instancia/i-0abc", "zenit-nodo-aws", "stopped")]


def test_solo_se_activan_los_proveedores_con_credencial():
    assert proveedores_desde_entorno({}) == []
    nombres = [p.nombre for p in proveedores_desde_entorno({
        "DO_TOKEN": "t", "PROXMOX_URL": "https://pve:8006", "PROXMOX_TOKEN": "u@pve!t=s", "DESCUBRIR_AWS": "1"})]
    assert nombres == ["digitalocean", "proxmox", "aws"]


# ------------------------------------------------------------------ catalogo
@con_pg
def test_identificador_estable_entre_ejecuciones(pg):
    rec = Recurso("digitalocean", "droplet/1", "zenit-nodo-x", "nyc3", "lab")
    r1 = catalogo.reconciliar(pg, "digitalocean", [rec])
    r2 = catalogo.reconciliar(pg, "digitalocean", [rec])
    assert (r1.nuevos, r2.nuevos) == (1, 0)
    mapa = catalogo.mapa_claves(pg)
    assert mapa == {"digitalocean:droplet/1": "activo-do-zenit-nodo-x", "zenit-nodo-x": "activo-do-zenit-nodo-x"}


@con_pg
def test_un_nodo_admitido_a_mano_conserva_su_id_al_descubrirse(pg, tmp_path):
    ruta = tmp_path / "catalogo.json"
    ruta.write_text(json.dumps({"zenit-nodo-do": "activo-do-nodo-01"}))
    assert catalogo.importar_archivo(pg, ruta) == 1
    assert catalogo.importar_archivo(pg, ruta) == 0          # idempotente
    catalogo.reconciliar(pg, "digitalocean", [Recurso("digitalocean", "droplet/7", "zenit-nodo-do", "nyc3", "lab")])
    (a,) = catalogo.listar_activos(pg)
    assert a["id"] == "activo-do-nodo-01"
    assert a["proveedor"] == "digitalocean" and a["region"] == "nyc3" and a["origen"] == "manual"
    assert sorted(a["claves"]) == ["digitalocean:droplet/7", "zenit-nodo-do"]


@con_pg
def test_se_retira_tras_tres_ejecuciones_sin_verlo_y_vuelve_si_reaparece(pg):
    rec = Recurso("proxmox", "qemu/105", "zenit-nodo-onprem", "local", "lab")
    catalogo.reconciliar(pg, "proxmox", [rec])
    for n in range(2):
        assert catalogo.reconciliar(pg, "proxmox", []).retirados == 0
    assert catalogo.listar_activos(pg)[0]["estado"] == "activo"
    assert catalogo.reconciliar(pg, "proxmox", []).retirados == 1
    (a,) = catalogo.listar_activos(pg)
    assert a["estado"] == "retirado" and a["retirado_en"] is not None
    # retirado no es borrado: la clave sigue resolviendo
    assert catalogo.mapa_claves(pg)["zenit-nodo-onprem"] == a["id"]
    assert catalogo.reconciliar(pg, "proxmox", [rec]).reactivados == 1
    assert catalogo.listar_activos(pg)[0]["estado"] == "activo"


@con_pg
def test_un_error_del_proveedor_no_cuenta_como_ausencia(pg):
    catalogo.reconciliar(pg, "digitalocean", [Recurso("digitalocean", "droplet/1", "n1")])
    caido = ProveedorFijo("digitalocean", error=httpx.ConnectError("sin red"))
    for _ in range(5):
        pasada(PG, [caido])
    (a,) = catalogo.listar_activos(pg)
    assert a["estado"] == "activo" and a["ausencias"] == 0
    ult = catalogo.ejecuciones(pg)["ultimas"][0]
    assert ult["estado"] == "error" and "sin red" in ult["error"]


@con_pg
def test_retirar_no_toca_a_otros_proveedores_ni_a_los_manuales(pg):
    catalogo.agregar_manual(pg, "zenit-nodo-viejo", "activo-viejo-01")
    catalogo.reconciliar(pg, "aws", [Recurso("aws", "instancia/i-1", "zenit-nodo-aws")])
    for _ in range(4):
        catalogo.reconciliar(pg, "digitalocean", [])
    assert {a["id"]: a["estado"] for a in catalogo.listar_activos(pg)} == {
        "activo-aws-zenit-nodo-aws": "activo", "activo-viejo-01": "activo"}


@con_pg
def test_dos_recursos_con_el_mismo_nombre_no_se_roban_la_clave(pg):
    catalogo.reconciliar(pg, "digitalocean", [Recurso("digitalocean", "droplet/1", "zenit-nodo")])
    r = catalogo.reconciliar(pg, "aws", [Recurso("aws", "instancia/i-1", "zenit-nodo")])
    # el de AWS es otro activo; la clave host.name se queda con el primero y
    # el conflicto queda dicho
    assert r.nuevos == 1 and r.conflictos
    mapa = catalogo.mapa_claves(pg)
    assert mapa["zenit-nodo"] == "activo-do-zenit-nodo"
    assert mapa["aws:instancia/i-1"] == "activo-aws-zenit-nodo"
    # y una segunda pasada de DigitalOcean no se lo lleva
    catalogo.reconciliar(pg, "digitalocean", [Recurso("digitalocean", "droplet/1", "zenit-nodo")])
    assert {a["id"]: a["proveedor"] for a in catalogo.listar_activos(pg)} == {
        "activo-aws-zenit-nodo": "aws", "activo-do-zenit-nodo": "digitalocean"}


@con_pg
def test_un_droplet_recreado_con_el_mismo_nombre_conserva_el_activo(pg):
    catalogo.reconciliar(pg, "digitalocean", [Recurso("digitalocean", "droplet/1", "zenit-nodo-do")])
    r = catalogo.reconciliar(pg, "digitalocean", [Recurso("digitalocean", "droplet/2", "zenit-nodo-do")])
    assert r.nuevos == 0
    assert catalogo.mapa_claves(pg)["digitalocean:droplet/2"] == "activo-do-zenit-nodo-do"


@con_pg
def test_pasada_registra_cada_ejecucion_con_su_resultado(pg):
    ok = ProveedorFijo("digitalocean", [Recurso("digitalocean", "droplet/1", "n1")])
    mal = ProveedorFijo("proxmox", error=RuntimeError("403 sin PVEAuditor"))
    resultado = dict(pasada(PG, [ok, mal]))
    assert resultado["digitalocean"].nuevos == 1 and "403" in resultado["proxmox"]
    ultimas = {e["proveedor"]: e for e in catalogo.ejecuciones(pg)["ultimas"]}
    assert ultimas["digitalocean"]["estado"] == "ok" and ultimas["digitalocean"]["vistos"] == 1
    assert ultimas["proxmox"]["estado"] == "error"


@con_pg
def test_migrar_pg_se_puede_repetir(tmp_path, monkeypatch, capsys):
    from conftest import ESQUEMAS_PG
    from zenit import admin

    preparar_pg(PG).close()
    monkeypatch.setenv("PG_URL", PG)
    ruta = tmp_path / "catalogo.json"
    ruta.write_text(json.dumps({"zenit-nodo-do": "activo-do-nodo-01"}))
    carpeta = ESQUEMAS_PG[0].parent
    assert admin.migrar_pg(carpeta, str(ruta)) == 0
    assert admin.migrar_pg(carpeta, str(ruta)) == 0
    salida = capsys.readouterr().out
    assert "02-catalogo.sql" in salida and "nada nuevo" in salida


# ------------------------------------------------------------------ ingesta
@con_pg
def test_catalogo_pg_de_la_ingesta_ve_lo_nuevo_y_aguanta_una_caida(pg):
    class Respaldo:
        def actual(self):
            return {"zenit-nodo-aws": "activo-aws-nodo-01"}

    c = catalogo.CatalogoPG(PG, respaldo=Respaldo(), arrancar=False)
    assert c.actual() == {} and c.estado()["fuente"] == "postgres"
    catalogo.agregar_manual(pg, "zenit-nodo-do", "activo-do-nodo-01", "digitalocean")
    c.recargar()
    assert c.actual() == {"zenit-nodo-do": "activo-do-nodo-01"}
    c._url = "postgresql://nadie@127.0.0.1:1/nada"           # PostgreSQL deja de responder
    assert c.recargar() is False
    assert c.actual() == {"zenit-nodo-do": "activo-do-nodo-01"}   # se conserva el ultimo bueno
    assert c.estado()["errores"] == 1


def test_catalogo_pg_que_nunca_respondio_usa_el_archivo():
    class Respaldo:
        def actual(self):
            return {"zenit-nodo-aws": "activo-aws-nodo-01"}

    c = catalogo.CatalogoPG("postgresql://nadie@127.0.0.1:1/nada", respaldo=Respaldo(), arrancar=False)
    assert c.actual() == {"zenit-nodo-aws": "activo-aws-nodo-01"}
    assert c.estado()["fuente"] == "archivo"


# ------------------------------------------------------------------ API
@con_pg
def test_api_lista_activos_y_la_ultima_ejecucion(pg, tmp_path):
    from fastapi.testclient import TestClient

    from zenit.api.main import crear_app

    catalogo.reconciliar(pg, "digitalocean", [Recurso("digitalocean", "droplet/1", "zenit-nodo-do", "nyc3", "lab")])
    pasada(PG, [ProveedorFijo("digitalocean", [Recurso("digitalocean", "droplet/1", "zenit-nodo-do", "nyc3", "lab")])])

    class RepoQuieto:
        def disponible(self):
            return True

    cliente = TestClient(crear_app(RepoQuieto(), tmp_path / "no-existe.json", pg_url=PG))
    (a,) = cliente.get("/api/activos").json()
    assert a["id"] == "activo-do-zenit-nodo-do" and a["proveedor"] == "digitalocean"
    assert a["claves"] == ["zenit-nodo-do"] and a["ids_nativos"] == ["digitalocean:droplet/1"]
    assert cliente.get("/api/activos/activo-do-zenit-nodo-do").json()["nombre"] == "zenit-nodo-do"
    assert cliente.get("/api/activos/no-existe").status_code == 404
    d = cliente.get("/api/catalogo/descubrimientos").json()
    assert d["ultimas"][0]["proveedor"] == "digitalocean" and d["ultimas"][0]["estado"] == "ok"
