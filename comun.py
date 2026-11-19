"""duberr · estado compartido entre el trabajador (duberr.py) y la web (web.py)."""
import hmac, json, os, re, secrets, threading, time, urllib.request

import nucleo as N
from arr import sonarr, radarr, env

VERSION = '0.1.0'
DATOS = env('DUBERR_DATA', 'DATOS', defecto='/duberr')
AUDIO = f'{DATOS}/audio'
CONFIG = f'{DATOS}/config.json'
ESTADO = f'{DATOS}/estado-mezclas.json'
RESULTADOS = f'{DATOS}/resultados.json'
ACCIONES = f'{DATOS}/acciones.json'
CACHE = f'{DATOS}/cache'
ENTRADA = f'{DATOS}/entrada'          # carpeta de entrada: <Serie o Película>/<archivos en castellano>
TOKEN_WEBHOOK = f'{DATOS}/webhook-token'
MEDIA = env('DUBERR_MEDIA', 'MEDIA', defecto='/data/media').rstrip('/') or '/'
PAPELERA = f'{MEDIA}/.duberr-papelera'  # mismo sistema de archivos que la biblioteca: se guarda con un enlace duro
# Carpetas de solo lectura (p. ej. otra carpeta del servidor que tiene enlaces duros a la biblioteca): duberr nunca modifica
# los archivos que comparten inodo con ellas ni escribe dentro. Separadas por «:».
PROTEGIDAS = [d.rstrip('/') or '/' for d in env('DUBERR_PROTECTED_DIRS').split(':') if d.strip()]
CASTELLANO_CF = env('DUBERR_CAST_FORMAT', defecto='Castellano')   # formato personalizado de Sonarr/Radarr que puntúa el castellano
VIDEO = ('.mkv', '.mp4', '.avi', '.m4v', '.ts', '.m2ts', '.mov', '.wmv', '.rmvb', '.rm', '.ogm', '.mpg', '.webm')
PERFIL_CAST, PERFIL_CAL = 'duberr · castellano', 'duberr · calidad'
OBJETIVO = {'Bluray-1080p', 'Bluray-1080p Remux', 'Remux-1080p', 'Bluray-2160p', 'Bluray-2160p Remux', 'Remux-2160p'}
FASES = {'solo_castellano': 'Solo castellano', 'castellano': 'Buscando castellano', 'calidad': 'Buscando calidad',
         'terminado': 'Terminado', 'pausado': 'En pausa'}

cerrojo = threading.RLock()
despertar = threading.Event()
vivo = {'trabajando': '', 'paso': '', 'desde': 0.0, 'ultima_pasada': '', 'cola': 0}
log = N.log


# ---------------------------------------------------------------- secretos y despertar al trabajador
def secretos():
    """Valores que nunca deben salir en una respuesta (claves de Sonarr/Radarr, ntfy, contraseña y token)."""
    vals = [sonarr.key, radarr.key, env('NTFY_TOKEN'), env('DUBERR_PASSWORD'), env('DUBERR_WEBHOOK_TOKEN')]
    try:
        with open(TOKEN_WEBHOOK) as fh:
            vals.append(fh.read().strip())
    except OSError:
        pass
    return [v for v in vals if v and len(v) >= 4]


def sin_claves(texto):
    """Quita de un texto (p. ej. un mensaje de error) cualquier clave conocida."""
    texto = str(texto)
    for v in secretos():
        texto = texto.replace(v, '***')
    return texto


def token_webhook():
    """Token del webhook: DUBERR_WEBHOOK_TOKEN, o uno generado la primera vez y guardado en la carpeta de datos."""
    t = env('DUBERR_WEBHOOK_TOKEN')
    if t:
        return t
    with cerrojo:
        try:
            with open(TOKEN_WEBHOOK) as fh:
                t = fh.read().strip()
        except OSError:
            t = ''
        if not t:
            t = secrets.token_urlsafe(24)
            os.makedirs(DATOS, exist_ok=True)
            fd = os.open(TOKEN_WEBHOOK, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w') as fh:
                fh.write(t + '\n')
        return t


def token_valido(candidato):
    """Comparación en tiempo constante con el token del webhook."""
    return bool(candidato) and hmac.compare_digest(str(candidato).encode(), token_webhook().encode())


_timer_despertar = None


def despertar_en(segundos):
    """Despierta al trabajador dentro de `segundos` (si ya había un aviso pendiente, se sustituye por este)."""
    global _timer_despertar
    with cerrojo:
        if _timer_despertar:
            _timer_despertar.cancel()
        _timer_despertar = threading.Timer(segundos, despertar.set)
        _timer_despertar.daemon = True
        _timer_despertar.start()


def en_biblioteca(ruta):
    """¿La ruta (ya traducida a las de duberr) está dentro de la carpeta de medios (DUBERR_MEDIA)?"""
    return bool(ruta) and (ruta == MEDIA or ruta.startswith(MEDIA.rstrip('/') + '/'))


def esta_protegido(ruta):
    """¿La ruta está dentro de una carpeta protegida (DUBERR_PROTECTED_DIRS)?"""
    return any(ruta == d or ruta.startswith(d.rstrip('/') + '/') for d in PROTEGIDAS)


# ---------------------------------------------------------------- ficheros
def leer(ruta, defecto):
    try:
        with open(ruta) as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return defecto


def escribir(ruta, datos):
    with open(ruta + '.tmp', 'w') as fh:
        json.dump(datos, fh, ensure_ascii=False, indent=1)
    os.replace(ruta + '.tmp', ruta)


def seguro(nombre):
    return re.sub(r'[\\/:*?"<>|]', '', nombre).strip()


def cfg():
    c = leer(CONFIG, {})
    c.setdefault('items', {})
    c.setdefault('ajustes', {})
    return c


def guardar_cfg(c):
    escribir(CONFIG, c)


def cambiar_cfg(fn):
    """Lee, cambia y guarda la configuración sin pisar lo que haya cambiado otro hilo."""
    with cerrojo:
        c = cfg()
        r = fn(c)
        guardar_cfg(c)
        return r


def resultado(item, ep, texto, ok, detalle='', clave_item='', extra=None):
    with cerrojo:
        r = leer(RESULTADOS, [])
        r.insert(0, {'fecha': time.strftime('%Y-%m-%d %H:%M'), 'item': item, 'ep': ep, 'texto': texto, 'ok': ok,
                     'detalle': detalle, 'k': clave_item, **(extra or {})})
        escribir(RESULTADOS, r[:800])


def pedir_accion(item_k, clave, accion, ajustes=None):
    with cerrojo:
        q = [a for a in leer(ACCIONES, []) if not (a['item'] == item_k and a['clave'] == clave)]
        q.append({'item': item_k, 'clave': clave, 'accion': accion, 'ajustes': ajustes, 'fecha': time.time()})  # None = no tocar los del episodio
        escribir(ACCIONES, q)
    despertar.set()


ACCION_CURSO = f'{DATOS}/accion-en-curso.json'


def tomar_accion():
    """Saca la primera acción de la cola y la apunta como «en curso» (si se reinicia, vuelve a la cola)."""
    with cerrojo:
        q = leer(ACCIONES, [])
        if not q:
            return None
        escribir(ACCION_CURSO, q[0])
        escribir(ACCIONES, q[1:])
        return q[0]


def accion_hecha():
    with cerrojo:
        if os.path.exists(ACCION_CURSO):
            os.remove(ACCION_CURSO)


def recuperar_accion():
    """Al arrancar: la acción que quedó a medias vuelve al principio de la cola."""
    with cerrojo:
        a = leer(ACCION_CURSO, None)
        if a:
            q = [x for x in leer(ACCIONES, []) if not (x['item'] == a['item'] and x['clave'] == a['clave'])]
            escribir(ACCIONES, [a] + q)
            os.remove(ACCION_CURSO)
        return a


def tomar_acciones():
    with cerrojo:
        q = leer(ACCIONES, [])
        if q:
            escribir(ACCIONES, [])
        return q


def acciones_pendientes():
    return leer(ACCIONES, [])


# ---------------------------------------------------------------- estado de las mezclas
def clave_archivo(p, st=None):
    st = st or os.stat(p)
    return f'{p}|{st.st_ino}|{st.st_size}'


def estado_de(v):
    """Las entradas antiguas eran texto; las nuevas, un dict con 'r' (resultado)."""
    if isinstance(v, dict):
        return v
    v = v or ''
    if v.startswith('mezclado ('):                  # formato antiguo: era la clave del ORIGINAL ya sustituido
        return {'r': 'sustituido', 'txt': v}
    r = ('mezclado' if v.startswith('mezclado') else 'sin_sincronizar' if v.startswith('sin sincronizar') else
         'error' if v.startswith('error') else 'origen' if v.startswith('origen') else 'ya_spa' if 'spa' in v else 'otro')
    return {'r': r, 'txt': v}


# ---------------------------------------------------------------- Sonarr / Radarr
def _segundos(rt):
    try:
        x = [float(v) for v in (rt or '').split(':')]
        return x[0] * 3600 + x[1] * 60 + x[2] if len(x) == 3 else x[0] * 60 + x[1] if len(x) == 2 else None
    except ValueError:
        return None


def episodios(item):
    """Episodios (o la película) con su archivo."""
    if item['tipo'] == 'serie':
        eps = sonarr.get('episode', seriesId=item['id'])
        fs = {f['id']: f for f in sonarr.get('episodefile', seriesId=item['id'])}
        ahora = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        out = []
        for e in eps:
            f = fs.get(e.get('episodeFileId') or -1) or {}
            out.append({'clave': f"S{e['seasonNumber']:02d}E{e['episodeNumber']:02d}", 'temporada': e['seasonNumber'],
                        'episodio': e['episodeNumber'], 'absoluto': e.get('absoluteEpisodeNumber'),
                        'epid': e['id'], 'titulo': e.get('title', ''), 'emitido': (e.get('airDateUtc') or '9') < ahora,
                        'monitorizado': e.get('monitored'), 'path': f.get('path'),
                        'calidad': f['quality']['quality']['name'] if f else None,
                        'idiomas': [l['name'] for l in f.get('languages', [])],
                        'audio': (f.get('mediaInfo') or {}).get('audioLanguages', ''), 'size': f.get('size', 0),
                        'duracion': _segundos((f.get('mediaInfo') or {}).get('runTime'))})
        return sorted(out, key=lambda x: x['clave'])
    m = radarr.get(f"movie/{item['id']}")
    f = m.get('movieFile') or {}
    return [{'clave': 'pelicula', 'temporada': None, 'episodio': None, 'epid': m['id'], 'titulo': m['title'],
             'emitido': True, 'monitorizado': m.get('monitored'), 'path': f.get('path'),
             'calidad': f['quality']['quality']['name'] if f else None, 'idiomas': [l['name'] for l in f.get('languages', [])],
             'audio': (f.get('mediaInfo') or {}).get('audioLanguages', ''), 'size': f.get('size', 0),
             'duracion': _segundos((f.get('mediaInfo') or {}).get('runTime'))}]


_cache_eps = {}


def episodios_cache(item, segundos=20):
    k = f"{item['tipo']}:{item['id']}"
    t, v = _cache_eps.get(k, (0, None))
    if time.time() - t > segundos or v is None:
        v = episodios(item)
        _cache_eps[k] = (time.time(), v)
    return v


def ruta_audio(item, clave):
    if item['tipo'] == 'serie':
        return f"{AUDIO}/{seguro(item['titulo'])}/{clave}.mka"
    return f"{AUDIO}/peliculas/{seguro(item['titulo'])}.mka"


def elegido(item, e):
    return item['tipo'] == 'pelicula' or e['temporada'] in item.get('temporadas', [])


def nombre_ep(item, clave):
    return f"{item['titulo']} {clave if clave != 'pelicula' else ''}".strip()


# ---------------------------------------------------------------- papelera
def papelera_de(item, clave):
    """Originales guardados de un episodio, del más nuevo al más viejo: [(ruta, meta)]."""
    d = os.path.join(PAPELERA, seguro(item['titulo']))
    out = []
    if os.path.isdir(d):
        for f in os.listdir(d):
            if f.endswith('.json') and f.startswith(clave + ' · '):
                meta = leer(os.path.join(d, f), {})
                r = os.path.join(d, f[:-5])
                if os.path.exists(r) and meta.get('tipo', 'video') == 'video':
                    out.append((r, meta))
    return sorted(out, key=lambda x: -x[1].get('t', 0))


def srt_guardado(item, clave, nombre):
    """Subtítulo externo original (antes del primer recorte) guardado en la papelera, o None."""
    d = os.path.join(PAPELERA, seguro(item['titulo']))
    if os.path.isdir(d):
        for f in sorted(os.listdir(d)):
            if f.startswith(clave + ' · ') and f.endswith(' · ' + nombre) and os.path.exists(os.path.join(d, f + '.json')):
                return os.path.join(d, f)
    return None


def papelera_resumen():
    n, propio, total = 0, 0, 0
    if os.path.isdir(PAPELERA):
        for r, _, fs in os.walk(PAPELERA):
            for f in fs:
                if f.endswith('.json'):
                    continue
                st = os.stat(os.path.join(r, f))
                n += 1
                total += st.st_size
                if st.st_nlink == 1:
                    propio += st.st_size
    return {'archivos': n, 'ocupa': propio, 'total': total}


# ---------------------------------------------------------------- carátulas
def caratula(tipo, id_, clase='poster'):
    """Carátula desde Sonarr/Radarr, guardada 7 días en /duberr/cache."""
    os.makedirs(CACHE, exist_ok=True)
    ruta = f'{CACHE}/{tipo}-{int(id_)}-{clase}.jpg'
    if os.path.exists(ruta) and time.time() - os.path.getmtime(ruta) < 7 * 86400:
        with open(ruta, 'rb') as fh:
            return fh.read()
    a = sonarr if tipo == 'sonarr' else radarr
    tam = '-500' if clase == 'poster' else '-360' if clase == 'fanart' else ''
    req = urllib.request.Request(f'{a.url}/api/v3/mediacover/{int(id_)}/{clase}{tam}.jpg', headers={'X-Api-Key': a.key})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            datos = r.read()
            if not r.headers.get('Content-Type', '').startswith('image'):
                raise ValueError('no es una imagen')
    except Exception:
        if os.path.exists(ruta):
            with open(ruta, 'rb') as fh:
                return fh.read()
        return None
    with open(ruta + '.tmp', 'wb') as fh:
        fh.write(datos)
    os.replace(ruta + '.tmp', ruta)
    return datos
