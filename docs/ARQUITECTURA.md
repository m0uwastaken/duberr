# Arquitectura de duberr

Cómo está hecho y por qué. Para usarlo, ver el [README](../README.md).

## Visión general

Un único proceso Python con dos hilos principales:

- **Trabajador** (`duberr.py`): bucle que cada `DUBERR_INTERVAL` segundos (120 por defecto) recorre las series y
  películas dadas de alta, habla con Sonarr/Radarr y mezcla lo que toque. Se **despierta antes** (`despertar.set()`)
  cuando se pulsa un botón de la web o llega un webhook de un elemento que está en duberr.
- **Web** (`web.py`): `ThreadingHTTPServer` con HTML renderizado en el servidor y un poco de JS. Sin dependencias.

Se comunican por el estado compartido de `comun.py` (archivos JSON en `DUBERR_DATA` protegidos por un cerrojo) y por la
cola de acciones. Todo lo pesado (medir, mezclar) lo hace el trabajador, de uno en uno; la web solo escribe peticiones.

Módulos:

| Archivo | Papel |
|---|---|
| `duberr.py` | Trabajador: perfiles, búsquedas, importación forzada, carpeta de entrada, mezcla, papelera |
| `nucleo.py` | Audio, sin Sonarr: leer pistas, medir la sincronía, montar y mezclar, verificar |
| `comun.py` | Rutas y estado compartido, configuración, cola de acciones, papelera, episodios desde Sonarr/Radarr |
| `web.py` | Interfaz, acciones por `POST`, webhook, seguridad (contraseña, `Origin`), vista previa |
| `ajustes.py` | Parámetros del proceso por capas, preajustes |
| `arr.py` | Cliente de Sonarr/Radarr (API v3), mapeo de rutas, idioma «Spanish» |
| `healthcheck.py` | Comprobación de salud de Docker |

## Datos en `DUBERR_DATA`

| Archivo / carpeta | Contenido |
|---|---|
| `config.json` | Series y películas dadas de alta, fase de cada una y ajustes |
| `estado-mezclas.json` | Resultado por archivo (clave: ruta + inodo + tamaño): mezclado, sin sincronizar, error... |
| `resultados.json` | Registro de actividad (últimos 800 sucesos) |
| `acciones.json`, `accion-en-curso.json` | Cola de acciones pedidas desde la web |
| `audio/<Serie>/SxxEyy.mka` | Castellano guardado sin recodificar |
| `entrada/<Título>/` | Carpeta de entrada («solo añadir castellano») |
| `cache/` | Carátulas (7 días) y clips de vista previa |
| `webhook-token` | Token del webhook, si no se fijó por variable (permisos `0600`) |

La papelera vive en `DUBERR_MEDIA/.duberr-papelera`, en el mismo sistema de archivos que la biblioteca porque cada
archivo guardado es un **enlace duro**.

## Flujo de búsqueda (fases)

Por serie (temporadas elegidas) o película. La fase se guarda en `config.json`:

1. **`castellano`.** Mientras ningún episodio elegido tenga castellano, perfil «duberr · castellano» (cualquier calidad;
   el formato personalizado de castellano puntúa 100 y es obligatorio). Se lanza la búsqueda.
2. **Guardar.** En todas las fases, el castellano de *todos* los episodios (también de temporadas no elegidas) se
   guarda sin recodificar en `audio/`.
3. **`calidad`.** Por episodio: en cuanto uno tiene el castellano guardado, la serie pasa a «duberr · calidad» (mejor
   calidad hasta Bluray 1080p, cualquier idioma), los episodios con castellano guardado se monitorizan y se busca su
   calidad. Los que aún no tienen castellano quedan **sin monitorizar** (para que Sonarr no les baje una versión sin
   él) y duberr les busca el castellano a mano: elige el mejor resultado de Sonarr con el formato de castellano (no
   latino), asignado a ese episodio. Si Sonarr bloquea la importación («no es mejora»), duberr la fuerza por importación
   manual, mirando solo la carpeta de la descarga y exigiendo que el archivo esté dentro de ella.
4. **`terminado`.** Todo en la mejor calidad con castellano. Cada 24 h vuelve a buscar lo que falte.

Otros estados: `solo_castellano` (modo de la carpeta de entrada: no toca perfiles ni busca) y `pausado`.
«Pasar ya a calidad» deja de buscar castellano y monitoriza todo; «Buscar castellano» lo pide aunque ya esté en la
fase de calidad (máx. 15 episodios por pulsación). Al quitar un elemento, se le devuelve su perfil original.

## Perfiles de calidad

`asegurar_perfiles` crea (si faltan) los dos perfiles clonando un perfil base (`DUBERR_BASE_PROFILE`; ver `perfil_base`
para el orden de respaldo):

- **«duberr · castellano»:** todas las calidades salvo una lista de exclusiones, sin mejoras (`upgradeAllowed=false`),
  `minFormatScore=100`; el formato personalizado de castellano (`DUBERR_CAST_FORMAT`) puntúa 100 y los formatos con
  puntuación negativa se respetan.
- **«duberr · calidad»:** HDTV/WEB/Bluray 720p y 1080p, con mejoras, corte en Bluray-1080p, formatos con puntuación
  positiva a 0.

Los ids de calidad (4, 14, 5, 6, 9, 15, 3, 7...) son los estándar de Sonarr/Radarr.

## Medición y mezcla (`nucleo.py`)

1. Se decodifica la pista de referencia del vídeo (por defecto el **idioma original** de Sonarr/Radarr; el doblaje se
   hizo sobre su música y efectos) y el castellano, a 16 kHz, y cada 10 ms se obtiene la energía de 8 bandas de
   frecuencia. Se compara el «ataque» de cada banda (cuándo sube el sonido) con correlación y blanqueo parcial (PHAT 0,5).
2. **1.ª pasada:** ventanas de 30 s con margen amplio → desfases candidatos y velocidad (1, 25→23,976, 23,976→25).
3. **2.ª pasada:** cada 5 s se puntúa cada candidato y un Viterbi elige el camino: tramos, cada uno con su desfase
   (el castellano no vuelve atrás: un salto atrás dejaría un hueco en el vídeo).
4. **Afinado:** desfase de cada tramo, deriva de velocidad restante (≥ 120 ms se corrige) y punto exacto de cada corte.
   De ahí sale qué parte del vídeo no tiene castellano y qué parte del castellano sobra.
5. **Partes sin castellano:** rellenar con el original (por defecto), recortarlas del vídeo (en fotogramas clave; se
   ajustan los `.srt` externos) o dejarlas en silencio.
6. **Mezcla:** `mkvmerge` mete la pista nueva (copia sin recodificar; solo se recodifica, a AC3 o FLAC, si hay que
   cambiar la velocidad, cortar o rellenar).
7. **Comprobación:** se vuelve a medir el resultado antes de sustituir nada. Si algo no cuadra, el archivo original queda.

La pista «castellana» casi idéntica a la original no es castellano (mal etiquetada): no se mezcla. El latino y las
pistas de comentarios nunca cuentan como castellano.

## Seguridad de los archivos y papelera

- Se escribe en un `.tmp` de la misma carpeta y se **renombra** encima: nunca se modifica un archivo existente, así que
  los demás enlaces duros al archivo antiguo conservan su contenido.
- Las **carpetas protegidas** (`DUBERR_PROTECTED_DIRS`) se recorren en cada pasada: los archivos de la biblioteca que
  comparten inodo con ellas se saltan.
- Se saltan también los archivos importados hace menos de 5 minutos.
- La **papelera** guarda el archivo sustituido (y los `.srt` que se ajusten) como enlace duro `<clave> · <fecha> · <nombre>`
  más un `.json` de metadatos, `papelera_dias` días (14 por defecto; 0 = no guardar). Si el archivo antiguo tenía otros
  enlaces duros, no ocupa espacio extra. Se purga sola cada 6 h.
- **Deshacer** devuelve el original de la papelera; si no hay, quita la pista que puso duberr (lo recortado no se
  puede recuperar). **Rehacer** repite la mezcla desde el original con los ajustes actuales.

## Acciones de la web y reinicios

Las acciones se encolan en `acciones.json` y el trabajador las toma de una en una; la que está en marcha se apunta en
`accion-en-curso.json` y, si el proceso se reinicia, vuelve a la cola. También se borran al arrancar los temporales de
una mezcla cortada. Un reinicio no pierde nada, pero evítalo mientras mezcla (el punto de la cabecera lo indica).

## Ajustes por capas

Valores de fábrica ← Ajustes generales ← serie/película ← episodio (`ajustes.efectivos`). Cambiar los ajustes reintenta
solos los episodios que no sincronizaron (`ajustes.huella` resume los que cambian el resultado). Preajustes:
Recomendado, Si falla (más permisivo) y Estricto.

## Integración con Sonarr/Radarr

- **API v3** con la cabecera `X-Api-Key`. `arr.Arr` es un cliente mínimo; las rutas de disco que devuelve (`path`,
  `outputPath`, `rootFolderPath`) se traducen con `DUBERR_PATH_MAP` y las que se le envían (`POST`, parámetro `folder`)
  se traducen al revés. Los objetos que se leen para devolverlos con `PUT` (cambio de perfil) van **sin** traducir
  (`crudo=True`), para no alterar la ruta de la serie.
- **Webhook:** `webhook_arr` en `web.py`. Con `Test` responde 200; con `Download`, `Rename`, `SeriesAdd`,
  `MovieAdded` o `ManualInteractionRequired` de un elemento ya dado de alta despierta al trabajador y vuelve a hacerlo
  305 s después (la mezcla espera 5 minutos tras una importación). Lo demás se ignora.
- **Carátulas:** se piden a `mediacover` de Sonarr/Radarr y se guardan 7 días.

## Interfaz de monitorización

`/salud`, `/api/estado` y `/api/vivo`: ver el [README](../README.md#interfaz-estable-para-monitorizar).

## Mapa del código (busca la función con `grep -n "def nombre"` y lee solo esa)

- **`duberr.py`:** `trabajador` (bucle) → `procesar_entrada` (carpeta de entrada), `orquestar` (fases por serie:
  `perfil_base`/`asegurar_perfiles`/`poner_perfil`, monitorizar, `buscar`, `buscar_castellano`, `forzar_importaciones`),
  `mezclar_pendientes` → `procesar` (mide y mezcla un episodio; `a_papelera`, `subtitulos_externos`,
  `inodos_protegidos`), `hacer_acciones` (cola de la web; `deshacer`), `purgar_papelera`, `migrar` (formato antiguo de
  `config.json`), `guardar_item` + `PROPIOS` (campos que escribe el trabajador sin pisar los de la web).
- **`nucleo.py`:** `info`, `pistas_castellano`, `es_latino`, `es_comentario`, `pistas_referencia`; medición `medir`
  (bandas → `ataques` → `_etapa1`/`_etapa2` → `_camino` (Viterbi) → `piezas_de`/`_subtramos`, `_velocidades`);
  montaje `partes_a_conservar`, `montar_audio`, `mezclar`, `verificar`, `remapear_srt`, `guardar_pista`.
- **`comun.py`:** rutas y variables, `cfg`/`cambiar_cfg`, `resultado`, cola (`pedir_accion`, `tomar_accion`,
  `recuperar_accion`), `episodios`/`episodios_cache`, `ruta_audio`, `elegido`, papelera, `caratula`, `vivo`,
  seguridad (`token_webhook`, `token_valido`, `sin_claves`), `despertar`/`despertar_en`.
- **`web.py`:** vistas `vista_biblioteca`, `vista_item`, `vista_anadir`, `vista_actividad`, `vista_ajustes`
  (+ `integracion_html`); `estado_ep`; acciones `accion_web`, `anadir_web`, `ajustes_web`, `episodio_web`;
  `generar_previa`; `webhook_arr`; seguridad en `Web._autorizado`/`_origen_valido`/`_webhook`.
- **`ajustes.py`:** valores de fábrica, `PRESETS`, `efectivos`, `huella`. **`arr.py`:** `env`, `mapeo`/`ruta_local`/
  `ruta_remota`, clase `Arr` (`get`/`post`/`put`/`comando`/`idioma_spanish`).
