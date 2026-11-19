# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/); versionado [SemVer](https://semver.org/lang/es/).

## [0.1.0] - 2026-11-19

Primera versión pública.

### Añadido
- Integración con Sonarr y Radarr (API v3): perfiles «duberr · castellano» y «duberr · calidad» creados a partir de
  un perfil base configurable (`DUBERR_BASE_PROFILE`), búsqueda, cola e importación manual forzada.
- Medición de la sincronía del castellano (desfase, velocidad, deriva y cortes), mezcla sin recodificar con
  `mkvmerge`, comprobación del resultado y sustitución por archivo nuevo + renombrado.
- Papelera con enlaces duros y Deshacer/Rehacer; vista previa de audio; carpeta de entrada («solo añadir castellano»).
- Webhook de Sonarr/Radarr (`POST /api/webhook/sonarr` y `/api/webhook/radarr`) autenticado con token
  (`DUBERR_WEBHOOK_TOKEN`, o generado y visible en Ajustes) para reaccionar al instante a importaciones, renombrados y altas.
- Mapeo de rutas `DUBERR_PATH_MAP` (`remoto=>local`) aplicado en ambos sentidos a todas las rutas de Sonarr/Radarr.
- Idioma «Spanish» obtenido de la API de idiomas de cada aplicación (el id 3 solo como último recurso).
- Interfaz estable de monitorización: `/salud`, `/api/estado` y `/api/vivo`.
- Seguridad: contraseña opcional (`DUBERR_PASSWORD`), comprobación de `Origin`/`Referer` en los `POST`,
  `DUBERR_BIND`, `DUBERR_ALLOWED_ORIGINS`, límite de tamaño de petición y cabeceras de seguridad.
- `Dockerfile` autocontenido (Alpine 3.22 fijada, usuario sin root, `HEALTHCHECK`), `docker-compose.example.yml`,
  `.env.example`, `SECURITY.md` y `docs/ARQUITECTURA.md`.

### Cambiado
- Variables de entorno con prefijo: `DUBERR_DATA`, `DUBERR_PORT`, `DUBERR_MEDIA`, `DUBERR_INTERVAL`. Los nombres sin
  prefijo (`DATOS`, `PUERTO`, `MEDIA`, `INTERVALO`) siguen valiendo como respaldo.
- Las carpetas de solo lectura se declaran con `DUBERR_PROTECTED_DIRS` (lista separada por `:`); no hay ninguna ruta
  de este tipo por defecto.
- Los avisos de error de Sonarr/Radarr con el servidor caído ya no vuelcan una traza en cada pasada.

[0.1.0]: https://github.com/m0uwastaken/duberr/releases/tag/v0.1.0
