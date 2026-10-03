# Zenit

**Plataforma de observabilidad neutral respecto al proveedor.** Un mismo nodo observado corre idéntico en AWS, DigitalOcean, Azure o un servidor on-premise, y su telemetría llega por OpenTelemetry a un único punto de entrada que la normaliza y la asocia a un activo.

Este repositorio contiene la **épica 0**: la infraestructura mínima sobre la que se construye el resto del producto.

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
│   │   └── admin.py             crear bases, verificar, huecos (H-005)
│   ├── pruebas/                 pytest, doble de InfluxDB, nodo simulado
│   └── Dockerfile.api
├── frontend/                    vista de serie (H-007), React + uPlot
├── deploy/                      la plataforma (H-001, H-008)
│   ├── compose.yml              borde, ingesta, influxdb, api, web
│   ├── nginx.conf               TLS en 4317 (gRPC) y 443 (interfaz)
│   ├── catalogo/catalogo.json   identificador del nodo → activo
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
| `scripts/aws/abrir-interfaz.sh` | Recurrente | Cada IP del equipo que vaya a usar la interfaz |
| `scripts/influx/preparar-token.sh` | Uso único | Antes del primer arranque de la plataforma |
| `scripts/tls/emitir.sh` | Ocasional | Solo si cambia la IP elástica. Exige `tls/ca.key`; `NUEVA_CA=1` crea una CA nueva a propósito |
| `scripts/publicar.sh <versión>` | Recurrente | Cada versión nueva de la aplicación |

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
| H-006 | ☑ Endpoint en `/api/docs` · ☐ revisión cruzada: ningún SQL fuera de `repositorio/influx.py` y `admin.py` |
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

El doble no verifica retención, persistencia ni rendimiento: eso se verifica
contra el motor real en la plataforma, con los comandos de la sección de
verificación.
