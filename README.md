# Zenit

**Plataforma de observabilidad neutral respecto al proveedor.** Un mismo nodo observado corre idéntico en AWS, DigitalOcean, Azure o un servidor on-premise, y su telemetría llega por OpenTelemetry a un único punto de entrada que la normaliza y la asocia a un activo.

Este repositorio contiene la **épica 0**: la infraestructura mínima sobre la que se construye el resto del producto.

| Historia | Qué entrega | Dónde vive |
|---|---|---|
| **H-001** · Entorno de despliegue | Anfitrión de la plataforma con volúmenes persistentes, puertos acotados y TLS | `deploy/`, `scripts/aws/`, `scripts/tls/` |
| **H-002** · Nodo víctima portátil | Aplicación de referencia empaquetada, combinable por perfiles y configurable por entorno | `laboratorio/` |
| **H-003** · Agente estándar | OpenTelemetry Collector con plantilla única y cola persistente | `laboratorio/agente/` |
| **H-004** · Punto de entrada | Receptor OTLP propio que aplica el esquema canónico y resuelve activos | `backend/ingesta/` |

---

## Arquitectura

```
        RED DE LA PLATAFORMA                     REDES DE LOS NODOS OBSERVADOS
        (VPC por defecto, AWS)                   (una por nodo, de cualquier proveedor)

  ┌─────────────────────────────┐          ┌─────────────────────────────────┐
  │  borde · nginx              │          │  zenit-nodo-aws · VPC 10.60/16  │
  │  TLS en :4317               │◄──OTLP───│                                 │
  │         │                   │  TLS +   │  agente (OTel Collector)        │
  │         ▼  red interna      │  token   │     ▲ métricas del host         │
  │  ingesta · Python           │          │     ▲ trazas de la aplicación   │
  │  valida token               │          │  pedidos → catalogo → almacen   │
  │  esquema canónico           │          │  carga ──┘                      │
  │  resuelve activo            │          └─────────────────────────────────┘
  │         │                   │          ┌─────────────────────────────────┐
  │         ▼                   │◄──OTLP───│  DigitalOcean / Azure / on-prem │
  │  salida JSONL               │          │  (mismo compose, otro .env)     │
  └─────────────────────────────┘          └─────────────────────────────────┘
       deploy/compose.yml                        laboratorio/compose.yml
```

Tres principios que explican casi todas las decisiones:

1. **Un solo transporte para todos los nodos.** Todos llegan por el mismo puerto, con el mismo certificado y el mismo token, sea cual sea su red. Así las diferencias medidas entre entornos son del entorno y no del camino que recorre la telemetría.
2. **Los nodos no aceptan conexiones entrantes.** Son ellos los que abren la conexión hacia la plataforma, por eso funcionan detrás de un NAT sin configurar nada.
3. **Lo que cambia entre entornos es un archivo de variables.** Mismo `compose.yml`, misma imagen, distinto `.env`.

---

## Estructura

```
.
├── backend/
│   └── ingesta/                 punto de entrada OTLP (H-004)
│       ├── servidor.py
│       ├── requirements.txt
│       └── Dockerfile
├── deploy/                      la plataforma (H-001)
│   ├── compose.yml              borde + ingesta
│   ├── nginx.conf               termina TLS y reenvía por gRPC
│   ├── catalogo.json            identificador del nodo → activo
│   └── .env.example
├── laboratorio/                 el nodo observado (H-002, H-003)
│   ├── compose.yml              un archivo, cuatro montajes por perfil
│   ├── agente/
│   │   ├── collector.yaml       plantilla única del Collector
│   │   └── ca.crt               CA pública de la plataforma
│   ├── entornos/
│   │   ├── aws.env.example
│   │   ├── digitalocean.env.example
│   │   ├── azure.env.example
│   │   └── onprem.env.example
│   └── servicios/
│       ├── pedidos/             FastAPI · llama a catalogo
│       ├── catalogo/            FastAPI · lee y escribe en el almacén
│       └── carga/               generador de peticiones
├── scripts/
│   ├── aws/                     aprovisionamiento en EC2
│   ├── tls/emitir.sh            CA propia y certificado del servidor
│   └── publicar.sh              construye y sube imágenes a GHCR
├── tls/ca.crt                   CA pública (la clave privada NO está aquí)
└── docs/nodos.md                registro de nodos admitidos
```

---

## Decisiones

| Decisión | Valor | Motivo |
|---|---|---|
| Entorno AWS | AWS Academy Learner Lab | IAM bloqueado; se usa `LabInstanceProfile` y credenciales temporales del panel |
| Región | `us-east-1` | Una de las dos habilitadas por el laboratorio |
| Instancias | `t3.small` ×2 | Margen para que la saturación sea inducida y no por falta de RAM |
| Sistema operativo | Ubuntu Server 24.04 LTS | Agente de SSM preinstalado, IMDSv2 |
| Acceso a instancias | SSM Session Manager | Sin puerto 22 abierto, sin llaves que repartir |
| Registro de imágenes | GHCR, paquetes públicos | Descargable desde cualquier proveedor sin credenciales |
| Transporte | OTLP/gRPC con TLS + token | Idéntico para todos los nodos, sin peering ni rutas privadas |
| Certificado | CA propia, IP en el SAN | Sin dominio ni DNS; el `ca.crt` va versionado |
| Identidad del nodo | Variable `ZENIT_NODO` | Sobrevive a la recreación del nodo; no consulta metadatos del proveedor |
| Prefijo de nombres | `zenit-` | Para localizar y borrar recursos por etiqueta |

---

## Puesta en marcha

**Requisitos en tu equipo:** AWS CLI v2, `session-manager-plugin`, Docker con Compose y Buildx, `openssl`, `git`.

### Infraestructura (una vez, desde tu equipo)

```bash
bash scripts/aws/01-redes.sh                                      # VPC, subred, grupos de seguridad
bash scripts/aws/02-plataforma.sh <sg-plataforma>                 # instancia + IP elástica
bash scripts/tls/emitir.sh <ip-elastica>                          # CA y certificado
openssl rand -hex 32                                              # token de ingesta
bash scripts/aws/03-nodo.sh <subred-nodo> <sg-nodo> <sg-plataforma>
```

### Plataforma

```bash
aws ssm start-session --target <id-plataforma>
sudo su - ubuntu
git clone https://github.com/Milu385/Zenit.git zenit && cd zenit/deploy
cp .env.example .env && nano .env              # ORG_GITHUB, TOKEN_INGESTA
# copiar servidor.crt y servidor.key a deploy/tls/ (no van por git)
docker compose --env-file .env up -d --build --wait
```

### Nodo observado

```bash
aws ssm start-session --target <id-nodo>
sudo su - ubuntu
git clone https://github.com/Milu385/Zenit.git zenit && cd zenit/laboratorio
cp entornos/aws.env.example entornos/aws.env && nano entornos/aws.env
docker compose --env-file entornos/aws.env --profile completo --profile observado up -d --wait
```

En otro proveedor, el mismo comando con su archivo: `--env-file entornos/digitalocean.env`.

---

## Perfiles del nodo

| Comando | Qué levanta |
|---|---|
| `up -d` | `almacen` + `pedidos` |
| `--profile completo` | + `catalogo` (la llamada entre servicios) |
| `--profile carga` | + `carga` (tráfico sostenido) |
| `--profile observado` | + `agente` (emite telemetría) |

Levantar con y sin `observado` permite medir cuánto consume el propio instrumental.

## Lo que distingue a un entorno de otro

Los cuatro archivos de `laboratorio/entornos/` son idénticos salvo estas líneas:

| Variable | aws | digitalocean | azure | onprem |
|---|---|---|---|---|
| `ZENIT_PROVEEDOR` | aws | digitalocean | azure | proxmox |
| `ZENIT_REGION` | us-east-1 | nyc3 | eastus | local |
| `ZENIT_NODO` | zenit-nodo-aws | zenit-nodo-do | zenit-nodo-azure | zenit-nodo-onprem |
| `LIMITE_CPU` | 2.0 | 1.0 | 2.0 | 4.0 |
| `LIMITE_MEMORIA` | 1g | 512m | 2g | 4g |
| `IP_PEDIDOS` | — | — | — | 0.0.0.0 |

`DESTINO_OTLP`, `TOKEN_INGESTA`, `RUTA_CA`, `INTERVALO_METRICAS` y `VERSION_APP` **deben ser iguales en todos**: si difieren, las comparaciones entre entornos dejan de medir el entorno.

---

## Scripts

| Script | Tipo | Cuándo |
|---|---|---|
| `scripts/aws/01-redes.sh` | Uso único | Crea la red. No repetir: duplicaría la VPC |
| `scripts/aws/02-plataforma.sh` | Uso único | Crea la instancia de la plataforma |
| `scripts/aws/03-nodo.sh` | Recurrente | Una vez por cada nodo EC2 |
| `scripts/aws/admitir-nodo.sh` | Recurrente | Cada nodo nuevo de cualquier proveedor |
| `scripts/aws/userdata-docker.sh` | No se ejecuta a mano | Lo corre EC2 en el primer arranque |
| `scripts/tls/emitir.sh` | Ocasional | Solo si cambia la IP elástica |
| `scripts/publicar.sh <versión>` | Recurrente | Cada versión nueva de la aplicación |

---

## Seguridad: qué no está en este repositorio

| Archivo | Por qué | Dónde vive |
|---|---|---|
| `tls/ca.key` | Firma certificados en nombre de la CA | Solo en el equipo de quien la creó, con copia de respaldo |
| `tls/servidor.key` | Clave privada del borde | En `deploy/tls/` de la instancia de la plataforma |
| `deploy/.env`, `laboratorio/entornos/*.env` | Contienen el token y la clave del almacén | En cada máquina, creados desde su `.example` |

> **`tls/ca.key` es irreemplazable.** Si se pierde, hay que generar una CA nueva, reemitir el certificado y distribuir el `ca.crt` nuevo a todos los nodos.

---

## Verificación rápida

```bash
# plataforma: servicios sanos y puertos correctos
docker compose ps
ss -tlnp | grep -E '4317|8080'            # 0.0.0.0:4317 y 127.0.0.1:8080

# desde tu equipo: el TLS valida contra la CA
openssl s_client -connect <ip-elastica>:4317 -CAfile tls/ca.crt </dev/null 2>&1 | grep Verify

# nodo: la cadena de la aplicación responde
curl -X POST http://localhost:8000/pedidos

# plataforma: señales recibidas, emitidas, huérfanas y rechazadas
curl -s localhost:8080/metricas
```

---

## Estado de la épica 0

| | Verificado |
|---|---|
| H-001 | ☐ Servicios `healthy` · ☐ sobrevive a reinicio · ☐ TLS valida desde fuera |
| H-002 | ☑ Cadena `pedidos → catalogo → almacen` · ☑ desechable en local y en EC2 · ☐ portable entre dos entornos |
| H-003 | ☐ Agente emite · ☐ la cola persistente no pierde puntos durante un corte |
| H-004 | ☐ Recibe OTLP · ☐ conteo de entrada = conteo de salida · ☐ huérfanas marcadas, no descartadas |

La épica se cierra con el ensayo completo: destruir el nodo, recrearlo desde este repositorio y ver llegar a la plataforma dos valores distintos de `cloud_provider` desde dos redes que no se conocen.
