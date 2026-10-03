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
RETENCION: zenit_raw 7 dias, zenit_1m 30 dias, zenit_1h 180 dias
INTERFAZ: 443 abierto solo a las IP del equipo
