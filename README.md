# Zenit

**Plataforma de observabilidad neutral respecto al proveedor.** Un mismo nodo observado corre idéntico en AWS, DigitalOcean, Azure o un servidor on-premise, y su telemetría llega por OpenTelemetry a un único punto de entrada que la normaliza y la asocia a un activo.

Este repositorio contiene la **épica 0**, la infraestructura mínima sobre la que se construye el resto del producto, y la **épica 2**, el laboratorio de fallos que produce los datos y la verdad de referencia para evaluar la detección (sección [Laboratorio de fallos](#laboratorio-de-fallos-épica-2)).

| Historia | Qué entrega | Dónde vive |
|---|---|---|
| **H-001** · Entorno de despliegue | Anfitrión de la plataforma con volúmenes persistentes, puertos acotados y TLS | `deploy/`, `scripts/aws/`, `scripts/tls/` |
| **H-002** · Nodo víctima portátil | Aplicación de referencia empaquetada, combinable por perfiles y configurable por entorno | `laboratorio/` |
| **H-003** · Agente estándar | OpenTelemetry Collector con plantilla única y cola persistente | `laboratorio/agente/` |
| **H-004** · Punto de entrada | Receptor OTLP propio que aplica el esquema canónico y resuelve activos | `backend/ingesta/` |
| **H-005** · Almacén de métricas | InfluxDB 3 Core con las tres bases de resolución, alimentado por el punto de entrada | `deploy/compose.yml`, `backend/zenit/admin.py` |
| **H-006** · Repositorio y endpoint | Interfaz de repositorio que aísla el motor y API con la serie de una métrica | `backend/zenit/repositorio/`, `backend/zenit/api/` |
| **H-007** · Vista de serie | Gráfica en el navegador con estados de carga, error y sin datos | `frontend/` |
| **H-008** · Despliegue completo | Todo lo anterior desde los archivos de este repositorio | este README |

---

## Arquitectura

```
        RED DE LA PLATAFORMA                     REDES DE LOS NODOS OBSERVADOS
        (VPC por defecto, AWS)                   (una por nodo, de cualquier proveedor)

  ┌─────────────────────────────┐          ┌─────────────────────────────────┐
  │  borde · nginx              │          │  zenit-nodo-aws · VPC 10.60/16  │
  │  TLS en :4317 y :443        │◄──OTLP───│                                 │
  │     │ :4317       │ :443    │  TLS +   │  agente (OTel Collector)        │
  │     ▼             ▼         │  token   │     ▲ métricas del host         │
  │  ingesta       web · api    │          │     ▲ trazas de la aplicación   │
  │  valida token     │         │          │  pedidos → catalogo → almacen   │
  │  esquema canónico │         │          │  carga ──┘                      │
  │  resuelve activo  │         │          └─────────────────────────────────┘
  │     │             │         │          ┌─────────────────────────────────┐
  │     ▼             ▼         │◄──OTLP───│  DigitalOcean / Azure / on-prem │
  │  influxdb (zenit_raw,       │          │  (mismo compose, otro .env)     │
  │  zenit_1m, zenit_1h)        │          └─────────────────────────────────┘
  └─────────────────────────────┘
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
│   ├── ingesta/                 punto de entrada OTLP (H-004)
│   │   ├── servidor.py          gRPC, token, contadores, catálogo
│   │   ├── extraccion.py        de OTLP a puntos canónicos
│   │   ├── destinos.py          escritura por lotes al almacén
│   │   └── Dockerfile           se construye desde backend/
│   ├── zenit/                   paquete compartido
│   │   ├── esquema.py           nombres canónicos
│   │   ├── protocolo_linea.py   escritura en protocolo de línea
│   │   ├── influx.py            único cliente HTTP del motor
│   │   ├── repositorio/         contrato e implementación (H-006)
│   │   ├── api/main.py          API de consulta (H-006)
│   │   ├── verdad.py            cargador de la verdad de referencia (H-024)
│   │   └── admin.py             crear bases, verificar, huecos, cargar-verdad, exportar
│   ├── pruebas/                 pytest, doble de InfluxDB, nodo simulado
│   └── Dockerfile.api
├── frontend/                    vista de serie (H-007), React + uPlot
├── deploy/                      la plataforma (H-001, H-008)
│   ├── compose.yml              borde, ingesta, influxdb, api, web, postgres, cargador
│   ├── postgres/01-esquema.sql  tablas del contrato de la API (inyecciones, anomalías...)
│   ├── nginx.conf               TLS en 4317 (gRPC) y 443 (interfaz)
│   ├── catalogo/catalogo.json   identificador del nodo → activo
│   └── .env.example
├── laboratorio/                 el nodo observado (H-002, H-003)
│   ├── compose.yml              un archivo, cuatro montajes por perfil
│   ├── perfiles/                perfiles de carga versionados (H-023)
│   ├── agente/
│   │   ├── collector.yaml       plantilla única del Collector (métricas, trazas, verdad)
│   │   └── ca.crt               CA pública de la plataforma
│   ├── entornos/
│   │   ├── aws.env.example
│   │   ├── digitalocean.env.example
│   │   ├── azure.env.example
│   │   └── onprem.env.example
│   └── servicios/
│       ├── pedidos/             FastAPI · llama a catalogo
│       ├── catalogo/            FastAPI · lee y escribe en el almacén
│       ├── carga/               generador de peticiones con perfil diurno (H-023)
│       └── inyector/            fallos, verdad de referencia y campaña (H-024 a H-030)
├── scripts/
│   ├── aws/                     aprovisionamiento en EC2
│   ├── do/                      aprovisionamiento en DigitalOcean (épica 2)
│   ├── exportar.sh              datos del laboratorio para trabajar sin la plataforma
│   ├── influx/preparar-token.sh token de InfluxDB, una sola vez
│   ├── tls/emitir.sh            CA propia y certificado del servidor
│   └── publicar.sh              construye y sube imágenes a GHCR
├── tls/ca.crt                   CA pública (la clave privada NO está aquí)
└── docs/
    ├── nodos.md                 registro de nodos admitidos
    ├── esquema-medicion.md      tablas, etiquetas y campos en InfluxDB (H-005)
    └── verificacion-epica-0.md  evidencia de cierre de la épica 0
```

---

## Decisiones

| Decisión | Valor | Motivo |
|---|---|---|
| Entorno AWS | AWS Academy Learner Lab | IAM bloqueado; se usa `LabInstanceProfile` y credenciales temporales del panel |
| Región | `us-east-1` | Una de las dos habilitadas por el laboratorio |
| Instancias | `t3.small` ×2 | Margen para que la saturación sea inducida y no por falta de RAM |
| Disco | 30 GB en la plataforma, 20 GB en el nodo, `gp3` | Las tres imágenes se construyen en la plataforma; con los 8 GB por defecto el build se queda sin espacio |
| Sistema operativo | Ubuntu Server 24.04 LTS | Agente de SSM preinstalado, IMDSv2 |
| Acceso a instancias | SSM Session Manager | Sin puerto 22 abierto, sin llaves que repartir |
| Registro de imágenes | GHCR, paquetes públicos | Descargable desde cualquier proveedor sin credenciales |
| Transporte | OTLP/gRPC con TLS + token | Idéntico para todos los nodos, sin peering ni rutas privadas |
| Certificado | CA propia, IP en el SAN | Sin dominio ni DNS; el `ca.crt` va versionado |
| Identidad del nodo | Variable `ZENIT_NODO` | Sobrevive a la recreación del nodo; no consulta metadatos del proveedor |
| Prefijo de nombres | `zenit-` | Para localizar y borrar recursos por etiqueta |

---

## Aprovisionamiento

Lo que crean `scripts/aws/01-redes.sh`, `02-plataforma.sh` y `03-nodo.sh`.

| | Plataforma | Nodo observado |
|---|---|---|
| Instancia | `t3.small` (2 vCPU, 2 GB) | `t3.small` (2 vCPU, 2 GB) |
| Sistema | Ubuntu Server 24.04 LTS, IMDSv2 | Ubuntu Server 24.04 LTS, IMDSv2 |
| Disco raíz | 30 GB `gp3` | 20 GB `gp3` |
| Red | VPC por defecto | VPC propia `10.60.0.0/16`, subred `10.60.1.0/24` |
| IP pública | Elástica | Elástica |
| Perfil de instancia | `LabInstanceProfile` (SSM) | `LabInstanceProfile` (SSM) |
| Docker | Instalado por `userdata-docker.sh` | Instalado por `userdata-docker.sh` |

Grupos de seguridad. Ninguno abre el 22: el acceso es por SSM.

| Grupo | Puerto | Origen | Lo agrega |
|---|---|---|---|
| `zenit-plataforma` | 4317/tcp (OTLP con TLS) | La IP de cada nodo, `/32` | `admitir-nodo.sh` |
| `zenit-plataforma` | 443/tcp (interfaz y API) | La IP de cada integrante, `/32` | `abrir-interfaz.sh` |
| `zenit-nodo` | — | Sin reglas de entrada: el nodo solo abre conexiones hacia fuera | — |

Dentro de la plataforma, InfluxDB (8181), la API (8000) y la interfaz (80) no publican puertos, y el administrador de la ingesta (8080) solo escucha en `127.0.0.1`.

---

## Puesta en marcha

**Requisitos en tu equipo:** AWS CLI v2, `session-manager-plugin`, Docker con Compose y Buildx, `openssl`, `git`.

### Infraestructura (una vez, desde tu equipo)

```bash
bash scripts/aws/01-redes.sh                                      # VPC, subred, grupos de seguridad
bash scripts/aws/02-plataforma.sh <sg-plataforma>                 # instancia + IP elástica
bash scripts/tls/emitir.sh <ip-elastica>                          # certificado (exige tls/ca.key, ver Seguridad)
openssl rand -hex 32                                              # token de ingesta
bash scripts/aws/03-nodo.sh <subred-nodo> <sg-nodo> <sg-plataforma>   # instancia + IP elástica + admisión
```

`emitir.sh` se corre desde la raíz del repositorio, en el equipo donde está
`tls/ca.key`. Si no la encuentra, se detiene en lugar de crear otra CA: con una
CA distinta, los nodos rechazan el certificado con `certificate signed by
unknown authority`.

### Plataforma

```bash
aws ssm start-session --target <id-plataforma>
sudo su - ubuntu
git clone https://github.com/Milu385/Zenit.git zenit && cd zenit/deploy
df -h /                                        # debe decir unos 29G; si dice 8G, ver Problemas conocidos
cp .env.example .env && nano .env              # ORG_GITHUB (minúsculas), TOKEN_INGESTA
bash ../scripts/influx/preparar-token.sh       # token de InfluxDB, una sola vez
```

El certificado del borde no viaja por git. Se pega desde tu equipo, donde lo
emitió `emitir.sh` (carpeta `tls/` de la raíz del repositorio), con un
*heredoc* por archivo. Si falta alguno de los dos, `docker compose up` se
detiene y dice cuál.

```bash
mkdir -p tls && sudo chown ubuntu:ubuntu tls
cat > tls/servidor.crt <<'EOF'
(contenido de tls/servidor.crt de tu equipo, de BEGIN a END)
EOF
cat > tls/servidor.key <<'EOF'
(contenido de tls/servidor.key de tu equipo)
EOF
chmod 600 tls/servidor.key
ls -l tls/                                     # los dos deben empezar con "-", no con "d"
# el certificado y la llave tienen que ser pareja: los dos md5 iguales
openssl x509 -in tls/servidor.crt -noout -pubkey | openssl md5
openssl pkey -in tls/servidor.key -pubout | openssl md5

docker compose --env-file .env up -d --build --wait
```

El primer arranque construye tres imágenes (ingesta, API e interfaz) y crea
las bases `zenit_raw`, `zenit_1m` y `zenit_1h` con su retención. Después,
desde tu equipo, abre la interfaz para tu IP:

```bash
bash scripts/aws/abrir-interfaz.sh <sg-plataforma> $(curl -s https://checkip.amazonaws.com) <tu-nombre>
```

y entra a `https://<ip-elastica>/`. El navegador va a advertir que no conoce
la CA; se puede importar `tls/ca.crt` como autoridad de confianza o aceptar la
advertencia.

**Admitir un nodo** son dos pasos: abrir el 4317 a su IP con
`admitir-nodo.sh` y agregar su `ZENIT_NODO` a `deploy/catalogo/catalogo.json`.
El punto de entrada relee el catálogo solo, en menos de diez segundos; ya no
hace falta reiniciarlo.

**Si un nodo cambia de IP.** La regla del 4317 admite una sola IP. Un nodo sin
IP elástica, o uno de otro proveedor con IP dinámica, recibe una IP nueva al
reiniciarse, y su agente queda en `dial tcp <ip>:4317: i/o timeout`. Hay que
admitir la IP nueva y retirar la vieja:

```bash
# en el nodo
curl -s https://checkip.amazonaws.com
# en tu equipo
aws ec2 describe-security-groups --group-ids <sg-plataforma> \
  --query "SecurityGroups[].IpPermissions[?ToPort==\`4317\`].IpRanges[]" --output table
bash scripts/aws/admitir-nodo.sh <sg-plataforma> <ip-nueva> <etiqueta>
aws ec2 revoke-security-group-ingress --group-id <sg-plataforma> --protocol tcp --port 4317 --cidr <ip-vieja>/32
```

Para que no vuelva a pasar con un nodo EC2 creado antes de que `03-nodo.sh`
asignara IP elástica:

```bash
ALLOC=$(aws ec2 allocate-address --domain vpc --query AllocationId --output text)
aws ec2 associate-address --instance-id <id-nodo> --allocation-id "$ALLOC"
```

y admitir esa IP como arriba.

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

## Laboratorio de fallos (épica 2)

La plataforma corre en un droplet de DigitalOcean siempre encendido. Los nodos
observados son dos: uno on-premise en el Proxmox y uno de nube en otro droplet.
Cada nodo corre su aplicación, su generador de carga, su agente y un inyector
que provoca fallos de forma programada. La planeación completa está en
`plan-epica-2.md` del proyecto.

```
  nodo (Proxmox o DigitalOcean)                          plataforma (DigitalOcean)
  inyector ──escribe antes de inyectar──▶ verdad.jsonl
                                            │ filelog
  pedidos, catalogo, carga ──trazas──▶ agente ──OTLP+TLS──▶ borde ▶ ingesta ▶ influxdb
  /proc, /sys del anfitrión ──métricas──▶   │                           └▶ registros ▶ cargador ▶ postgres
```

El inyector no tiene red (`network_mode: none`): no conoce la plataforma ni
tiene credenciales suyas. La verdad de referencia sale del nodo por el agente,
como registro OTLP, igual que las métricas (RF-LAB-04).

### Plataforma en DigitalOcean

Requisitos en tu equipo, además de los de arriba: `doctl` autenticado
(`doctl auth init`) y una llave SSH registrada en DigitalOcean
(`doctl compute ssh-key list`).

```bash
bash scripts/do/01-plataforma.sh <llave-ssh>          # droplet + IP reservada + cortafuegos (solo 22 desde tu IP)
bash scripts/tls/emitir.sh <ip-reservada>             # certificado nuevo con la CA vigente (exige tls/ca.key)
bash scripts/do/abrir-equipo.sh $(curl -s https://checkip.amazonaws.com) juanjo
ssh root@<ip-reservada>
```

En el droplet, cuando exista `/var/lib/zenit-listo`:

```bash
git clone https://github.com/Milu385/Zenit.git zenit && cd zenit/deploy
cp .env.example .env && nano .env      # ORG_GITHUB, TOKEN_INGESTA, PG_CLAVE (openssl rand -hex 24)
bash ../scripts/influx/preparar-token.sh
mkdir -p tls                            # y pega servidor.crt y servidor.key como en la sección Plataforma
docker compose --env-file .env up -d --build --wait
docker compose ps -a                    # ocho contenedores: siete arriba e influxdb-bases terminado con código 0
```

El cortafuegos de DigitalOcean está fuera del droplet, así que Docker no lo
puede saltar (con `ufw` dentro del droplet, los puertos publicados por Docker
sí lo saltarían).

### Nodo de nube en DigitalOcean

```bash
bash scripts/do/02-nodo.sh <llave-ssh> zenit-nodo-do    # crea el droplet y lo admite en el 4317
ssh root@<ip-del-nodo>
git clone https://github.com/Milu385/Zenit.git zenit && cd zenit/laboratorio
cp entornos/digitalocean.env.example entornos/digitalocean.env && nano entornos/digitalocean.env
#   DESTINO_OTLP=<ip-reservada>:4317, TOKEN_INGESTA, ORG_GITHUB, ALMACEN_CLAVE
docker compose --env-file entornos/digitalocean.env \
  --profile completo --profile carga --profile observado --profile laboratorio up -d --build --wait
```

Y en la plataforma, agrega `"zenit-nodo-do": "activo-do-nodo-01"` a
`deploy/catalogo/catalogo.json`. La ingesta lo relee sola.

### Nodo on-premise en el Proxmox

Una máquina virtual con Ubuntu Server 24.04, 2 vCPU, 4 GB de RAM y 30 GB de disco.
En la máquina virtual:

```bash
sudo apt-get install -y git
git clone https://github.com/Milu385/Zenit.git zenit && cd zenit
sudo bash scripts/do/userdata-docker.sh              # Docker, swap, reloj y rotación de registros, igual que en los droplets
sudo usermod -aG docker "$USER" && newgrp docker
curl -s https://checkip.amazonaws.com                # IP con la que sale a internet
```

Desde tu equipo, `bash scripts/do/admitir-nodo.sh <esa-ip> zenit-nodo-onprem`.
De vuelta en la máquina virtual:

```bash
cd ~/zenit/laboratorio
cp entornos/onprem.env.example entornos/onprem.env && nano entornos/onprem.env
#   DESTINO_OTLP=<ip-reservada>:4317, TOKEN_INGESTA, ORG_GITHUB, ALMACEN_CLAVE
docker compose -f compose.yml -f compose.onprem.yml --env-file entornos/onprem.env \
  --profile completo --profile carga --profile observado --profile laboratorio up -d --build --wait
```

Y en el catálogo, `"zenit-nodo-onprem": "activo-onprem-nodo-01"`. Si la red del
Proxmox sale con IP dinámica, cada cambio corta el nodo hasta admitir la IP
nueva y retirar la vieja con `scripts/do/retirar.sh 4317 <ip-vieja>`.

### Manejar la campaña

La campaña arranca sola con el perfil `laboratorio`. La primera vez espera
`CALENTAMIENTO_H` horas (12 por defecto) sin inyectar, para que haya
comportamiento normal de referencia. Después inyecta cada 60 a 150 minutos,
con al menos 45 minutos de normalidad entre una inyección y la siguiente.

```bash
docker compose --env-file entornos/<entorno>.env logs -f inyector                 # qué hace y qué viene
docker compose --env-file entornos/<entorno>.env exec inyector python -m inyector plan --n 10
docker compose --env-file entornos/<entorno>.env exec inyector python -m inyector abiertas
docker compose --env-file entornos/<entorno>.env stop inyector                    # pausa: cierra la inyección en curso como fallida y restaura el nodo
```

Las primeras inyecciones de cada tipo conviene hacerlas a mano, mirando la
gráfica. Con la campaña detenida:

```bash
docker compose --env-file entornos/<entorno>.env run --rm inyector python -m inyector manual cpu --carga 70 --duracion 300
#   memoria --objetivo 0.75 · disco --modo gradual --objetivo 0.8 --duracion 600 · caida --servicio catalogo --duracion 180
```

Topes de seguridad: CPU 90 %, memoria ocupada 85 % del total, disco 85 % del
sistema de archivos. Si el nodo ya está por encima, la inyección queda
`fallida` con el motivo y no se ejecuta.

### La verdad de referencia en la plataforma

```bash
cd ~/zenit/deploy
docker compose logs cargador --tail 5        # inyecciones=N cambiadas=M descartadas=0, una línea por minuto
docker compose exec postgres psql -U zenit -c \
  "SELECT nodo, tipo, estado, inicio, fin - inicio AS duracion FROM inyecciones ORDER BY inicio DESC LIMIT 10"
```

Solo cuentan las inyecciones `completada`. Una `fallida` lleva su motivo en
`parametros->>'motivo'`. Toda inyección cerrada dice además qué pasó con el
efecto en `parametros->'cierre'`: `efecto` es `ninguno` (no se llegó a
provocar), `parcial` (se cortó) o `completo`, con `efecto_inicio` y
`efecto_fin`. Si el nodo se apagó a mitad, el cierre lleva `fin_estimado`.

**Si una inyección no aparece o se queda `en_curso`.** La línea viaja por un
exportador que reintenta sin límite, pero si algo la perdió en el camino, el
archivo del nodo sigue teniendo todo. En el nodo:

```bash
docker compose --env-file entornos/<entorno>.env cp inyector:/var/lib/zenit-lab/verdad.jsonl ./verdad-<nodo>.jsonl
```

Cópialo a la plataforma (`scp`) y cárgalo; el upsert no duplica nada:

```bash
cd ~/zenit/deploy
docker compose run --rm -v "$PWD/../verdad-<nodo>.jsonl:/tmp/verdad.jsonl:ro" cargador \
  python -m zenit.admin cargar-verdad --crudo /tmp/verdad.jsonl --activo <activo-del-nodo>
```

### Exportar datos para la evaluación

```bash
bash ~/zenit/scripts/exportar.sh 2026-10-06T00:00Z 2026-10-07T00:00Z
bash ~/zenit/scripts/exportar.sh ayer
```

Deja en `~/zenit/exportaciones/` un `.tar.gz` con un Parquet por métrica (UTC,
etiquetas, dimensiones y `value`), `inyecciones.csv`, `huecos.csv` y
`manifiesto.json`. Para una exportación diaria automática, en la plataforma:

```bash
mkdir -p /root/zenit/exportaciones
( crontab -l 2>/dev/null; echo '20 0 * * * bash /root/zenit/scripts/exportar.sh ayer >> /root/zenit/exportaciones/cron.log 2>&1' ) | crontab -
```

Y desde tu equipo: `scp root@<ip-reservada>:zenit/exportaciones/*.tar.gz .`

El nivel crudo de InfluxDB guarda 30 días (`RETENCION_RAW`), lo que dura la
campaña, así que una exportación que falle un día se puede repetir después.

### Qué métrica mueve cada fallo

| Fallo | Métrica principal | Apoyo |
|---|---|---|
| CPU | `system_cpu_utilization`, `state=user` | `system_cpu_load_average_1m` |
| Memoria | `system_memory_utilization`, `state=used` | `system_paging_*` |
| Disco gradual o abrupto | `system_filesystem_usage`, `state=used` | `system_disk_io` |
| Caída de servicio | `traces_span_metrics_calls` del servicio (acumulada: la tasa es la diferencia) | `system_network_io` |

`traces_span_metrics_*` sale del conector `spanmetrics` del agente, a partir de
las trazas de `pedidos` y `catalogo`. Se reinicia cuando el agente se reinicia.
Las trazas en sí no se envían a la plataforma: solo alimentan esas métricas,
hasta que H-017 les dé un almacén.

---

## Perfiles del nodo

| Comando | Qué levanta |
|---|---|
| `up -d` | `almacen` + `pedidos` |
| `--profile completo` | + `catalogo` (la llamada entre servicios) |
| `--profile carga` | + `carga` (tráfico con el perfil de `CARGA_PERFIL`) |
| `--profile observado` | + `agente` (emite telemetría y la verdad de referencia) |
| `--profile laboratorio` | + `inyector` (campaña de fallos desatendida) |

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
| `scripts/aws/abrir-interfaz.sh` | Recurrente | Cada IP del equipo que vaya a usar la interfaz |
| `scripts/influx/preparar-token.sh` | Uso único | Antes del primer arranque de la plataforma |
| `scripts/tls/emitir.sh` | Ocasional | Solo si cambia la IP elástica. Exige `tls/ca.key`; `NUEVA_CA=1` crea una CA nueva a propósito |
| `scripts/publicar.sh <versión>` | Recurrente | Cada versión nueva de la aplicación |
| `scripts/do/01-plataforma.sh` | Uso único | Crea el droplet de la plataforma, su IP reservada y su cortafuegos |
| `scripts/do/02-nodo.sh` | Recurrente | Un droplet por cada nodo observado en DigitalOcean |
| `scripts/do/admitir-nodo.sh` | Recurrente | Abre el 4317 a un nodo de cualquier proveedor, el Proxmox incluido |
| `scripts/do/abrir-equipo.sh` | Recurrente | Abre el 443 y el 22 a la IP de un integrante |
| `scripts/do/retirar.sh` | Ocasional | Quita una IP de un puerto (un nodo que cambió de IP) |
| `scripts/do/userdata-docker.sh` | No se ejecuta a mano | Lo corre el droplet al nacer; sirve también para la VM del Proxmox |
| `scripts/exportar.sh` | Diario | En la plataforma: datos del laboratorio para la evaluación |
| `scripts/instantanea-nodo.sh` | Recurrente | Instantánea de configuración de un nodo para el reporte de configuraciones (ver `NOTAS.md`) |

---

## Seguridad: qué no está en este repositorio

| Archivo | Por qué | Dónde vive |
|---|---|---|
| `tls/ca.key` | Firma certificados en nombre de la CA | Solo en el equipo de quien la creó, con copia de respaldo |
| `tls/servidor.key` | Clave privada del borde | En `deploy/tls/` de la instancia de la plataforma |
| `deploy/.env`, `laboratorio/entornos/*.env` | Contienen el token y la clave del almacén | En cada máquina, creados desde su `.example` |
| `deploy/secretos/influx-admin.json` | Token de administración de InfluxDB | Solo en la instancia de la plataforma |

> **`tls/ca.key` es irreemplazable.** Si se pierde, hay que generar una CA nueva, reemitir el certificado y distribuir el `ca.crt` nuevo a todos los nodos.

**Una sola CA.** La vigente se creó el 2 de octubre de 2026 en el equipo de
Juan José Tamayo, que la custodia; la anterior quedó retirada. Reglas:

- `tls/ca.key` no sale de ese equipo salvo como copia de respaldo cifrada, y
  dónde está esa copia queda anotado en `docs/Decisiones.md`.
- Todo certificado nuevo lo emite el custodio con `emitir.sh`.
- El `ca.crt` versionado (en `tls/` y en `laboratorio/agente/`) tiene que ser
  el de esa CA. Para comprobarlo, la huella debe coincidir en los dos sitios y
  en cada nodo:
  `openssl x509 -in laboratorio/agente/ca.crt -noout -fingerprint -sha256`.

---

## Verificación rápida

```bash
# plataforma: seis servicios, influxdb-bases terminado con código 0
docker compose ps -a
ss -tlnp | grep -E '4317|443|8080|8181|8000'   # solo 0.0.0.0:4317, 0.0.0.0:443 y 127.0.0.1:8080

# desde el NODO OBSERVADO (el grupo de seguridad no admite tu equipo en el 4317)
openssl s_client -connect <ip-elastica>:4317 -CAfile ~/zenit/laboratorio/agente/ca.crt </dev/null 2>&1 | grep -i verif

# nodo: la cadena de la aplicación responde
curl -X POST http://localhost:8000/pedidos

# plataforma: contadores por punto de datos
curl -s localhost:8080/metricas | python3 -m json.tool
#   senales.metricas: recibidas = emitidas + sin_valor + errores + rechazadas_por_almacen + en_cola
#   en reposo, en_cola es 0; huerfanas cuenta los puntos de nodos fuera del catálogo

# plataforma: esquema del almacén (H-005)
docker compose exec api python -m zenit.admin verificar

# plataforma: saltos de más de 20 s en los últimos 30 minutos (H-003, corte de 5 minutos)
docker compose exec api python -m zenit.admin huecos activo-aws-nodo-01 --minutos 30

# desde fuera: el almacén, la API y el administrador NO responden
nc -zv -w 3 <ip-elastica> 8181; nc -zv -w 3 <ip-elastica> 8000; nc -zv -w 3 <ip-elastica> 8080
```

**Prueba de corte (H-003).** Con el nodo emitiendo: `docker compose stop borde`,
esperar cinco minutos, `docker compose start borde`, esperar un minuto y correr
`huecos`. La cola persistente del agente debe haber entregado todo: cero
huecos de más de 20 s. Si `docker compose stop borde` tarda, es la ingesta
vaciando su cola; está bien.

**Prueba de cierre de la épica (H-007).** Con la interfaz abierta en
`system_cpu_utilization`, levantar en el nodo `--profile carga` (o
`stress-ng --cpu 2 --timeout 120`) y medir cuánto tarda en verse el escalón.
RNF-REN-01 pide 30 segundos en el percentil 95: el agente agrupa hasta 5 s,
la ingesta 1 s y la vista refresca cada 10 s.

---

## Estado de la épica 0

Verificado en AWS el 3 de octubre de 2026. La evidencia, paso por paso, está en
[`docs/verificacion-epica-0.md`](docs/verificacion-epica-0.md).

| | Verificado |
|---|---|
| H-001 | ☑ Servicios `healthy` · ☑ sobrevive a reinicio del contenedor y de la instancia · ☑ desde fuera solo responden 4317 y 443 · ☐ instancia limpia solo con este README |
| H-002 | ☑ Cadena `pedidos → catalogo → almacen` · ☑ desechable en local y en EC2 · ☑ CPU, memoria, disco y red · ☐ portable entre dos entornos |
| H-003 | ☑ Agente emite a 10 s · ☑ un corte de 5 minutos no deja huecos |
| H-004 | ☑ Recibe OTLP · ☑ conteo de entrada = conteo de salida · ☑ huérfanas marcadas y guardadas como `sin_resolver` |
| H-005 | ☑ Tres bases creadas · ☑ métricas consultables · ☑ `zenit.admin verificar` sin fallas · ☑ sobrevive a reinicio · ☑ esquema en [`docs/esquema-medicion.md`](docs/esquema-medicion.md) |
| H-006 | ☑ Endpoint en `/api/docs` · ☐ revisión cruzada: ninguna consulta al motor fuera de `repositorio/influx.py` y `admin.py` (`grep -rn "\.consultar(" backend --include='*.py' --exclude-dir=.venv \| grep -v pruebas`) |
| H-007 | ☑ Gráfica con rango configurable · ☑ escalón de carga visible en 19 s (límite 30 s) · ☑ estados de carga, error y sin datos |
| H-008 | ☑ Compose versionado · ☑ plantillas sin valores, historial sin secretos · ☑ aprovisionamiento documentado · ☐ despliegue desde instancia limpia por alguien que no lo construyó, solo con este README |

La épica se cierra formalmente con las tres casillas que faltan: el nodo
on-premise reportando junto al de AWS, la revisión cruzada de H-006 y la
reproducción desde cero.

---

## Problemas conocidos

**Disco de 8 GB.** Si `df -h /` dice 8G, la instancia se creó con una versión
anterior de `02-plataforma.sh`, que pedía el disco en `/dev/xvda` cuando la AMI
de Ubuntu usa `/dev/sda1`: EC2 agregó un volumen aparte y dejó la raíz en 8 GB.
El build falla con `no space left on device`. Se arregla sin recrear la
instancia:

```bash
# en tu equipo: ampliar el volumen raíz
VOL=$(aws ec2 describe-instances --instance-ids <id-plataforma> \
  --query 'Reservations[0].Instances[0].BlockDeviceMappings[?DeviceName==`/dev/sda1`].Ebs.VolumeId' --output text)
aws ec2 modify-volume --volume-id "$VOL" --size 30
# en la plataforma, cuando el volumen diga "optimizing" o "completed"
sudo growpart /dev/nvme0n1 1 && sudo resize2fs /dev/nvme0n1p1 && df -h /
docker builder prune -af
```

El volumen extra que haya quedado sin usar se puede borrar desde la consola.

**El borde reinicia en bucle.** Casi siempre es el certificado. `docker compose
logs borde --tail 20` dice cuál: `cannot load certificate` (archivo vacío o mal
pegado), `key values mismatch` (el certificado y la llave no son pareja) o
`is a directory` (Docker creó un directorio en lugar del archivo; con
`create_host_path: false` en el compose ya no debería pasar). En el último
caso: `docker compose rm -sf borde`, `sudo rm -rf tls/servidor.crt
tls/servidor.key` y volver a pegarlos.

**El agente no llega a la plataforma.** El error del agente
(`docker logs zenit-laboratorio-agente-1 --tail 20`) dice dónde está el
problema:

| Error | Causa | Arreglo |
|---|---|---|
| `connection refused` | El borde no está arriba | `docker compose ps` en la plataforma |
| `i/o timeout` | El grupo de seguridad no admite la IP actual del nodo | Ver "Si un nodo cambia de IP" |
| `certificate signed by unknown authority` | El `ca.crt` del nodo no es el de la CA que firmó el certificado | Comparar huellas (ver Seguridad) |

**Memoria.** Una `t3.small` tiene 2 GB y la AMI de Ubuntu no trae swap. InfluxDB
reserva por defecto una parte de la memoria para consultas y caché, y el primer
`--build` compila tres imágenes en la misma máquina. Si el build o un
contenedor muere sin mensaje (`docker compose ps` muestra `Exited (137)`), es
el OOM. Un swap de 2 GB lo evita:

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

**Token de InfluxDB perdido.** InfluxDB registra el token del archivo
`deploy/secretos/influx-admin.json` la primera vez que arranca con el volumen
vacío, y después lo ignora. Si se pierde `deploy/.env` y se genera un token
nuevo, `influxdb-bases` falla con 401. Hay dos salidas: recuperar el `.env`
anterior (por eso conviene respaldarlo) o, si los datos no importan, borrar el
volumen con `docker compose down -v` y volver a arrancar.

**Ventana de pérdida.** Un punto que la ingesta ya aceptó vive en memoria
hasta que se escribe, normalmente menos de un segundo. Si el proceso muere de
golpe en ese lapso, ese punto se pierde: el agente ya lo dio por entregado. Un
`docker compose stop` o un reinicio ordenado vacían la cola antes de salir.

---

## Uso de inteligencia artificial

Parte del código, las pruebas y la documentación de este repositorio se
escribió con asistencia de Claude (Anthropic), un modelo de lenguaje, usado
como par de programación. Lo declaramos así:

- **Quién decide.** El alcance, la arquitectura y las decisiones técnicas son
  del equipo. La herramienta propone; lo que entra al repositorio lo revisó,
  lo probó y lo aprobó un integrante.
- **Quién responde.** Cada integrante puede explicar y sustentar el código de
  su frente sin la herramienta al lado.
- **Cómo se verifica.** Nada se da por bueno porque la herramienta lo diga: las
  pruebas automáticas, las verificaciones en la plataforma real y la revisión
  cruzada de la definición de terminado aplican igual.
- **Qué no sale.** No se comparten con la herramienta secretos, llaves ni
  datos personales.
- **Dónde consta.** Los commits hechos con asistencia lo dicen en su mensaje
  con la línea `Uso de IA: ...`.

---

## Desarrollo local y pruebas

Las pruebas del backend no necesitan AWS ni Docker. Usan el cliente OTLP real
contra el punto de entrada real, y un doble de InfluxDB que analiza el
protocolo de línea con las mismas reglas que el motor y ejecuta el SQL con
DataFusion, que es el motor de consulta de InfluxDB 3.

```bash
cd backend
pip install -r requirements-pruebas.txt
python -m pytest pruebas/
```

Las pruebas del cargador contra PostgreSQL se saltan salvo que haya una base
de pruebas vacía, que borran y recrean:
`ZENIT_PG_PRUEBAS=postgresql://usuario@127.0.0.1:5432/zenit_pruebas python -m pytest pruebas/`.

Las del laboratorio corren aparte, porque el laboratorio no importa nada del
backend:

```bash
cd laboratorio/servicios/inyector && python -m pytest pruebas/   # usa stress-ng si está instalado
cd laboratorio/servicios/carga && python -m pytest pruebas/
```

Para ver la interfaz con datos sin desplegar nada:

```bash
cd backend
python pruebas/falso_influx.py 18181 &
INFLUX_URL=http://127.0.0.1:18181 INFLUX_TOKEN=apiv3_prueba python -m zenit.admin crear-bases
(cd ingesta && INFLUX_URL=http://127.0.0.1:18181 INFLUX_TOKEN=apiv3_prueba PUERTO_OTLP=14317 PUERTO_ADMIN=18080 \
   RUTA_CATALOGO=../../deploy/catalogo/catalogo.json RUTA_SALIDA=/tmp/zenit PYTHONPATH=.. python servidor.py &)
INFLUX_URL=http://127.0.0.1:18181 INFLUX_TOKEN=apiv3_prueba RUTA_CATALOGO=../deploy/catalogo/catalogo.json \
   python -m uvicorn zenit.api.main:app --port 8000 &
python pruebas/nodo_simulado.py --destino 127.0.0.1:14317 --relleno-min 20 --cada 2 --escalon-tras 60 &
cd ../frontend && npm install && npm run dev       # http://localhost:5173
```

La API pide sesión en todas las rutas salvo `/api/salud` y `/api/sesion`, y
sin `PG_URL` no hay usuarios con los que entrar. Para la API real en local,
levanta un PostgreSQL, aplica `deploy/postgres/01-esquema.sql` y
`02-gobernanza.sql`, exporta `PG_URL` y crea un usuario con
`python -m zenit.gobernanza.usuarios crear <usuario> <rol>`. Para trabajar
solo en la interfaz, `npm run dev:simulado` sirve todas las rutas del
contrato sin backend. Usuarios, escenarios y decisiones de diseño: `NOTAS.md`.

El doble no verifica retención, persistencia ni rendimiento: eso se verifica
contra el motor real en la plataforma, con los comandos de la sección de
verificación.
