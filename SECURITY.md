# Seguridad

## Modelo de amenazas

duberr es una herramienta de **red doméstica de confianza**. Su web, con las acciones de sus botones, lanza `ffmpeg` y
`mkvmerge` sobre los archivos de tu biblioteca y habla con tus Sonarr/Radarr usando sus claves de API. Por eso:

- **No la expongas a internet** (ni con redirección de puertos, ni detrás de un proxy público sin autenticación).
- Alguien que llegue a la web puede añadir/quitar series, cambiar ajustes y provocar mezclas y sustituciones de archivos
  (siempre reversibles desde la papelera mientras no caduque, pero con coste de CPU y disco).
- Alguien con la clave de API de Sonarr/Radarr o con el token del webhook ya tiene acceso a esas aplicaciones: trata
  `.env`, `DUBERR_PASSWORD`, `DUBERR_WEBHOOK_TOKEN` y `<datos>/webhook-token` como secretos.

### Lo que duberr ya hace

- **Contraseña opcional** (`DUBERR_PASSWORD`, HTTP Basic, comparación en tiempo constante, con un pequeño retardo tras
  cada fallo). `/salud` y el webhook (que lleva su propio token) quedan exentos.
- **Anti-CSRF:** todo `POST` exige que `Origin` (o `Referer`) coincida con el `Host` de la petición; si no vienen,
  solo se acepta con un token válido. Si un proxy cambia el `Host`, añade el nombre en `DUBERR_ALLOWED_ORIGINS`.
- **Webhook autenticado** con `DUBERR_WEBHOOK_TOKEN` (o uno aleatorio de 192 bits guardado con permisos `0600`),
  aceptado en `?token=` o en la cabecera `X-Api-Key`, comparado en tiempo constante.
- **Ninguna respuesta devuelve claves** de Sonarr/Radarr/ntfy; los mensajes de error pasan por un filtro que las tapa.
  La página de Ajustes sí muestra el token del webhook (hace falta para configurarlo): protégela con contraseña.
- Escucha configurable (`DUBERR_BIND`), límite de tamaño del cuerpo de las peticiones, `X-Frame-Options: DENY`,
  `X-Content-Type-Options: nosniff` y `Referrer-Policy: same-origin`.
- Contenedor **sin root** (usuario 1000 por defecto, o el que pongas en `user:`).
- Sobre los archivos: nunca modifica uno existente (escribe un archivo nuevo y lo renombra), respeta las carpetas
  protegidas y deja el archivo sustituido en una papelera con enlace duro.

### Lo que no hace

- No cifra el tráfico: usa HTTPS en un proxy inverso o una VPN (Tailscale, WireGuard) si sales de tu LAN.
- No limita intentos de contraseña más allá del retardo, ni tiene usuarios ni roles: es una sola contraseña.
- La contraseña viaja en HTTP Basic: sin TLS, es legible en la red.
- El `GET` de la vista previa y las imágenes dependen solo de la contraseña (no de `Origin`).

## Recomendaciones

1. Pon siempre `DUBERR_PASSWORD`, o publica el puerto solo en `127.0.0.1` / una IP de VPN (`ports: "127.0.0.1:3005:3005"`).
2. Si la pones detrás de un proxy, que el proxy autentique y mantenga el `Host`.
3. Monta las carpetas que no deban tocarse como **solo lectura** (`:ro`) y decláralas en `DUBERR_PROTECTED_DIRS`.
4. Usa un `user:` sin privilegios que tenga permisos de escritura en la biblioteca y en la carpeta de datos, nada más.
5. Mantén actualizada la imagen base (reconstruye de vez en cuando con `docker compose build --pull`).

## Reportar una vulnerabilidad

Por favor, **en privado**: usa «Report a vulnerability» en la pestaña *Security* del repositorio de GitHub
(Security advisories), o escribe al titular a través de su perfil de GitHub (`m0uwastaken`). No abras un *issue*
público con los detalles. Intentaré responder en unos días y publicar el arreglo antes de dar detalles.

Versiones con soporte: la última publicada.
