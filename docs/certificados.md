# Certificados del canal de telemetria (H-012)

Versión 1 · 2026-10-09 · RF-ING-08 · RNF-SEG-02 · RNF-SEG-07 · ADR-017

El 4317 es la única puerta de la plataforma hacia internet. Se protege con
autenticación mutua: el agente verifica el certificado del borde y el borde
verifica el del agente, los dos contra la misma autoridad (CA) propia.

## Qué hay y dónde

| Pieza | Dónde vive | Se versiona | Vigencia |
|---|---|---|---|
| Clave de la CA (`tls/ca.key`) | Solo en el PC de Juan José | **Nunca** | 10 años |
| Certificado de la CA (`ca.crt`) | `laboratorio/agente/ca.crt`, y de ahí lo monta el borde | Sí, es público | 10 años |
| Certificado del borde (`servidor.crt` y `.key`) | `deploy/tls/` en la plataforma | No | 825 días |
| Certificado de cada agente (`cliente.crt` y `.key`) | `laboratorio/agente/cliente/` en cada nodo | No | 400 días |

El borde rechaza con 403 (gRPC `PERMISSION_DENIED`) toda conexión sin
certificado de cliente válido, y con 400 la que trae un certificado de otra CA,
vencido o mal formado. Cada rechazo queda registrado con su origen, en JSON:

```bash
docker compose logs borde | grep '"estado":4'
docker compose exec borde tail -n 20 /var/log/zenit/rechazos-otlp.log
```

## Emitir el certificado de un nodo

En tu PC, en la carpeta del repositorio donde está `tls/ca.key`:

```bash
bash scripts/tls/emitir-cliente.sh zenit-nodo-do
```

El nombre es el `ZENIT_NODO` del nodo: queda como CN del certificado, el
borde lo usa como identidad del emisor para el límite de tasa, y la ingesta
cuenta en `emisor_distinto` cuando un certificado manda telemetría con otro
`host.name`. El script imprime la fila para la tabla de abajo y los dos `scp`.

En el nodo, después de copiar los dos archivos:

```bash
cd ~/zenit/laboratorio
nano entornos/<entorno>.env      # RUTA_CERT_CLIENTE y RUTA_CLAVE_CLIENTE, como en el .env.example
docker compose --env-file entornos/<entorno>.env --profile observado up -d agente
```

Reiniciar el agente no pierde nada: su cola y la posición de lectura de los
archivos están en disco. La campaña del inyector no se toca.

## Pasar a modo estricto

`MTLS_MODO` en `deploy/.env`:

- `transicion`: un agente sin certificado todavía entra (con el token); uno
  con certificado inválido no. Es el modo para repartir los certificados.
- `estricto`: sin certificado válido no se entra. Es lo que pide RNF-SEG-07.

```bash
cd ~/zenit/deploy && nano .env    # MTLS_MODO=estricto
docker compose up -d borde
```

## Revocar un certificado

Si la clave de un nodo se compromete, su número de serie va a
`deploy/borde/revocados.map` (la tabla de abajo lo tiene) y se recarga el
borde. Después se emite uno nuevo para el nodo.

```bash
echo '"<SERIE>" 1;   # zenit-nodo-do, <fecha> <motivo>' >> deploy/borde/revocados.map
docker compose exec borde nginx -s reload
```

## Límite de tasa por emisor

50 peticiones por segundo por certificado, con ráfagas de hasta 100
(`deploy/nginx.conf`). Un nodo normal hace unas pocas por segundo. Al pasar el
límite el borde responde 429, que el agente trata como "reintentar más tarde"
(`UNAVAILABLE`): se frena y no pierde datos. Los otros emisores y la salud de
la ingesta no se enteran.

## Vencimientos

Un certificado vencido corta la ingesta del nodo y se parece a una caída de
red. Esta tabla es el lugar donde se mira.

| Nodo | Serie | Emitido | Vence | Estado |
|---|---|---|---|---|
| (borde) zenit-ingesta | ver `openssl x509 -in deploy/tls/servidor.crt -noout -serial -enddate` | | | vigente |
| zenit-nodo-azure | 6B41E4AC0F13AC39C10C0BC6D0C96021093CB503 | 2026-10-16 | 2027-11-20 | vigente |
