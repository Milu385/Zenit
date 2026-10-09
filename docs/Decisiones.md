Decisiones relacionadas a la epica-0 AWS:
REGION: us-east-1
INSTANCIAS: t3.small (x2)
SO: Ubuntu Server 24.04 LTS
REGISTRO DE IMAGENES: GHCR (ghcr.io)
ACCESO A LAS INTANNCIAS: SSM
PREFIJO DE NOMBRES: zenit-

DISCO: 30 GB gp3 en la plataforma, 20 GB gp3 en el nodo
IP PUBLICA: elastica en la plataforma y en los nodos EC2
CA: una sola, creada el 2026-10-02; custodio Juan Jose Tamayo; respaldo cifrado de ca.key: pendiente de definir donde
SERVICE_NAME DE LAS METRICAS DEL ANFITRION: host
RETENCION: zenit_raw 30 dias durante la campana del laboratorio (7 en el diseno), zenit_1m 30 dias, zenit_1h 180 dias
INTERFAZ: 443 abierto solo a las IP del equipo
ANFITRION DE LA EPICA 2: plataforma en un droplet de DigitalOcean, nodo on-premise en el Proxmox y nodo de nube en un droplet
VERDAD DE REFERENCIA: viaja del nodo a la plataforma como registro OTLP; el inyector no tiene red ni credenciales de la plataforma
TRAZAS: solo alimentan el conector spanmetrics hasta H-017

Decisiones de la epica 1 (2026-10-09):
ENTORNOS OBSERVADOS: Proxmox y DigitalOcean siempre; AWS solo durante las sesiones del Learner Lab. Azure no se implementa: desviacion declarada (docs/proveedores.md)
IDENTIFICADOR CANONICO: se asigna una vez y se guarda (activo-<proveedor>-<nombre>); no se deriva del id nativo. Los ids de las epicas 0 y 2 se conservan
RECONOCIMIENTO: primero por id nativo; por nombre solo para adoptar un activo admitido a mano o recreado en el mismo proveedor
RETIRO: tras 3 pasadas exitosas seguidas sin ver el activo; una pasada con error no cuenta. Retirar es marcar, nunca borrar
DESCUBRIMIENTO: cada 15 minutos (servicio descubridor); el catalogo de la ingesta se recarga cada 30 s
HUERFANAS: se reescriben con el activo cuando su clave entra al catalogo; en InfluxDB las filas sin_resolver quedan (Core no borra)
REGISTROS Y TRAZAS: ClickHouse 24.8, escrito desde la ingesta por lotes, 7 dias de retencion, esquema en deploy/clickhouse
PLATAFORMA: droplet de 8 GB (s-4vcpu-8gb); ClickHouse limitado a 2 GB y la ingesta a 768 MB
RESOLUCION: hasta 24 h crudo, hasta 30 dias resumen por minuto, hasta 180 dias resumen por hora; unos 1500 puntos por grafica
CONSULTAS LARGAS: en tramos de 1 dia (zenit_1m) y 10 dias (zenit_1h) por el limite de 432 archivos de Core
AGREGACION: cada 60 s; minuto completo 30 s despues de terminar; rehace los 2 ultimos minutos; cada hora se escribe una vez
MTLS: CA propia, certificado de cliente por nodo (CN = ZENIT_NODO, 400 dias); modo transicion para repartirlos y estricto despues
LIMITE POR EMISOR: 50 peticiones por segundo por certificado, rafaga de 100; el exceso recibe 429 y el agente reintenta
SALUD DE FUENTES: degradada con mas de 5 minutos sin recibir metricas, por hora de recepcion (no del dato)
H-022: no se ejecuta la prueba comparativa de motores; desviacion declarada (decisiones de cierre del 2 de octubre)
