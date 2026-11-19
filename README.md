# duberr

Servicio para Sonarr y Radarr que **conserva el audio en castellano** de cualquier versión que lo tenga y lo
**mezcla, sin recodificar, con la mejor imagen disponible** (hasta Bluray 1080p). Pensado para quien monta su propia
biblioteca con la suite *arr y quiere «la mejor calidad con el audio en castellano», sin hacerlo episodio a episodio
a mano.

> **Estado: 0.1.0.** Funciona en uso real, pero es joven. Lee las [limitaciones](#limitaciones) y haz copia de tu
> biblioteca antes de dejarlo trabajar sobre ella (aunque nunca modifica un archivo existente y todo se puede deshacer).

## Qué hace

Para cada serie (temporadas elegidas) o película que añades desde su web:

1. **Castellano.** Si a algún episodio le falta el castellano, pone en Sonarr/Radarr el perfil «duberr · castellano»
   (cualquier calidad, puntúa solo el formato personalizado de castellano) y busca.
2. **Guardar.** Guarda **sin recodificar** la pista en castellano de *todos* los episodios de la serie en su propia
   carpeta (`<datos>/audio/<Serie>/SxxEyy.mka`), para no perderla nunca aunque luego cambie el archivo.
3. **Calidad.** Con el castellano a salvo, pasa a «duberr · calidad» (mejor calidad hasta Bluray 1080p, cualquier
   idioma) y busca las temporadas elegidas. Cada episodio va por su cuenta.
4. **Mezclar.** Cada versión nueva sin castellano recibe la pista guardada: se **mide** dónde encaja, se monta, se
   mezcla en un archivo nuevo, se **comprueba** y solo entonces sustituye al anterior.

Lo que lo hace fiable:

- **Mide antes de sustituir.** Compara el castellano con la pista de referencia del vídeo (por defecto, el idioma
  original) por bandas de frecuencia; detecta desfase, velocidad (25 fps frente a 23,976) y deriva, y permite
  cortes (escenas quitadas, anuncios, aperturas distintas). Si no hay una sincronía clara, **no mezcla** y te lo dice.
- **Partes sin castellano:** se rellenan con el audio original, se recortan del vídeo o se dejan en silencio, a tu
  elección.
- **Nunca modifica un archivo existente.** Escribe un archivo nuevo en la misma carpeta y lo renombra encima. Los
  archivos que tengan otros enlaces duros (`DUBERR_PROTECTED_DIRS`) ni se tocan.
- **Papelera con Deshacer.** El archivo sustituido se guarda como enlace duro (no ocupa espacio extra) N días y se
  restaura desde la ficha del episodio. Vista previa de audio en el navegador (castellano, original o los dos, uno por
  oído, para oír el desfase).
- **Carpeta de entrada.** Si ya tienes el castellano en otro sitio, déjalo en
  `<datos>/entrada/<Título tal cual en Sonarr/Radarr>/` y duberr saca la pista («solo añadir castellano»).
- **Avisos** opcionales por [ntfy](https://ntfy.sh).

Cómo funciona por dentro: [docs/ARQUITECTURA.md](docs/ARQUITECTURA.md).

## Requisitos

- Sonarr y/o Radarr con la **API v3** (desarrollado contra Sonarr v4 y Radarr v3-v5; **no probado con Sonarr v3**).
- Un **formato personalizado** de Sonarr/Radarr que marque el castellano (por defecto se llama `Castellano`; cámbialo
  con `DUBERR_CAST_FORMAT`). Sin él, la fase «castellano» no aceptará ninguna descarga; duberr lo avisa en el
  registro. Los formatos personalizados existen en Sonarr v4 y en Radarr; no en Sonarr v3.
- Docker (la imagen lleva todo). Sin Docker: Python 3 reciente (la imagen usa el de Alpine 3.22, 3.12), `numpy`, `ffmpeg`, `ffprobe` y `mkvmerge` en el `PATH`.
- La biblioteca montada en la **misma ruta** que usan Sonarr y Radarr (o un mapeo de rutas, ver más abajo), y que
  los medios y la papelera estén en el mismo sistema de archivos (para los enlaces duros).

### Dependencias del sistema (Alpine, lo que instala el Dockerfile)

| Paquete `apk`     | Para qué                                                       |
|-------------------|----------------------------------------------------------------|
| `python3`         | el servicio (solo biblioteca estándar)                         |
| `py3-numpy`       | medición de la sincronía                                       |
| `ffmpeg`          | decodificar audio, recodificar si hace falta, vista previa     |
| `mkvtoolnix`      | `mkvmerge`: leer pistas y mezclar sin recodificar              |
| `tzdata`          | hora local en el registro                                      |
| `ca-certificates` | HTTPS hacia tus *arr / ntfy si los pones detrás de TLS         |

En Debian/Ubuntu: `apt install python3 python3-numpy ffmpeg mkvtoolnix`.

## Inicio rápido (Docker Compose)

```bash
git clone https://github.com/m0uwastaken/duberr.git && cd duberr
cp docker-compose.example.yml docker-compose.yml
cp .env.example .env            # rellena SONARR_KEY, RADARR_KEY y, mejor, DUBERR_PASSWORD
# edita docker-compose.yml: la red de tus *arr, el uid:gid y el volumen de datos (la MISMA ruta que usan ellos)
docker compose up -d --build
```

Abre `http://localhost:3005`, ve a **Añadir**, elige una serie y sus temporadas. En la cabecera, un punto indica
cuándo está trabajando. En **Ajustes** están el token del webhook y el estado de la integración.

Los volúmenes importan:

| Volumen                       | Contenido                                                                          |
|-------------------------------|------------------------------------------------------------------------------------|
| `./duberr-data:/duberr`       | configuración, audio guardado, estado, caché. **Haz copia de esta carpeta.**       |
| `/ruta/a/tus/datos:/data`     | tu biblioteca, igual que en Sonarr/Radarr (convención TRaSH: `/data/media/...`)    |

## Variables de entorno

Todas opcionales salvo las de Sonarr/Radarr. Los nombres antiguos sin prefijo (`DATOS`, `PUERTO`, `MEDIA`,
`INTERVALO`) siguen funcionando como respaldo.

| Variable | Por defecto | Qué hace |
|---|---|---|
| `SONARR_URL`, `SONARR_KEY` | `http://sonarr:8989`, — | Dirección (vista desde el contenedor) y clave de API de Sonarr |
| `RADARR_URL`, `RADARR_KEY` | `http://radarr:7878`, — | Ídem para Radarr |
| `DUBERR_DATA` | `/duberr` | Carpeta de datos (config, audio guardado, estado, caché, token del webhook) |
| `DUBERR_MEDIA` | `/data/media` | Raíz de la biblioteca tal como la ve duberr. Ahí vive la papelera (`.duberr-papelera`) |
| `DUBERR_PROTECTED_DIRS` | vacío | Carpetas de solo lectura, separadas por `:`, cuyos archivos (y sus enlaces duros en la biblioteca) duberr nunca modifica |
| `DUBERR_PATH_MAP` | vacío | Mapeo de rutas `remoto=>local` separado por `;` (ver [Mapeo de rutas](#mapeo-de-rutas)) |
| `DUBERR_BASE_PROFILE` | vacío | Nombre del perfil de calidad que se clona para crear los de duberr. Vacío: «HD - 720p/1080p» (mejor si lleva el formato de castellano en el nombre), luego uno con «1080p», luego el primero |
| `DUBERR_CAST_FORMAT` | `Castellano` | Nombre del formato personalizado de Sonarr/Radarr que marca el castellano |
| `DUBERR_INTERVAL` | `120` | Segundos entre revisiones (el webhook las adelanta) |
| `DUBERR_PORT` | `3005` | Puerto de la web |
| `DUBERR_BIND` | `0.0.0.0` | Dirección en la que escucha la web (`127.0.0.1` para solo local) |
| `DUBERR_PASSWORD` | vacío | Contraseña de la web (HTTP Basic, usuario cualquiera). `/salud` y el webhook quedan exentos |
| `DUBERR_WEBHOOK_TOKEN` | generado | Token del webhook. Vacío: se genera la primera vez, se guarda en `<datos>/webhook-token` y se muestra en Ajustes |
| `DUBERR_ALLOWED_ORIGINS` | vacío | Hosts extra (`host[:puerto]`, separados por comas) aceptados en `Origin`/`Referer`, si un proxy cambia el `Host` |
| `NTFY_URL`, `NTFY_TOPIC`, `NTFY_TOKEN` | `https://ntfy.sh`, —, — | Avisos al móvil (sin `NTFY_TOPIC` no se envía nada) |
| `TZ` | `UTC` | Zona horaria |

## Integración con la suite *arr

### Qué toca duberr en Sonarr/Radarr

- **Crea dos perfiles de calidad**, «duberr · castellano» y «duberr · calidad», clonando el perfil base (ver
  `DUBERR_BASE_PROFILE`). Al quitar un elemento de duberr, se le devuelve su perfil original.
- Cambia el perfil de las series/películas que añades, pone a monitorizar lo necesario y lanza búsquedas.
- Lee la cola y, si una descarga en castellano queda bloqueada porque Sonarr no la considera «mejora», **fuerza la
  importación manual** de ese archivo (solo para sacar el castellano; después vuelve la calidad y se mezcla).
- Pide un reescaneo al terminar para que Sonarr/Radarr vean el archivo nuevo.
- El idioma «Spanish» se pregunta a la API de idiomas de cada aplicación.

### Webhook (reacción inmediata)

Sin webhook, duberr revisa cada `DUBERR_INTERVAL` segundos. Con webhook reacciona al importar un archivo.

1. Abre duberr → **Ajustes** → «Integración» y copia el token (o fíjalo con `DUBERR_WEBHOOK_TOKEN`).
2. En **Sonarr** → *Ajustes → Conectar → + → Webhook*:
   - **URL:** `http://duberr:3005/api/webhook/sonarr?token=TU_TOKEN` (`duberr` = nombre del contenedor en la red
     compartida). Alternativa: URL sin `?token=` y una cabecera `X-Api-Key: TU_TOKEN`.
   - **Método:** `POST`.
   - **Eventos:** *Al importar*, *Al importar una actualización*, *Al renombrar*, *Al añadir serie*. Opcional:
     *Requiere intervención manual*, para que duberr fuerce antes las importaciones bloqueadas.
3. Pulsa **Probar**: debe salir en verde (duberr responde `200` al evento `Test`).
4. En **Radarr**, igual con `/api/webhook/radarr` y los eventos *Al importar*, *Al importar una actualización*,
   *Al renombrar*, *Al añadir película*.

Respuestas: `200` siempre que el token sea válido (los eventos que no son de interés se ignoran), `401` sin token o
con uno incorrecto, `400` si el cuerpo no es JSON. Solo reaccionan los elementos que ya están en duberr. Como duberr
no mezcla archivos importados hace menos de 5 minutos, vuelve a despertar ~5 minutos después del evento.

### Mapeo de rutas

Si Sonarr/Radarr ven la biblioteca en una ruta distinta de la de duberr (montajes distintos), define
`DUBERR_PATH_MAP` como pares `remoto=>local` separados por `;`:

```yaml
DUBERR_PATH_MAP: "/tv=>/data/media/tv;/movies=>/data/media/movies"
```

Se aplica a **todas** las rutas que devuelven Sonarr/Radarr (archivos de episodio y película, carpeta de salida de la
cola, importación manual) y, al revés, a las rutas que duberr les envía. Gana la regla de prefijo más largo; admite
rutas de Windows en el lado remoto (`D:\Series=>/data/media/series`, sin distinguir mayúsculas). Si usas la
convención TRaSH (`/data` en todos los contenedores) no hace falta.

### Interfaz estable para monitorizar

Pensada para Homarr, Uptime Kuma, scripts, etc. No cambiarán sin avisar en el [CHANGELOG](CHANGELOG.md):

| Ruta | Respuesta |
|---|---|
| `GET /salud` | `200` y el texto `ok` si la web responde. Siempre sin contraseña (para `HEALTHCHECK` y monitores). También acepta `HEAD` |
| `GET /api/estado` | JSON: `version`, `trabajando`, `paso`, `ultima_pasada`, `cola` e `items` (por cada serie/película: `titulo`, `fase`, `resumen`) |
| `GET /api/vivo` | JSON del trabajador: `trabajando`, `paso`, `desde`, `ultima_pasada`, `cola` |

`/api/estado` y `/api/vivo` piden la contraseña si hay `DUBERR_PASSWORD` (HTTP Basic: la mayoría de monitores lo
admiten). Ninguna respuesta incluye claves de API.

## Seguridad

La web **no está pensada para internet**: sus acciones lanzan `ffmpeg` y `mkvmerge` sobre tu biblioteca. Medidas
incluidas: contraseña opcional (HTTP Basic, comparación en tiempo constante), comprobación de `Origin`/`Referer` en
todos los `POST` (anti-CSRF; sin ninguno de los dos, solo con token), webhook con token propio, límite de tamaño de
petición, cabeceras `X-Frame-Options`/`nosniff` y ninguna clave en las respuestas. Detalle y modelo de amenazas en
[SECURITY.md](SECURITY.md). Usa `DUBERR_PASSWORD` y no publiques el puerto fuera de tu LAN/VPN.

## Limitaciones

- Interfaz y mensajes **solo en español**, y el idioma que se busca es **solo castellano** (no latino).
- Pensado para bibliotecas **Matroska**: el resultado de una mezcla es un `.mkv`. Con otros contenedores (mp4, avi)
  no está garantizado el comportamiento.
- Depende de que exista un formato personalizado de castellano en Sonarr/Radarr y de que los indexadores lo etiqueten
  bien. No encuentra contenido: trabaja con lo que tus *arr ya obtienen.
- Si no hay una sincronía fiable (audios muy pobres o versiones muy distintas), no mezcla; hay ajustes («Si falla»,
  desfase manual) para casos difíciles.
- Una sola instancia por biblioteca (el estado está en archivos JSON, sin base de datos).
- Los ids de calidad de los perfiles que crea (720p/1080p) son los estándar de Sonarr/Radarr.
- No probado con Sonarr v3.

## Aviso legal

duberr no incluye ni enlaza contenido: trabaja sobre tu propia biblioteca. Cada usuario es responsable de cumplir la
ley de su país respecto a los archivos que gestiona.

## Licencia

[MIT](LICENSE) © 2026 m0uwastaken. Cambios en [CHANGELOG.md](CHANGELOG.md).
