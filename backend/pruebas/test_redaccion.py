"""H-041: solo evidencia redactada sale hacia el modelo."""
import json

from zenit.diagnostico import hipotesis
from zenit.diagnostico.redaccion import redactar, restaurar

CONOCIDOS = {
    "activo-aws-nodo-01": "ACTIVO",
    "zenit-nodo-aws": "ANFITRION",
    "zenit-nodo-do": "ANFITRION",
    "aws:instancia/i-0abc123def4567890": "ID",
    "azure:vm/6f1c2b3a-1111-2222-3333-444455556666": "ID",
    "digitalocean:droplet/412345678": "ID",
}


def _todo(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def test_reemplaza_anfitriones_activos_ips_e_ids():
    ev = {
        "incidente": {"activo": "activo-aws-nodo-01", "metrica": "system_cpu_utilization",
                      "dimensiones": {"state": "user"}, "inicio": "2026-10-20T12:00:00+00:00",
                      "valor_observado": 0.93, "puntaje": 6.8},
        "registros_error": [
            {"cuerpo": "zenit-nodo-aws: conexion a 10.0.3.17:5432 rechazada", "servicio": "pedidos"},
            {"cuerpo": "peer fe80::1ff:fe23:4567:890a no responde; reintento en http://10.0.3.17:8000/salud"},
            {"cuerpo": "instancia aws:instancia/i-0abc123def4567890 y azure:vm/6f1c2b3a-1111-2222-3333-444455556666"},
            {"cuerpo": "droplet digitalocean:droplet/412345678 en zenit-nodo-do"},
        ],
    }
    enviado, mapa = redactar(ev, CONOCIDOS)
    texto = _todo(enviado)
    for secreto in ("activo-aws-nodo-01", "zenit-nodo-aws", "zenit-nodo-do", "10.0.3.17", "fe80::1ff:fe23:4567:890a",
                    "i-0abc123def4567890", "6f1c2b3a", "412345678"):
        assert secreto not in texto, secreto
    assert enviado["incidente"]["activo"] == "[ACTIVO_1]"
    assert enviado["registros_error"][0]["cuerpo"] == "[ANFITRION_1]: conexion a [IP_1]:5432 rechazada"
    assert "[IP_2]" in enviado["registros_error"][1]["cuerpo"]
    assert set(mapa.values()) >= {"activo-aws-nodo-01", "zenit-nodo-aws", "10.0.3.17", "fe80::1ff:fe23:4567:890a"}


def test_el_mismo_valor_recibe_siempre_el_mismo_marcador():
    ev = ["10.0.3.17 cayo", {"a": "otra vez 10.0.3.17"}, "zenit-nodo-aws y ZENIT-NODO-AWS", "10.0.3.18"]
    enviado, mapa = redactar(ev, CONOCIDOS)
    assert enviado[0] == "[IP_1] cayo" and enviado[1]["a"] == "otra vez [IP_1]"
    assert enviado[2] == "[ANFITRION_1] y [ANFITRION_1]"
    assert enviado[3] == "[IP_2]"                     # otro valor, otro marcador
    assert len(set(mapa)) == len(mapa) == 3


def test_numeros_fechas_metricas_y_claves_no_se_tocan():
    ev = {"metrica": "system_disk_io", "dimensiones": {"device": "sda1", "direction": "write"},
          "momento": "2026-10-20T12:30:45.123+00:00", "hora": "12:30:45", "valor": 945140.622, "n": 3,
          "version": "0.116.1", "puntos": [[1760961600000, 0.25]], "texto": "duro 12:30 min con 3 reintentos",
          "zenit-nodo-aws": "la clave de un diccionario no se redacta"}
    enviado, mapa = redactar(ev, CONOCIDOS)
    assert enviado == ev and mapa == {}


def test_el_valor_conocido_mas_largo_gana_y_solo_como_palabra_completa():
    conocidos = {"zenit-nodo-do": "ANFITRION", "zenit-nodo-do-viejo": "ANFITRION", "nodo": "ANFITRION"}
    enviado, mapa = redactar(["zenit-nodo-do-viejo y zenit-nodo-do", "nodos y nodo"], conocidos)
    assert enviado[0] == "[ANFITRION_1] y [ANFITRION_2]"
    assert mapa["[ANFITRION_1]"] == "zenit-nodo-do-viejo" and mapa["[ANFITRION_2]"] == "zenit-nodo-do"
    assert enviado[1] == "nodos y [ANFITRION_3]"


def test_reconoce_ids_y_anfitriones_que_no_estan_en_el_catalogo():
    enviado, mapa = redactar(
        "ip-10-0-3-17.ec2.internal arranco i-0123456789abcdef0 en "
        "/subscriptions/6f1c2b3a-1111-2222-3333-444455556666/resourceGroups/zenit/providers/x y proxmox:qemu/105", {})
    assert enviado == "[ANFITRION_1] arranco [ID_1] en [ID_2] y [ID_3]"
    assert mapa["[ID_3]"] == "proxmox:qemu/105"


def test_ip_invalidas_no_son_ip():
    enviado, mapa = redactar(["300.1.2.3", "a:b", "::", "1.2.3"], {})
    assert enviado[:2] == ["300.1.2.3", "a:b"] and enviado[3] == "1.2.3"
    assert "1.2.3" not in mapa.values()


def test_no_modifica_la_evidencia_original():
    ev = {"cuerpo": ["zenit-nodo-aws"]}
    redactar(ev, CONOCIDOS)
    assert ev == {"cuerpo": ["zenit-nodo-aws"]}


def test_restaurar_devuelve_los_nombres_reales():
    enviado, mapa = redactar("zenit-nodo-aws llama a 10.0.3.17", CONOCIDOS)
    hipotesis_modelo = f"La CPU de [ANFITRION_1] subio porque {enviado.split()[-1]} respondio lento; [IP_9] no existe."
    assert restaurar(hipotesis_modelo, mapa) == ("La CPU de zenit-nodo-aws subio porque 10.0.3.17 respondio lento; "
                                                 "[IP_9] no existe.")
    assert restaurar("sin marcadores", mapa) == "sin marcadores"
    assert restaurar("[IP_1]", {}) == "[IP_1]"


def test_ida_y_vuelta():
    original = "zenit-nodo-do (digitalocean:droplet/412345678) en 10.1.2.3 y 2001:db8::7"
    enviado, mapa = redactar(original, CONOCIDOS)
    assert restaurar(enviado, mapa) == original


def test_hipotesis_carga_esta_redaccion():
    redactar_, restaurar_ = hipotesis.cargar_redactor()
    assert redactar_ is redactar and restaurar_ is restaurar


def test_lo_que_compacta_h040_sale_sin_identificadores():
    """Con la forma real que arma H-040: compactar y redactar como en diagnosticar()."""
    from datetime import datetime, timezone

    anomalia = {"activo": "activo-aws-nodo-01", "metrica": "system_cpu_utilization", "dimensiones": {"state": "user"},
                "severidad": "alta", "puntaje": 6.8, "inicio": datetime(2026, 10, 20, 12, tzinfo=timezone.utc),
                "fin": None, "version_modelo": "mad-v1", "evidencia": {"valor_observado": 0.9, "valor_esperado": 0.1}}
    ev = {"ventana": {"desde": "2026-10-20T11:00:00+00:00", "hasta": "2026-10-20T12:30:00+00:00"},
          "metricas": [{"metrica": "system_cpu_utilization", "dimensiones": {"state": "user"}, "papel": "afectada",
                        "puntos": [["2026-10-20T12:00:00+00:00", 0.9]]}],
          "registros_error": [{"momento": "2026-10-20T12:01:00+00:00", "service_name": "pedidos", "severidad": "ERROR",
                               "cuerpo": "zenit-nodo-aws no alcanza 172.18.0.4"}],
          "trazas_error": [], "correlacion": [], "conteos": {}, "faltantes": []}
    enviado, _ = redactar(hipotesis.compactar(anomalia, ev), CONOCIDOS)
    texto = _todo(enviado)
    assert "activo-aws-nodo-01" not in texto and "zenit-nodo-aws" not in texto and "172.18.0.4" not in texto
    assert "system_cpu_utilization" in texto and "2026-10-20T12:00:00+00:00" in texto


# ------------------------------------------------------------------ correos y secretos
def test_correos_se_redactan_y_se_restauran():
    enviado, mapa = redactar(["fallo al notificar a ops@zenit.co y a Daniel.Perez+alertas@empresa.com.co",
                              "otra vez ops@zenit.co"], {})
    assert enviado[0] == "fallo al notificar a [CORREO_1] y a [CORREO_2]"
    assert enviado[1] == "otra vez [CORREO_1]"
    assert restaurar("avisar a [CORREO_1]", mapa) == "avisar a ops@zenit.co"


JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJkYW5pZWwifQ.dGhpcy1pcy1hLXNpZ25hdHVyZQ"


def test_secretos_se_redactan_y_nunca_se_restauran():
    textos = [
        f"Authorization: Bearer {JWT}",
        f"token suelto {JWT} en el registro",
        "ANTHROPIC_API_KEY=sk-ant-api03-AbCdEf123456_xyz",
        "conectando con password=Sup3rS3creta! y api_key: 9f8e7d6c5b4a",
        '{"clave": "zenit2026", "token":"abc.def"}',
        "client_secret = 'n0-v4-a-s4lir'",
    ]
    enviado, mapa = redactar(textos, {})
    texto = json.dumps(enviado)
    for secreto in (JWT, "sk-ant-api03-AbCdEf123456_xyz", "Sup3rS3creta!", "9f8e7d6c5b4a", "zenit2026", "abc.def",
                    "n0-v4-a-s4lir"):
        assert secreto not in texto, secreto
    assert enviado[0] == "Authorization: Bearer [SECRETO_1]"
    assert enviado[1] == "token suelto [SECRETO_1] en el registro"          # el mismo JWT, el mismo marcador
    assert enviado[2] == "ANTHROPIC_API_KEY=[SECRETO_2]"
    assert enviado[3] == "conectando con password=[SECRETO_3] y api_key: [SECRETO_4]"
    assert not any(m.startswith("[SECRETO") for m in mapa)                # nunca entran al mapa
    assert restaurar("la clave [SECRETO_3] quedo en el registro", mapa) == "la clave [SECRETO_3] quedo en el registro"


def test_el_valor_de_un_campo_secreto_se_redacta_entero():
    enviado, mapa = redactar({"usuario": "daniel", "password": "una clave con espacios", "tokens_entrada": 1800,
                              "Token": "abc"}, {})
    assert enviado["password"] == "[SECRETO_1]" and enviado["Token"] == "[SECRETO_2]"
    assert enviado["usuario"] == "daniel" and enviado["tokens_entrada"] == 1800
    assert mapa == {}


def test_lo_que_parece_campo_secreto_pero_no_lo_es_no_se_toca():
    ev = ["tokens_entrada: 1800", "la clave del catalogo es el id del activo", "Bearer", "token_count=3"]
    enviado, _ = redactar(ev, {})
    assert enviado[:3] == ev[:3]
