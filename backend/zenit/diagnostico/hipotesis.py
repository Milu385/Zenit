"""Diagnostico como hipotesis con evidencia citada (H-040).

Para un incidente:

1. recupera la evidencia de las tres senales (H-039);
2. la compacta y la redacta: los nombres de anfitrion, los ids de activo,
   las IP y los ids nativos se reemplazan por marcadores antes de salir de la
   plataforma (H-041, `zenit.diagnostico.redaccion`). **Sin redaccion no se
   llama al modelo**: si el modulo no esta, el diagnostico se niega;
3. le pide al modelo una hipotesis en JSON, con citas de metrica e intervalo
   y acciones sugeridas que nadie ejecuta;
4. comprueba cada cita contra la evidencia: la metrica tiene que estar en la
   evidencia y el intervalo dentro de su ventana. Las que no cumplen se
   descartan; si no queda ninguna, no se guarda el diagnostico;
5. devuelve los marcadores a los nombres reales y guarda el diagnostico con
   la evidencia recuperada y lo que se envio (H-039 y P-DIA-02).

Si el proveedor falla, el incidente y su evidencia se siguen viendo: no se
escribe nada (P-DIA-05).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from zenit.diagnostico.evidencia import recuperar
from zenit.diagnostico.modelo import ErrorModelo
from zenit.diagnostico.persistencia import guardar_diagnostico

log = logging.getLogger("zenit.diagnostico")

# Partes de la evidencia con texto crudo de registros, que no se guardan con el diagnostico
TEXTO_CRUDO = ("registros_error", "correlacion")

SISTEMA = """Eres el modulo de diagnostico de Zenit, una plataforma de observabilidad de infraestructura.
Recibes la evidencia de un incidente que abrio un detector estadistico: la metrica que se desvio, \
metricas relacionadas, registros de error y trazas con error del mismo activo, con su ventana de tiempo. \
Algunos identificadores vienen reemplazados por marcadores como [ANFITRION_1] o [IP_1]: usalos tal cual.

Reglas:
1. Usa solo la evidencia entregada. No inventes metricas, valores ni horas.
2. Escribe una hipotesis, no una conclusion: "probablemente", "es posible que", "la evidencia sugiere". \
No afirmes una causa que la evidencia no demuestra. Si la evidencia no alcanza, dilo.
3. Cita la evidencia que sostiene la hipotesis. Cada cita lleva la metrica exactamente como aparece en la \
evidencia y un intervalo dentro de la ventana, en ISO 8601 con zona, y una observacion corta con los valores.
4. Sugiere de 1 a 3 acciones para que el equipo las haga a mano. Zenit no ejecuta ninguna accion.
5. Responde en espanol y solo con un objeto JSON, sin texto antes ni despues:
{"hipotesis": "...", "evidencia_citada": [{"metrica": "...", "desde": "...", "hasta": "...", "observacion": "..."}], \
"acciones_sugeridas": ["..."], "confianza": "baja|media|alta"}"""

MAX_PUNTOS_PROMPT = 60
MAX_REGISTROS_PROMPT = 15
MAX_TRAZAS_PROMPT = 10
MAX_CORRELACION_PROMPT = 15
TEXTO_MAXIMO = 300
HOLGURA = timedelta(minutes=1)
CONFIANZAS = ("baja", "media", "alta")


class SinRedaccion(ErrorModelo):
    """Falta `zenit.diagnostico.redaccion` (H-041): no se envia nada sin redactar."""


# ------------------------------------------------------------------ redaccion
def cargar_redactor() -> tuple[Callable, Callable]:
    try:
        from zenit.diagnostico.redaccion import redactar, restaurar
    except ImportError:
        raise SinRedaccion("falta la redaccion de identificadores (H-041): no se envia evidencia sin redactar") from None
    return redactar, restaurar


def conocidos_del_catalogo(conexion) -> dict[str, str]:
    """{valor: tipo} con lo que identifica a cada activo, para la redaccion:
    el id del activo, su nombre, sus claves de host.name y sus ids nativos."""
    from zenit import catalogo

    conocidos: dict[str, str] = {}
    for a in catalogo.listar_activos(conexion):
        conocidos[a["id"]] = "ACTIVO"
        if a.get("nombre"):
            conocidos.setdefault(a["nombre"], "ANFITRION")
        for clave in a.get("claves") or []:
            conocidos.setdefault(clave, "ID" if ":" in clave else "ANFITRION")
    return conocidos


# ------------------------------------------------------------------ prompt
def _corto(v):
    """Redondea a 4 cifras significativas para gastar menos tokens."""
    if isinstance(v, float):
        return float(f"{v:.4g}")
    if isinstance(v, dict):
        return {k: _corto(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_corto(x) for x in v]
    return v


def _cada(lista: list, n: int) -> list:
    if len(lista) <= n:
        return lista
    paso = len(lista) / n
    return [lista[int(i * paso)] for i in range(n)]


def compactar(anomalia: dict, ev: dict) -> dict:
    """Lo que se le envia al modelo, antes de redactar: el incidente y la
    evidencia, recortados. Los puntos solo de la serie afectada."""
    deteccion = anomalia.get("evidencia") or {}
    metricas = []
    for m in ev.get("metricas", []):
        item = {k: m.get(k) for k in ("metrica", "dimensiones", "papel", "unidad", "resolucion_s", "resumen")}
        if m.get("papel") == "afectada":
            item["puntos"] = _cada(m.get("puntos") or [], MAX_PUNTOS_PROMPT)
        metricas.append(item)
    return _corto({
        "incidente": {
            "activo": anomalia["activo"],
            "metrica": anomalia["metrica"],
            "dimensiones": anomalia.get("dimensiones") or {},
            "severidad": anomalia.get("severidad"),
            "puntaje": anomalia.get("puntaje"),
            "inicio": _iso(anomalia["inicio"]),
            "fin": _iso(anomalia.get("fin")),
            "valor_observado": deteccion.get("valor_observado"),
            "valor_esperado": deteccion.get("valor_esperado"),
            "metodo": anomalia.get("version_modelo"),
        },
        "ventana": ev.get("ventana"),
        "metricas": metricas,
        "registros_error": [{"momento": r.get("momento"), "servicio": r.get("service_name"),
                             "severidad": r.get("severidad"), "cuerpo": str(r.get("cuerpo") or "")[:TEXTO_MAXIMO]}
                            for r in ev.get("registros_error", [])[:MAX_REGISTROS_PROMPT]],
        "trazas_error": [{"raiz": t.get("raiz"), "inicio": t.get("inicio"), "errores": t.get("errores"),
                          "duracion_ms": round((t.get("duracion_ns") or 0) / 1e6, 1), "servicios": t.get("servicios")}
                         for t in ev.get("trazas_error", [])[:MAX_TRAZAS_PROMPT]],
        "correlacion": [{"momento": c.get("momento"), "servicio": c.get("servicio"), "operacion": c.get("operacion"),
                         "estado_span": c.get("estado_span"), "cuerpo": str(c.get("cuerpo") or "")[:TEXTO_MAXIMO]}
                        for c in ev.get("correlacion", [])[:MAX_CORRELACION_PROMPT]],
        "conteos": ev.get("conteos"),
        "no_recuperado": ev.get("faltantes", []),
    })


def mensaje_para_el_modelo(enviado: dict) -> str:
    return ("Evidencia del incidente (JSON). Responde con el objeto JSON que piden las reglas.\n\n"
            + json.dumps(enviado, ensure_ascii=False, separators=(",", ":")))


# ------------------------------------------------------------------ respuesta
def _iso(v) -> Optional[str]:
    if v is None:
        return None
    return v.isoformat() if hasattr(v, "isoformat") else str(v)


def _fecha(texto) -> Optional[datetime]:
    try:
        d = datetime.fromisoformat(str(texto).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d


def _json_de(texto: str) -> dict:
    """El objeto JSON de la respuesta, aunque venga envuelto en un bloque de codigo."""
    m = re.search(r"\{.*\}", texto, re.S)
    if not m:
        raise ErrorModelo("la respuesta del modelo no trae un objeto JSON")
    try:
        datos = json.loads(m.group(0))
    except ValueError:
        raise ErrorModelo("la respuesta del modelo no es JSON valido") from None
    if not isinstance(datos, dict):
        raise ErrorModelo("la respuesta del modelo no es un objeto")
    return datos


def interpretar(texto: str, ev: dict, mapa: dict, restaurar: Callable) -> dict:
    """La hipotesis lista para guardar, con las citas comprobadas.

    Una cita vale si su metrica esta en la evidencia y su intervalo cae dentro
    de la ventana de la evidencia (con un minuto de holgura). Las demas se
    descartan y se cuentan. Sin citas validas, la respuesta no sirve.
    """
    datos = _json_de(texto)
    hipotesis = str(datos.get("hipotesis") or "").strip()
    if not hipotesis:
        raise ErrorModelo("la respuesta del modelo no trae hipotesis")
    metricas = {m["metrica"] for m in ev.get("metricas", [])} | {ev.get("metrica")}
    desde_ev = _fecha(ev["ventana"]["desde"]) - HOLGURA
    hasta_ev = _fecha(ev["ventana"]["hasta"]) + HOLGURA

    citas, descartadas = [], []
    for c in datos.get("evidencia_citada") or []:
        if not isinstance(c, dict):
            continue
        d, h = _fecha(c.get("desde")), _fecha(c.get("hasta"))
        valida = (c.get("metrica") in metricas and d is not None and h is not None
                  and desde_ev <= d <= h <= hasta_ev)
        destino = citas if valida else descartadas
        destino.append({"metrica": c.get("metrica"), "desde": d.isoformat() if d else c.get("desde"),
                        "hasta": h.isoformat() if h else c.get("hasta"),
                        "observacion": restaurar(str(c.get("observacion") or ""), mapa)})
    if not citas:
        raise ErrorModelo("la hipotesis no cito ninguna evidencia comprobable")

    acciones = [restaurar(str(a), mapa) for a in (datos.get("acciones_sugeridas") or []) if str(a).strip()][:3]
    confianza = datos.get("confianza") if datos.get("confianza") in CONFIANZAS else None
    partes = [restaurar(hipotesis, mapa)]
    if acciones:
        partes.append("Acciones sugeridas para el equipo (Zenit no ejecuta ninguna):\n"
                      + "\n".join(f"- {a}" for a in acciones))
    if confianza:
        partes.append(f"Confianza de la hipotesis: {confianza}.")
    return {"texto": "\n\n".join(partes), "evidencia_citada": citas, "descartadas": descartadas,
            "acciones": acciones, "confianza": confianza}


# ------------------------------------------------------------------ todo junto
def diagnosticar(conexion, anomalia_id: int, repositorio, analitico, cliente, *,
                 redactor: Optional[tuple] = None, ahora: Optional[datetime] = None) -> Optional[dict]:
    """Genera, comprueba y guarda el diagnostico de un incidente.

    Devuelve el diagnostico con la forma del contrato, o None si el incidente
    no existe. Lanza ErrorModelo (o SinRedaccion) si no se pudo generar; en ese
    caso no se escribe nada.
    """
    from zenit import incidentes

    redactar, restaurar = redactor or cargar_redactor()
    anomalia = incidentes.anomalia(conexion, anomalia_id)
    if anomalia is None:
        return None

    ev = recuperar(anomalia, repositorio, analitico, ahora)
    conocidos = conocidos_del_catalogo(conexion)
    conocidos.setdefault(anomalia["activo"], "ACTIVO")
    enviado, mapa = redactar(compactar(anomalia, ev), conocidos)

    respuesta = cliente.generar(SISTEMA, mensaje_para_el_modelo(enviado))
    hipotesis = interpretar(respuesta.texto, ev, mapa, restaurar)

    generado = datetime.now(timezone.utc)
    detectada_en = anomalia.get("detectada_en")
    # Minimizacion (Ley 1581 de 2012, art. 4; Decreto 1377 de 2013, art. 11):
    # no se guarda el texto crudo de los registros. Los registros y trazas
    # viven 7 dias en ClickHouse; si el diagnostico guardara su texto, quedaria
    # en PostgreSQL sin plazo. Lo que queda es lo enviado, ya redactado, y los
    # conteos. La evidencia completa se vuelve a pedir en vivo
    # (GET /api/incidentes/{id}/evidencia) mientras ClickHouse la tenga.
    guardada = {
        **{k: v for k, v in ev.items() if k not in TEXTO_CRUDO},
        "enviado": enviado,                       # lo que salio de la plataforma, ya redactado (P-DIA-02)
        "citas_descartadas": hipotesis["descartadas"],
        "segundos_desde_deteccion": (round((generado - detectada_en).total_seconds(), 1)
                                     if detectada_en else None),   # RNF-REN-06
    }
    guardar_diagnostico(conexion, anomalia_id, texto=hipotesis["texto"], evidencia_citada=hipotesis["evidencia_citada"],
                        modelo=respuesta.modelo, tokens_entrada=respuesta.tokens_entrada,
                        tokens_salida=respuesta.tokens_salida, evidencia_recuperada=guardada, generado=generado)
    log.info("diagnostico del incidente %s con %s: %s citas, %s descartadas, %s/%s tokens, %s s desde la deteccion",
             anomalia_id, respuesta.modelo, len(hipotesis["evidencia_citada"]), len(hipotesis["descartadas"]),
             respuesta.tokens_entrada, respuesta.tokens_salida, guardada["segundos_desde_deteccion"])
    return {
        "texto": hipotesis["texto"],
        "evidencia_citada": hipotesis["evidencia_citada"],
        "modelo": respuesta.modelo,
        "tokens_entrada": respuesta.tokens_entrada,
        "tokens_salida": respuesta.tokens_salida,
        "generado": generado.isoformat(),
    }
