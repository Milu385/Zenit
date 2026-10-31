# Pruebas de seguridad de la plataforma (31 de octubre de 2026)

Resultados de las pruebas de la sección 4 del último lote:
- **Objetivo:** solo `20.3.217.87`, desde la IP de Daniel admitida en el 443.
- **Límites:** sin denegación de servicio, sin escaneo activo y nada contra Kaderno ni contra terceros.
- **Autorización:** la de `textos-legales.md`, sección 3. Daniel dio la indicación de correr las pruebas; **falta anexar la autorización firmada** por Juan José. Las capturas y salidas completas van en `seguridad/` del registro de evidencias.

| # | Prueba | Cómo | Resultado | ¿Pasa? | Qué se hizo |
|---|---|---|---|---|---|
| 1 | Matriz de permisos con las rutas nuevas (P-GOB-01) | `scripts/seguridad/probar_permisos.py` con un usuario de cada rol | **Pendiente: la corre Daniel**, porque pide las claves reales de los cuatro usuarios | — | — |
| 2 | TLS del 443 | `docker run --rm drwetter/testssl.sh --quiet 20.3.217.87:443`, a las 16:26 UTC | Solo TLS 1.2 y 1.3. SSLv2, SSLv3, TLS 1.0 y 1.1 no se ofrecen. Sin cifrados NULL, anónimos, de exportación, RC4, DES ni 3DES. No vulnerable a Heartbleed, ROBOT, POODLE, SWEET32, LOGJAM ni BEAST. **Se ofrecían los cifrados CBC obsoletos de TLS 1.2** (LUCKY13 "potencialmente vulnerable") y no había orden de cifrados del servidor. Nota global: B, limitada por la cadena de confianza | Con observaciones | **Corregido en esta rama:** en `deploy/nginx.conf` (443), TLS 1.2 solo con cifrados AEAD con ECDHE y `ssl_prefer_server_ciphers on`. Comprobado con nginx 1.27 en local: CBC rechazado, GCM aceptado, TLS 1.3 sin cambios. **Declarado:** la "cadena incompleta" y la falta de OCSP/CRL son esperadas, porque el certificado es de la CA propia del proyecto. HSTS no se pone porque los navegadores lo ignoran para una IP (decisión ya anotada en `nginx.conf`) |
| 4 | Fuerza bruta en el inicio de sesión | 7 intentos con un usuario que no existe (`fb-prueba`) a `/api/sesion` a las 16:26:02 UTC; se repitió a las 16:31:33 UTC | 401, 401, 401, 401, 401, 429, 429. Al repetir, más de 5 min después: 401, 401 | Sí | — |
| 5 | Escaneo pasivo de la interfaz (OWASP ZAP baseline) | `zap-baseline.py -t https://20.3.217.87 -I` (sin escaneo activo) | 0 fallos y 62 reglas sin hallazgo. **Ninguna alerta alta.** Ya no sale "CSP header not set". Detalle de las alertas en la tabla de abajo | Sí | Ver la tabla de abajo |
| 6 | Dependencias vulnerables, interfaz | `npm audit --omit=dev` en `frontend/` | 0 vulnerabilidades en producción. Sin `--omit=dev` aparecen esbuild (moderada) y vite (alta), que son del servidor de desarrollo y no van en la imagen | Sí | Declarado: la interfaz que se despliega es estática y la sirve nginx |
| 6 | Dependencias vulnerables, API | `pip-audit` dentro del contenedor `api` | **Pendiente: se corre en Azure** | — | — |
| 8 | Secretos en el historial | `gitleaks detect` sobre todo el historial (99 commits) | Dos hallazgos, los dos datos falsos de las pruebas de la redacción de H-041 (un JWT de juguete y `api_key: 9f8e7d6c5b4a`) | Sí, con los dos en `.gitleaksignore`: "no leaks found" | Documentados como falsos positivos (PR #29). Juan José la repite antes de `v1.0` |

Las pruebas 3 (puerto 4317 con un certificado de otra CA) y 7 (inyección de instrucciones y XSS por registros) las hace Juan José desde el nodo.

## Prueba 5: alertas medias y bajas de ZAP

| Riesgo | Alerta | Decisión |
|---|---|---|
| Media | CSP: `style-src 'unsafe-inline'` | **Se acepta.** Las gráficas (uPlot) y algunas barras usan atributos `style` generados en tiempo de ejecución. `script-src` sigue siendo solo `'self'`, que es lo que protege de XSS. Está anotado en `nginx.conf` |
| Baja | Falta Cross-Origin-Opener-Policy | **Corregido:** `same-origin` |
| Baja | Falta Cross-Origin-Resource-Policy | **Corregido:** `same-origin` |
| Baja | Falta Cross-Origin-Embedder-Policy | **Se acepta:** todos los recursos son del mismo origen y no se usan funciones que lo exijan |
| Baja | Falta Strict-Transport-Security | **Se acepta:** los navegadores ignoran HSTS para una dirección IP. Si la plataforma tuviera nombre de dominio, se agregaría |
| Informativa | Aplicación web moderna; directivas de caché; contenido almacenable en caché | Sin acción: `index.html` va con `no-cache` y los recursos con huella en el nombre son inmutables (`frontend/nginx-web.conf`) |

**Para verificar la corrección en Azure:** después de desplegar esta rama, repetir las pruebas 2 y 5.
- En testssl, "Obsoleted CBC ciphers" debe salir como `not offered` y LUCKY13 como `not vulnerable`.
- En ZAP ya no deben salir las alertas de COOP y CORP.
