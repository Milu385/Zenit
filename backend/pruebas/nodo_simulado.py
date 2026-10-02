"""Nodo observado de mentira, para la prueba de extremo a extremo.

Habla OTLP por gRPC con el punto de entrada igual que el Collector:
utilizacion de CPU por nucleo y estado, memoria y red. Puede rellenar los
ultimos minutos y luego emitir en vivo; con --escalon, a partir de ese
momento la CPU de usuario sube como si arrancara la carga.

    python pruebas/nodo_simulado.py --destino 127.0.0.1:14317 --token x \
        --relleno-min 15 --cada 2 --escalon-tras 20
"""
import argparse
import math
import random
import time

import grpc
from opentelemetry.proto.collector.metrics.v1 import metrics_service_pb2_grpc

import otlp


def muestra(t_ns: int, carga: bool, nodo: str):
    fase = (t_ns / 1e9) / 60
    user = 0.12 + 0.03 * math.sin(fase) + random.uniform(-0.01, 0.01) + (0.55 if carga else 0)
    puntos_cpu = []
    for cpu in ("cpu0", "cpu1"):
        u = min(user + random.uniform(-0.02, 0.02), 0.98)
        s = 0.04 + random.uniform(-0.005, 0.005)
        puntos_cpu += [
            otlp.punto(u, t_ns, cpu=cpu, state="user"),
            otlp.punto(s, t_ns, cpu=cpu, state="system"),
            otlp.punto(max(0.0, 1 - u - s), t_ns, cpu=cpu, state="idle"),
        ]
    memoria = otlp.gauge("system.memory.utilization", [
        otlp.punto(0.41 + (0.2 if carga else 0), t_ns, state="used"),
        otlp.punto(0.59 - (0.2 if carga else 0), t_ns, state="free"),
    ])
    red = otlp.suma("system.network.io", [
        otlp.punto(int(t_ns / 1e7) % 10**9, t_ns, device="eth0", direction="receive"),
        otlp.punto(int(t_ns / 2e7) % 10**9, t_ns, device="eth0", direction="transmit"),
    ])
    return otlp.peticion(otlp.gauge("system.cpu.utilization", puntos_cpu), memoria, red, res=otlp.recurso(nodo))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--destino", default="127.0.0.1:4317")
    p.add_argument("--token", default="")
    p.add_argument("--nodo", default="zenit-nodo-aws")
    p.add_argument("--relleno-min", type=int, default=0)
    p.add_argument("--cada", type=float, default=10.0)
    p.add_argument("--escalon-tras", type=float, default=-1, help="segundos en vivo antes de subir la carga")
    p.add_argument("--duracion", type=float, default=3600)
    a = p.parse_args()

    stub = metrics_service_pb2_grpc.MetricsServiceStub(grpc.insecure_channel(a.destino))
    meta = [("authorization", f"Bearer {a.token}")]

    ahora = time.time_ns()
    paso = 10 * 10**9
    for k in range(a.relleno_min * 6, 0, -1):
        stub.Export(muestra(ahora - k * paso, False, a.nodo), metadata=meta, timeout=10)
    print(f"relleno de {a.relleno_min} min enviado", flush=True)

    inicio = time.time()
    while time.time() - inicio < a.duracion:
        carga = a.escalon_tras >= 0 and time.time() - inicio >= a.escalon_tras
        stub.Export(muestra(time.time_ns(), carga, a.nodo), metadata=meta, timeout=10)
        time.sleep(a.cada)


if __name__ == "__main__":
    main()
