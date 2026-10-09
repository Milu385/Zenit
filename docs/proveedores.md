# Tres proveedores, tres formas de decir lo mismo

Versión 1 · 2026-10-09 · H-009b, H-009c, H-009e, H-013, H-014, H-015 · ADR-008, ADR-010, ADR-012

Este documento cumple dos criterios a la vez. El de H-013 a H-015: la credencial
de descubrimiento es de solo lectura y eso se puede demostrar. Y el de H-014 y
H-015: queda escrito en qué difieren los proveedores en descubrimiento,
credenciales y facturación. Ese registro es la evidencia de primera mano de la
fragmentación que la propuesta afirma, y se escribió mientras se construía.

El código está en `backend/zenit/descubrimiento/`. Los tres módulos tienen la
misma forma (`listar() -> list[Recurso]`) y ninguno escribe en el catálogo: eso
lo hace `zenit.catalogo.reconciliar`, igual para todos.

---

## 1. La comparación

| | DigitalOcean | Proxmox VE (on-premise) | AWS |
|---|---|---|---|
| **Qué se consulta** | `GET /v2/droplets?tag_name=zenit-nodo` | `GET /api2/json/cluster/resources?type=vm` | `ec2:DescribeInstances` con filtro `tag:zenit:rol=nodo` |
| **Llamadas por pasada** | Una, más una por página de 200 | Una para todo el clúster | Una por región, más una por página |
| **Paginación** | URL completa en `links.pages.next` | No hay | `NextToken` (el paginador de boto3) |
| **Identificador nativo** | Número global del droplet (`droplet/412345678`) | `qemu/105` o `lxc/110`; el número se reutiliza si se borra la máquina | `i-0abc...`, único dentro de la cuenta y la región |
| **Nombre** | Atributo propio del droplet | Atributo propio, sin obligación de ser único | No existe: es la etiqueta `Name`, opcional |
| **Etiquetas** | Lista de textos | Un texto separado por `;` | Pares clave y valor |
| **Agrupación** | Proyecto, plana | Nodo físico dentro del clúster; pools | Cuenta, región, VPC |
| **Estados que no son "borrado"** | `off` | `stopped` | `stopped`; el laboratorio de AWS detiene las instancias al cerrar la sesión |
| **Credencial de solo lectura** | Token con alcance de lectura | Token de API de un usuario con rol `PVEAuditor` | Rol o usuario IAM con `AmazonEC2ReadOnlyAccess`; **no disponible en Learner Lab** |
| **Vida de la credencial** | La que se elija al crearla | La que se elija al crearla | Cuatro horas (sesión del laboratorio) |
| **TLS de la API** | CA pública | CA propia del clúster (`/etc/pve/pve-root-ca.pem`) | CA pública, y cada petición firmada (SigV4) |
| **Cómo la alcanza la plataforma** | Internet | No es alcanzable: está detrás del NAT de la red local | Internet |
| **Facturación** | API de facturas (`/v2/customers/my/invoices`), con CSV por recurso; exige alcance de facturación | No hay: el costo es hardware propio y energía, se asigna a mano | Cost Explorer o el reporte de costo y uso (CUR) en S3; **Learner Lab no da acceso** |

Lo que la tabla dice en una línea: **el mismo hecho, "esta máquina existe y se
llama así", se pide de tres maneras, se identifica de tres maneras y se protege
de tres maneras.** Sin el catálogo canónico, cada consulta de Zenit tendría que
saber de cuál de los tres viene el dato.

Tres diferencias que costaron trabajo y que vale la pena contar en la sustentación:

1. **El nombre no es una identidad en ningún lado.** En DigitalOcean y Proxmox
   puede repetirse; en AWS ni siquiera es un atributo. Por eso el catálogo
   reconoce un recurso primero por su identificador nativo, y por nombre solo
   para adoptar un activo admitido a mano (`zenit/catalogo.py`, `reconciliar`).
2. **"No aparece" no significa "no existe".** AWS detiene las instancias del
   laboratorio cada cuatro horas y sus credenciales vencen con la sesión. Si
   una pasada fallida retirara activos, el inventario de AWS se vaciaría cada
   tarde. La regla: solo una pasada **exitosa** cuenta, y se retira tras tres
   seguidas sin ver el activo.
3. **El on-premise no se alcanza desde la nube.** Los dos proveedores públicos
   exponen su API en internet; el Proxmox no. La plataforma necesita un camino
   de entrada (sección 3), que en la nube pública no hace falta.

---

## 2. DigitalOcean

**Crear el token.** En el panel, *API → Tokens → Generate New Token*, con
alcance *Read Only* (o uno personalizado con `droplet:read`) y una expiración
que pase el 30 de noviembre. Va en `deploy/.env` como `DO_TOKEN`.

**Demostrar que es de solo lectura.** Una escritura inofensiva, crear una
etiqueta, tiene que responder 403:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://api.digitalocean.com/v2/tags \
  -H "Authorization: Bearer $DO_TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"zenit-prueba-escritura"}'
# 403
```

Si respondiera 201, el token no es de solo lectura: se revoca, se borra la
etiqueta y se crea otro.

**Qué descubre.** Los droplets con la etiqueta `zenit-nodo`, que es la que pone
`scripts/do/02-nodo.sh`. La plataforma lleva solo `zenit`, así que no aparece
como activo observado.

---

## 3. Proxmox VE

**Crear el usuario y el token**, en el Proxmox, como root:

```bash
pveum user add zenit@pve --comment "Zenit: descubrimiento, solo lectura"
pveum acl modify / --users zenit@pve --roles PVEAuditor
pveum user token add zenit@pve descubrimiento --privsep 0 --expire $(date -d 2026-12-31 +%s)
```

`--privsep 0` hace que el token tenga exactamente los permisos del usuario, que
son los de `PVEAuditor`: solo privilegios `.Audit`. La salida muestra el
secreto una sola vez. En `deploy/.env`:
`PROXMOX_TOKEN=zenit@pve!descubrimiento=<secreto>`.

**Marcar la máquina virtual.** El descubrimiento solo lista las que tienen la
etiqueta `zenit-nodo` (en la interfaz, *Opciones → Etiquetas*). El nombre de
la VM conviene que sea el `ZENIT_NODO` (`zenit-nodo-onprem`); si no lo es, el
activo se crea igual y la telemetría se resuelve por la clave que se admita a
mano.

**Demostrar que es de solo lectura.** Crear un pool tiene que fallar:

```bash
curl -s --cacert pve-root-ca.pem -X POST https://<proxmox>:8006/api2/json/pools \
  -H "Authorization: PVEAPIToken=zenit@pve!descubrimiento=<secreto>" -d poolid=zenit-prueba
# {"data":null,"message":"Permission check failed (/pool/zenit-prueba, Pool.Allocate)\n"}
pveum user permissions zenit@pve      # solo .Audit
```

**TLS.** El certificado de la API lo firma la CA propia del clúster. Se copia
`/etc/pve/pve-root-ca.pem` a `deploy/secretos/descubrimiento/proxmox-ca.pem`
en la plataforma, y `PROXMOX_CA=/run/zenit/descubrimiento/proxmox-ca.pem`.
Nunca se desactiva la verificación.

**Cómo llega la plataforma a la API.** El Proxmox está detrás del NAT de la red
local y la plataforma está en internet. Dos caminos:

- **Redirección de puerto** en el enrutador local, del 8006 al Proxmox, abierta
  solo a la IP reservada de la plataforma. Es lo más simple si se administra el
  enrutador. `PROXMOX_URL=https://<ip-publica-local>:8006`.
- **Túnel SSH inverso desde la VM on-premise**, si no se puede tocar el
  enrutador. La VM abre la conexión hacia la plataforma, como hace el agente, y
  la plataforma ve la API del Proxmox en un puerto de su red interna de Docker.

  En la plataforma, una llave que solo sirve para ese túnel. En
  `/root/.ssh/authorized_keys`:

  ```
  restrict,port-forwarding,permitlisten="172.17.0.1:18006",command="/bin/false" ssh-ed25519 AAAA... tunel-proxmox
  ```

  y en `/etc/ssh/sshd_config.d/zenit.conf`, `GatewayPorts clientspecified`
  (luego `systemctl reload ssh`). El cortafuegos de DigitalOcean no abre el
  18006, así que solo lo alcanza lo que corre en la plataforma.

  En la VM on-premise, como servicio:

  ```bash
  sudo apt-get install -y autossh
  sudo tee /etc/systemd/system/zenit-tunel-proxmox.service >/dev/null <<'EOF'
  [Unit]
  Description=Tunel de la API del Proxmox hacia la plataforma Zenit
  After=network-online.target
  [Service]
  Environment=AUTOSSH_GATETIME=0
  ExecStart=/usr/bin/autossh -M 0 -N -o ServerAliveInterval=30 -o ExitOnForwardFailure=yes \
    -i /root/.ssh/tunel-proxmox -R 172.17.0.1:18006:<ip-del-proxmox>:8006 root@<ip-reservada>
  Restart=always
  [Install]
  WantedBy=multi-user.target
  EOF
  sudo systemctl enable --now zenit-tunel-proxmox
  ```

  En `deploy/.env`: `PROXMOX_URL=https://host.docker.internal:18006` y
  `PROXMOX_NOMBRE_TLS=<nombre del nodo Proxmox>` (el certificado se emitió
  para ese nombre, no para `host.docker.internal`).

  Este túnel lleva solo la consulta de descubrimiento. La telemetría del nodo
  sigue yendo por el 4317 con mTLS y sin túnel, como pide H-012.

---

## 4. AWS

**Lo correcto** sería un rol o un usuario de IAM con la política administrada
`AmazonEC2ReadOnlyAccess`. **AWS Academy Learner Lab no permite crear roles ni
usuarios**, así que se usan las credenciales temporales de la sesión (panel
*AWS Details*), que no son de solo lectura y vencen a las cuatro horas. Es una
limitación del entorno, declarada, no una decisión.

Lo que sí se puede demostrar es que **el código solo lee**: la única llamada a
AWS de todo el repositorio es `describe_instances`.

```bash
grep -rn "boto3\|\.client(\|describe_\|run_\|terminate_\|create_\|delete_" backend/zenit/descubrimiento/aws.py
```

**Usarlo durante una sesión del laboratorio.** Las credenciales van en
`deploy/secretos/descubrimiento/aws-credenciales`, con el formato del panel:

```
[default]
aws_access_key_id=...
aws_secret_access_key=...
aws_session_token=...
```

y en `deploy/.env`, `DESCUBRIR_AWS=1`, con `docker compose up -d descubridor`.
Al cerrar la sesión, `DESCUBRIR_AWS=0`. Con las credenciales vencidas cada
pasada queda en `error` y no retira nada.

**Qué descubre.** Las instancias con la etiqueta `zenit:rol=nodo` que pone
`scripts/aws/03-nodo.sh`, detenidas incluidas. La de la plataforma lleva
`zenit:rol=plataforma` y no aparece.

---

## 5. Azure: desviación

El ADR-012 compromete cuatro entornos observados. En este semestre **no se
implementa Azure**: ni el nodo (H-014) ni su descubrimiento (H-009d). Se declara
como desviación en el informe final, por decisión del equipo del 9 de octubre.

Lo que haría falta, para que quede escrito: una entidad de servicio con los
roles `Reader` y `Monitoring Reader` sobre el grupo de recursos (ADR-010), una
consulta a Resource Graph o a `Microsoft.Compute/virtualMachines` por
suscripción y grupo de recursos (un cuarto modelo de identidad: el id del
recurso es una ruta completa), y un módulo `descubrimiento/azure.py` con la
misma forma que los otros tres. Nada del catálogo ni de la ingesta cambia.

El carácter híbrido del proyecto no depende de Azure: lo sostiene el entorno
on-premise (ADR-012, precisión del 18 de septiembre).
