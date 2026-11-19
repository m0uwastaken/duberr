"""duberr: series y películas en la mejor calidad (hasta Bluray 1080p) con el audio en castellano.

Para cada serie (temporadas elegidas) o película que se le apunta:
  1. castellano  Si a algún episodio elegido le falta el castellano, pone el perfil «duberr · castellano»
                 (cualquier calidad, solo castellano) y busca.
  2. guardar     Guarda sin recodificar la pista en castellano de TODOS los episodios de la serie
                 (no solo los elegidos) en <datos>/audio/<Serie>/SxxEyy.mka, para no perderla nunca.
  3. calidad     Solo con todo guardado, pone el perfil «duberr · calidad» (mejor calidad hasta
                 Bluray 1080p, cualquier idioma) y busca las temporadas elegidas.
  4. mezclar     Cada versión nueva sin castellano recibe la pista guardada (nucleo.medir + nucleo.mezclar):
                 medida, montada, mezclada en un archivo nuevo y comprobada antes de sustituir.

Seguridad: las carpetas protegidas (DUBERR_PROTECTED_DIRS) se montan en solo lectura y se respetan; nunca se escribe
sobre un archivo existente (archivo nuevo + renombrar), así que los enlaces duros a otras carpetas no se tocan. El archivo sustituido queda en la papelera (enlace duro, N días) para
poder deshacer. Al quitar una serie de duberr se le devuelve su perfil original.

Archivos: comun.py (estado compartido), ajustes.py (parámetros), nucleo.py (audio), web.py (interfaz), arr.py (API).
"""
import json, os, re, shutil, threading, time, traceback

import ajustes as A
import comun as C
import nucleo as N
import web
from arr import sonarr, radarr, env, mapeo

INTERVALO = int(env('DUBERR_INTERVAL', 'INTERVALO', defecto='120'))      # segundos entre pasadas (el webhook despierta antes)
REBUSCAR_H = 24                       # volver a buscar lo que falta cada tantas horas
REINTENTOS = 3                        # errores (no de sincronía) se reintentan solos, con 1 h, 2 h, 3 h de espera
log = C.log
cache_info = {}                       # path|ino|size -> {'spa': bool}


def generales():
    return C.cfg().get('ajustes', {})


def avisar(titulo, mensaje, prioridad=3, tipo='episodio'):
    modo = A.efectivos(generales()).get('avisos', 'todos')
    if modo == 'ninguno' or (modo == 'importantes' and tipo == 'episodio'):
        log('AVISO (no enviado)', titulo, '|', mensaje)
        return
    N.avisar(titulo, mensaje, prioridad)


def paso(texto):
    C.vivo['paso'] = texto


N.progreso = paso


# ---------------------------------------------------------------- perfiles en Sonarr / Radarr
# Ids de calidad de Sonarr/Radarr (estables entre versiones).
CAL_SI = {4, 1001, 14, 5, 6, 9, 1002, 15, 3, 7}            # HDTV/WEB/Bluray 720p y 1080p (sin Remux ni 4K)
CAST_NO = {'sonarr': {10, 16, 17, 18, 19, 20, 21, 1003},
           'radarr': {10, 16, 17, 18, 19, 22, 24, 25, 26, 27, 28, 29, 30, 31, 1003}}


def _items(items, permitido):
    for x in items:
        if x.get('items'):
            _items(x['items'], permitido)
            x['allowed'] = any(y['allowed'] for y in x['items'])
        else:
            x['allowed'] = permitido(x['quality']['id'])
    return items


def perfil_base(perfiles):
    """Perfil de calidad que se clona para crear los de duberr. Orden: el que diga DUBERR_BASE_PROFILE (nombre exacto);
    uno «HD - 720p/1080p» que lleve el formato de castellano en el nombre; «HD - 720p/1080p»; uno con «1080p»; el primero."""
    propios = (C.PERFIL_CAST, C.PERFIL_CAL)
    cand = [p for p in perfiles if p['name'] not in propios] or perfiles
    if not cand:
        raise RuntimeError('Sonarr/Radarr no tiene ningún perfil de calidad que clonar')
    pedido = env('DUBERR_BASE_PROFILE')
    if pedido:
        p = next((p for p in cand if p['name'].lower() == pedido.lower()), None)
        if p:
            return p
        log(f'DUBERR_BASE_PROFILE «{pedido}» no existe; uso uno de respaldo')
    hd = [p for p in cand if p['name'].lower().startswith('hd - 720p/1080p')]
    return (next((p for p in hd if C.CASTELLANO_CF.lower() in p['name'].lower()), None) or (hd[0] if hd else None) or
            next((p for p in cand if '1080p' in p['name']), None) or cand[0])


def asegurar_perfiles(a):
    """Crea (si faltan) «duberr · castellano» y «duberr · calidad». Devuelve {nombre: id}."""
    perfiles = a.perfiles()
    ids = {p['name']: p['id'] for p in perfiles}
    if all(n in ids for n in (C.PERFIL_CAST, C.PERFIL_CAL)):
        return ids
    base = perfil_base(perfiles)
    if not any(f.get('name') == C.CASTELLANO_CF for f in base.get('formatItems') or []):
        log(a.tipo, f'AVISO: no existe el formato personalizado «{C.CASTELLANO_CF}» (Ajustes > Perfiles > Formatos '
                    'personalizados); sin él, la fase «castellano» no aceptará ninguna descarga. Ver README.')
    for nombre in (C.PERFIL_CAST, C.PERFIL_CAL):
        if nombre in ids:
            continue
        p = json.loads(json.dumps(base))
        p.pop('id', None)
        p['name'] = nombre
        if nombre == C.PERFIL_CAST:
            _items(p['items'], lambda q: q not in CAST_NO[a.tipo])
            p['upgradeAllowed'] = False
            p['minFormatScore'] = 100
            for f in p.get('formatItems') or []:
                f['score'] = 100 if f['name'] == C.CASTELLANO_CF else min(f['score'], 0) if f['score'] < 0 else 0
        else:
            _items(p['items'], lambda q: q in CAL_SI)
            p['upgradeAllowed'] = True
            p['minFormatScore'] = 0
            p['minUpgradeFormatScore'] = 1
            for f in p.get('formatItems') or []:
                f['score'] = f['score'] if f['score'] < 0 else 0
        p['cutoff'] = 7                                          # Bluray-1080p
        p['cutoffFormatScore'] = 0
        nuevo = a.post('qualityprofile', p)
        ids[nombre] = nuevo['id']
        log(a.tipo, 'perfil creado', nombre, nuevo['id'], '(clonado de', base['name'] + ')')
    return ids


def poner_perfil(item, nombre, ids):
    a = sonarr if item['tipo'] == 'serie' else radarr
    ruta = f"{'series' if item['tipo'] == 'serie' else 'movie'}/{item['id']}"
    obj = a.get(ruta, crudo=True)                    # sin traducir rutas: el objeto vuelve tal cual con PUT
    pid = ids[nombre]
    cambios = obj['qualityProfileId'] != pid
    if item.get('perfil_original') is None:
        item['perfil_original'] = obj['qualityProfileId']
    obj['qualityProfileId'] = pid
    if item['tipo'] == 'serie':
        for s in obj['seasons']:
            if s['seasonNumber'] in item['temporadas'] and not s['monitored']:
                s['monitored'] = cambios = True
    elif not obj.get('monitored'):
        obj['monitored'] = cambios = True
    obj['monitored'] = True
    if cambios:
        a.put(ruta, obj)
        log(item['titulo'], 'perfil', nombre)


def buscar(item, eps_ids=None):
    if item['tipo'] == 'pelicula':
        radarr.comando('MoviesSearch', movieIds=[item['id']])
    elif eps_ids:
        sonarr.put('episode/monitor', {'episodeIds': eps_ids, 'monitored': True})
        sonarr.comando('EpisodeSearch', episodeIds=eps_ids)
    item['ultima_busqueda'] = time.time()
    item['buscar_hecho'] = item.get('buscar_pedido', 0)
    log(item['titulo'], 'búsqueda lanzada', len(eps_ids or [1]))


def reescanear(item):
    try:
        if item['tipo'] == 'serie':
            sonarr.comando('RescanSeries', seriesId=item['id'])
        else:
            radarr.comando('RescanMovie', movieId=item['id'])
    except Exception as ex:
        log('no pude pedir el reescaneo', ex)


def tiene_spa(path):
    st = os.stat(path)
    k = C.clave_archivo(path, st)
    if k not in cache_info:
        cache_info[k] = {'spa': N.tiene_castellano(N.info(path))}
    return cache_info[k]['spa']


# ---------------------------------------------------------------- orquestador (fases de búsqueda)
def orquestar(item, ids):
    """Avanza una serie o película. Cambia 'item' (copia) y devuelve True si hay que guardarlo."""
    eps = C.episodios(item)
    if item['tipo'] == 'serie' and not item.get('idioma_original'):
        try:
            s = sonarr.get(f"series/{item['id']}")
            item['idioma_original'] = (s.get('originalLanguage') or {}).get('name')
            item['anio'] = s.get('year')
        except Exception:
            pass
    elif item['tipo'] == 'pelicula' and not item.get('idioma_original'):
        try:
            m = radarr.get(f"movie/{item['id']}")
            item['idioma_original'] = (m.get('originalLanguage') or {}).get('name')
            item['anio'] = m.get('year')
        except Exception:
            pass
    # 2. guardar el castellano de TODOS los episodios (también temporadas no elegidas)
    sin_guardar = []
    for e in eps:
        if not e['path'] or not os.path.exists(e['path']):
            continue
        dst = C.ruta_audio(item, e['clave'])
        if os.path.exists(dst):
            continue
        try:
            if tiene_spa(e['path']) or 'Spanish' in e['idiomas']:
                ok, nota = N.guardar_pista(e['path'], dst, e['idiomas'])
                if ok:
                    log(item['titulo'], e['clave'], 'castellano guardado', nota)
                    C.resultado(item['titulo'], e['clave'], 'castellano guardado', True, nota, C_k(item))
                elif e['temporada'] != 0:
                    sin_guardar.append(e['clave'])
        except Exception as ex:
            sin_guardar.append(e['clave'])
            log(item['titulo'], e['clave'], 'error al guardar', ex)
    elig = [e for e in eps if C.elegido(item, e) and e['emitido']]
    guardado = lambda e: os.path.exists(C.ruta_audio(item, e['clave']))
    falta_cast = [e for e in elig if not guardado(e)]
    forzar = bool(item.get('forzar_calidad'))
    # cada episodio va por su cuenta: con el castellano guardado ya se busca su calidad
    listos = [e for e in elig if guardado(e) or forzar]
    no_objetivo = [e for e in listos if e['calidad'] not in C.OBJETIVO]
    con_spa = sum(1 for e in elig if e['path'] and os.path.exists(e['path']) and tiene_spa(e['path']))
    item['resumen'] = {'elegidos': len(elig), 'con_castellano': len(elig) - len(falta_cast), 'con_spa': con_spa,
                       'en_calidad': sum(1 for e in elig if e['calidad'] in C.OBJETIVO), 'actualizado': time.strftime('%H:%M')}
    fase = item.get('fase')
    pedido = item.get('buscar_pedido', 0) != item.get('buscar_hecho', 0)
    if item.get('modo') == 'solo_castellano' or fase == 'pausado':
        return
    toca_buscar = pedido or time.time() - item.get('ultima_busqueda', 0) > REBUSCAR_H * 3600
    if not listos:                                   # aún nada en castellano: como antes, perfil de castellano
        poner_perfil(item, C.PERFIL_CAST, ids)
        item['fase'] = 'castellano'
        if toca_buscar:
            buscar(item, [e['epid'] for e in falta_cast])
        return
    # perfil de calidad; los episodios sin castellano (ni guardado ni sin guardar) quedan sin monitorizar para que
    # Sonarr no les baje una versión sin castellano; su castellano lo busca duberr a mano (buscar_castellano)
    poner_perfil(item, C.PERFIL_CAL, ids)
    if item['tipo'] == 'serie':
        bloqueados = {c for c in sin_guardar}       # tienen castellano en el archivo pero no se pudo guardar: no tocar
        monitor = {e['epid']: (guardado(e) or forzar) and e['clave'] not in bloqueados for e in elig}
        for valor in (True, False):
            ids_ = [k for k, v in monitor.items() if v == valor and
                    next(x for x in elig if x['epid'] == k).get('monitorizado') != valor]
            if ids_:
                sonarr.put('episode/monitor', {'episodeIds': ids_, 'monitored': valor})
                log(item['titulo'], 'monitorizados' if valor else 'sin monitorizar (falta castellano)', len(ids_))
        if sin_guardar:
            item['aviso'] = 'No se pudo guardar el castellano de ' + ', '.join(sin_guardar[:6])
        else:
            item.pop('aviso', None)
        try:
            forzar_importaciones(item, [e for e in falta_cast if e['clave'] not in bloqueados])
        except Exception as ex:
            log(item['titulo'], 'no pude revisar la cola', ex)
    item['fase'] = 'calidad'
    if toca_buscar:
        if no_objetivo:
            buscar(item, [e['epid'] for e in no_objetivo])
        if falta_cast and not forzar and item['tipo'] == 'serie':
            buscar_castellano(item, [e for e in falta_cast if e['clave'] not in sin_guardar])
        item['ultima_busqueda'] = time.time()
        item['buscar_hecho'] = item.get('buscar_pedido', 0)
    # botón «Buscar castellano»: pide a Sonarr el castellano que falta aunque ya se haya pasado a calidad
    if item.get('castellano_pedido', 0) != item.get('castellano_hecho', 0):
        item['castellano_hecho'] = item.get('castellano_pedido', 0)
        if falta_cast and item['tipo'] == 'serie':
            buscar_castellano(item, [e for e in falta_cast if e['clave'] not in sin_guardar])
        elif falta_cast:
            buscar(item)
    sin_spa =[e for e in elig if e['path'] and os.path.exists(e['path']) and not tiene_spa(e['path']) and guardado(e)]
    if not no_objetivo and not sin_spa:
        item['resumen']['sin_castellano'] = len(falta_cast)
        if fase != 'terminado':
            item['fase'] = 'terminado'
            if falta_cast:
                avisar(f"duberr · {item['titulo']}", f'{len(elig) - len(falta_cast)} episodios en la mejor calidad con castellano. '
                       f'{len(falta_cast)} siguen sin castellano: se sigue buscando cada día (o déjalos en la carpeta de entrada).', 3, 'final')
            else:
                avisar(f"duberr · {item['titulo']}", 'Terminado: todo en la mejor calidad con castellano.', 3, 'final')
        else:
            item['fase'] = 'terminado'


_LATINO_REL = re.compile(r'latin|\blat\b|latam|mex|dual.?lat', re.I)


def _es_castellano(r):
    cfs = [c.get('name', '') for c in r.get('customFormats') or []]
    if any(c.lower().startswith('latino') for c in cfs) or _LATINO_REL.search(r.get('title', '')):
        return False
    return C.CASTELLANO_CF in cfs or any(l.get('name') == 'Spanish' for l in r.get('languages') or [])


_NO_VALE = re.compile(r'blocklist|sample|seeders|unknown series|matches an alias|unable to parse|wasn.t requested|not wanted|'
                      r'size|already in the queue|been grabbed|is not monitored', re.I)


def buscar_castellano(item, eps):
    """Para cada episodio sin castellano, mira lo que encuentra Sonarr y pide la mejor versión en castellano (no latino),
    asignada a ese episodio. Las rechazadas solo por perfil, calidad o numeración absoluta valen."""
    en_cola = {q.get('episodeId') for q in sonarr.get('queue', pageSize=200, seriesIds=item['id']).get('records', [])}
    pedidos, mirados = 0, 0
    for e in eps[:15]:
        if e['epid'] in en_cola:
            continue
        mirados += 1
        try:
            rels = sonarr.get('release', episodeId=e['epid'])
        except Exception as ex:
            log(item['titulo'], e['clave'], 'búsqueda de castellano falló', ex)
            continue
        buenas = []
        for r in rels:
            if not _es_castellano(r) or any(_NO_VALE.search(x) for x in r.get('rejections') or []):
                continue
            if r.get('mappedSeasonNumber') not in (None, e['temporada']) or \
                    (r.get('mappedEpisodeNumbers') and e['episodio'] not in r['mappedEpisodeNumbers']):
                continue
            buenas.append(r)
        if not buenas:
            continue
        r = max(buenas, key=lambda r: (r['quality']['quality'].get('resolution', 0), r.get('seeders') or 0, -r.get('ageHours', 0)))
        try:
            sonarr.post('release', {'guid': r['guid'], 'indexerId': r['indexerId'], 'shouldOverride': True, 'seriesId': item['id'],
                                    'episodeIds': [e['epid']], 'quality': r['quality'], 'languages': r['languages']})
            pedidos += 1
            log(item['titulo'], e['clave'], 'castellano pedido:', r['title'])
            C.resultado(item['titulo'], e['clave'], 'castellano encontrado y pedido', True, r['title'], C_k(item))
        except Exception as ex:
            log(item['titulo'], e['clave'], 'no pude pedir', r['title'], ex)
    log(item['titulo'], f'castellano buscado en {mirados} episodios ({len(eps) - mirados} ya en descarga o pendientes), {pedidos} pedidos')


def forzar_importaciones(item, faltan):
    """Descargas en castellano que Sonarr no importa porque el archivo que hay es «mejor»: se importan igual (solo hace
    falta para sacar el castellano; luego vuelve la calidad y se mezcla)."""
    ids = {e['epid'] for e in faltan}
    if not ids:
        return
    for q in sonarr.get('queue', pageSize=200, seriesIds=item['id']).get('records', []):
        if q.get('episodeId') not in ids or q.get('trackedDownloadState') not in ('importBlocked', 'importPending'):
            continue
        msgs = ' '.join(m for s in q.get('statusMessages', []) for m in s.get('messages', []))
        if not re.search(r'upgrade|custom format|cutoff', msgs, re.I):
            continue
        # Con seriesId, Sonarr devuelve los archivos que YA están en la biblioteca (no los de la descarga) y se
        # importaba el primero encima del episodio. Por eso solo se mira la carpeta de la descarga y se exige que
        # el archivo esté dentro de ella. Las rutas ya vienen traducidas a las de duberr (DUBERR_PATH_MAP).
        salida = (q.get('outputPath') or '').rstrip('/')
        if C.en_biblioteca(salida):
            continue
        if salida:
            arch = sonarr.get('manualimport', folder=salida, downloadId=q['downloadId'], filterExistingFiles='false')
        else:                                            # sin seriesId: solo los archivos de esa descarga
            arch = sonarr.get('manualimport', downloadId=q['downloadId'], filterExistingFiles='false')
        files = [{'path': a['path'], 'seriesId': item['id'], 'episodeIds': [q['episodeId']], 'quality': a['quality'],
                  'languages': a.get('languages') or [sonarr.idioma_spanish()], 'downloadId': q['downloadId'],
                  'releaseGroup': a.get('releaseGroup')} for a in arch
                 if a.get('path', '').lower().endswith(C.VIDEO) and not C.en_biblioteca(a['path'])
                 and (not salida or a['path'].startswith(salida + '/'))][:1]
        if files:
            sonarr.post('command', {'name': 'ManualImport', 'files': files, 'importMode': 'auto'})
            log(item['titulo'], 'importación forzada del castellano:', q.get('title'))


def C_k(item):
    return f"{'sonarr' if item['tipo'] == 'serie' else 'radarr'}:{item['id']}"


PROPIOS = ('resumen', 'perfil_original', 'ultima_busqueda', 'buscar_hecho', 'castellano_hecho', 'aviso', 'error', 'idioma_original', 'anio')


def guardar_item(clave, antes, item):
    """Guarda lo que cambió el trabajador sin pisar lo que se haya cambiado desde la web mientras tanto."""
    def fn(c):
        web_it = c['items'].get(clave)
        if not web_it:
            return
        for campo in PROPIOS:
            if campo in item:
                web_it[campo] = item[campo]
            else:
                web_it.pop(campo, None)
        if item.get('fase') != antes.get('fase') and web_it.get('fase') == antes.get('fase'):
            web_it['fase'] = item['fase']
    C.cambiar_cfg(fn)


# ---------------------------------------------------------------- carpeta de entrada
def _norm(t):
    return re.sub(r'[^a-z0-9]', '', t.lower())


def _numero(nombre):
    """(temporada o None, episodio) a partir del nombre de un archivo."""
    b = os.path.splitext(os.path.basename(nombre))[0]
    for patron in (r'[Ss](\d{1,2})[ ._-]*[Ee](\d{1,3})', r'(?<!\d)(\d{1,2})x(\d{2,3})(?!\d)', r'[Cc]ap[ ._-]*(\d)(\d\d)(?!\d)'):
        m = re.search(patron, b)
        if m:
            return int(m[1]), int(m[2])
    limpio = re.sub(r'(?i)(480|576|720|1080|2160)[pi]?|[xh]\.?26[45]|(19|20)\d\d|\[[0-9a-f]{8}\]|mp3|aac|ac3|dts|5\.1|2\.0', ' ', b)
    nums = re.findall(r'(?<!\d)(\d{1,3})(?!\d)', limpio)
    return (None, int(nums[0])) if nums else (None, None)


def _buscar_destino(carpeta, series, pelis):
    k = _norm(carpeta)
    for s in series:
        nombres = [s['title'], s.get('sortTitle', '')] + [a['title'] for a in s.get('alternateTitles', [])]
        if any(_norm(x) == k for x in nombres if x):
            return 'sonarr', s
    for m in pelis:
        if _norm(m['title']) == k or _norm(f"{m['title']} ({m.get('year')})") == k:
            return 'radarr', m
    return None, None


def procesar_entrada():
    if not os.path.isdir(C.ENTRADA):
        os.makedirs(C.ENTRADA, exist_ok=True)
        return
    carpetas = [d for d in sorted(os.listdir(C.ENTRADA)) if os.path.isdir(os.path.join(C.ENTRADA, d))]
    if not carpetas:
        return
    series, pelis = None, None
    estado = C.leer(C.ESTADO, {})
    for carpeta in carpetas:
        base = os.path.join(C.ENTRADA, carpeta)
        archivos = []
        for r, ds, fs in os.walk(base):
            ds[:] = [d for d in ds if d not in ('procesados', 'con problemas')]
            archivos += [os.path.join(r, f) for f in fs if f.lower().endswith(C.VIDEO)]
        if not archivos:
            continue
        if series is None:
            series, pelis = sonarr.get('series'), radarr.get('movie')
        tipo, obj = _buscar_destino(carpeta, series, pelis)
        if not obj:
            k = f'entrada-carpeta|{carpeta}'
            if k not in estado:
                estado[k] = 'sin serie'
                C.resultado(carpeta, '', 'carpeta de entrada: no sé de qué serie o película es', False,
                            'El nombre de la carpeta tiene que ser el título tal cual está en Sonarr o Radarr.')
            continue
        clave_item = f"{tipo}:{obj['id']}"

        def alta(c):
            if clave_item not in c['items']:
                c['items'][clave_item] = {'tipo': 'serie' if tipo == 'sonarr' else 'pelicula', 'id': obj['id'],
                                          'titulo': obj['title'], 'temporadas': [], 'fase': 'solo_castellano',
                                          'modo': 'solo_castellano', 'creado': time.strftime('%Y-%m-%d %H:%M'),
                                          'perfil_original': None}
                log(obj['title'], 'añadido a duberr (solo añadir castellano, desde la carpeta de entrada)')
            return dict(c['items'][clave_item])
        item = C.cambiar_cfg(alta)
        eps = C.episodios(item)
        temporadas_normales = sorted({e['temporada'] for e in eps if e['temporada']})
        for ruta in sorted(archivos):
            st = os.stat(ruta)
            k = f'entrada|{ruta}|{st.st_size}'
            if k in estado or time.time() - st.st_mtime < 120:      # ya visto o aún copiándose
                continue
            nombre = os.path.basename(ruta)
            if item['tipo'] == 'pelicula':
                destino_ep = eps[0]
            else:
                t, n = _numero(nombre)
                destino_ep = None
                if n is not None and t is not None:
                    destino_ep = next((e for e in eps if e['temporada'] == t and e['episodio'] == n), None)
                if n is not None and not destino_ep and t is None:
                    destino_ep = next((e for e in eps if e['absoluto'] == n and e['temporada']), None)
                    if not destino_ep and len(temporadas_normales) == 1:
                        destino_ep = next((e for e in eps if e['episodio'] == n and e['temporada']), None)
            if not destino_ep:
                estado[k] = 'sin episodio'
                C.resultado(item['titulo'], nombre, 'carpeta de entrada: no sé qué episodio es', False,
                            'Ponle SxxEyy en el nombre (por ejemplo S01E05; los especiales, S00E01).', clave_item)
                continue
            dst = C.ruta_audio(item, destino_ep['clave'])
            try:
                if os.path.exists(dst):
                    estado[k] = 'ya había castellano guardado'
                    C.resultado(item['titulo'], destino_ep['clave'], 'carpeta de entrada: ya tenía castellano guardado', True, nombre, clave_item)
                    ok = True
                else:
                    ok, nota = N.guardar_pista(ruta, dst, ['Spanish'], unica_vale=True)
                    estado[k] = 'guardado' if ok else nota
                    C.resultado(item['titulo'], destino_ep['clave'],
                                'castellano recibido de la carpeta de entrada' if ok else 'carpeta de entrada: ' + nota, ok, nombre, clave_item)
                    log(item['titulo'], destino_ep['clave'], 'entrada:', nota, nombre)
                sub = 'procesados' if ok else 'con problemas'
                os.makedirs(os.path.join(base, sub), exist_ok=True)
                os.rename(ruta, os.path.join(base, sub, nombre))
                if ok and item['tipo'] == 'serie':
                    def temporada(c, t=destino_ep['temporada']):
                        it = c['items'].get(clave_item)
                        if it and t not in it.get('temporadas', []):
                            it['temporadas'] = sorted(set(it.get('temporadas', []) + [t]))
                    C.cambiar_cfg(temporada)
            except Exception as ex:
                estado[k] = f'error: {ex}'[:200]
                C.resultado(item['titulo'], destino_ep['clave'], 'carpeta de entrada: error', False, str(ex)[:300], clave_item)
            C.escribir(C.ESTADO, estado)
    C.escribir(C.ESTADO, estado)


# ---------------------------------------------------------------- mezclador
def inodos_protegidos():
    """(dispositivo, inodo) de todos los archivos de las carpetas protegidas: sus enlaces duros en la biblioteca no se tocan."""
    s = set()
    for d in C.PROTEGIDAS:
        for r, _, fs in os.walk(d):
            for f in fs:
                try:
                    st = os.lstat(os.path.join(r, f))
                except OSError:
                    continue
                s.add((st.st_dev, st.st_ino))
    return s


def a_papelera(item, clave, p, aj, tipo='video'):
    """Guarda el archivo que se va a sustituir con un enlace duro (no copia nada). Devuelve la ruta o None."""
    if not aj.get('papelera_dias'):
        return None
    d = os.path.join(C.PAPELERA, C.seguro(item['titulo']))
    os.makedirs(d, exist_ok=True)
    marca = time.strftime('%Y%m%d-%H%M%S')
    dst = os.path.join(d, f'{clave} · {marca} · {os.path.basename(p)}')
    os.link(p, dst)
    st = os.stat(p)
    C.escribir(dst + '.json', {'origen': p, 't': time.time(), 'fecha': time.strftime('%Y-%m-%d %H:%M'),
                               'ino': st.st_ino, 'size': st.st_size, 'clave': clave, 'item': C_k(item), 'tipo': tipo})
    return dst


def subtitulos_externos(p):
    base = os.path.splitext(p)[0]
    d = os.path.dirname(p)
    return [os.path.join(d, f) for f in os.listdir(d)
            if os.path.join(d, f).startswith(base + '.') and f.lower().endswith('.srt')]


def procesar(item, e, aj, estado, fuente=None, quitar_ids=(), motivo='automático'):
    """Mide, mezcla y sustituye un episodio. Escribe el resultado en 'estado'. Devuelve True si se mezcló."""
    p = e['path']
    es = C.ruta_audio(item, e['clave'])
    fuente = fuente or p
    st = os.stat(p)
    k = C.clave_archivo(p, st)
    ne = C.nombre_ep(item, e['clave'])
    tmp = os.path.join(os.path.dirname(p), f".{e['clave']}.duberr.tmp.mkv")
    huella = A.huella(aj)
    previo = C.estado_de(estado.get(k))
    era_mezcla = previo.get('r') == 'mezclado' or previo.get('era_mezcla')
    base = {'item': C_k(item), 'clave': e['clave'], 'fecha': time.strftime('%Y-%m-%d %H:%M'), 'aj': huella,
            'intentos': previo.get('intentos', 0) + 1 if previo.get('r') == 'error' else 1, 't': time.time()}
    if era_mezcla:                                   # si falla, el archivo sigue siendo la mezcla anterior
        base.update(era_mezcla=True, uid=previo.get('uid'), mezcla_txt=previo.get('mezcla_txt') or previo.get('txt'))
    C.vivo.update(trabajando=f'{ne}', paso='midiendo', desde=time.time())
    try:
        libre = shutil.disk_usage(os.path.dirname(p)).free
        if libre < os.stat(fuente).st_size * 1.05 + 2e9:
            raise RuntimeError(f'poco espacio libre ({libre / 1e9:.0f} GB)')
        j = N.info(fuente)
        refs = N.pistas_referencia(j, item.get('idioma_original'), aj.get('pista_ref'))
        refs = [r for r in refs if r not in quitar_ids]
        if not refs:
            estado[k] = {**base, 'r': 'sin_ref', 'txt': 'no hay pista con la que comparar'}
            return False
        log(ne, 'midiendo', os.path.basename(fuente), f'({motivo})')
        plan, fallos = None, []
        for ref in refs[:2]:
            try:
                plan = N.medir(fuente, ref, es, aj)
                break
            except N.Fallo as f:
                fallos.append(f'pista {ref}: {f}')
                log('   ', fallos[-1][:300])
        if plan is None:
            estado[k] = {**base, 'r': 'sin_sincronizar', 'txt': fallos[0][:400]}
            C.resultado(item['titulo'], e['clave'], 'no sincroniza: se queda sin castellano', False, fallos[0][:400], C_k(item))
            avisar(f'{ne}: castellano sin mezclar', 'No he podido sincronizarlo. ' + fallos[0][:200], 4, 'error')
            return False
        for d in plan['detalle']:
            log('   ', d)
        C.vivo['paso'] = 'mezclando'
        res = N.mezclar(fuente, es, plan, tmp, aj, quitar_ids)
        if os.stat(p).st_ino != st.st_ino:              # Sonarr lo cambió mientras tanto: empezar otra vez
            os.remove(tmp)
            return False
        # el archivo que se sustituye va a la papelera, salvo si ya es una mezcla de duberr (el original ya está)
        guardado = a_papelera(item, e['clave'], p, aj) if not era_mezcla else None
        srts = []
        if res['partes'] or fuente != p:                # subtítulos externos: ajustarlos al recorte (desde el original)
            for s in subtitulos_externos(p):
                try:
                    previo_srt = C.srt_guardado(item, e['clave'], os.path.basename(s))
                    with open(previo_srt or s, encoding='utf-8', errors='replace') as fh:
                        texto_srt = fh.read()
                    nuevo = N.remapear_srt(texto_srt, res['partes']) if res['partes'] else texto_srt
                    if not previo_srt and aj.get('papelera_dias') and res['partes']:
                        a_papelera(item, e['clave'], s, aj, tipo='srt')
                    with open(s + '.tmp', 'w', encoding='utf-8') as fh:
                        fh.write(nuevo)
                    os.replace(s + '.tmp', s)
                    srts.append(os.path.basename(s))
                except Exception as ex:
                    log('no pude ajustar', s, ex)
        os.rename(tmp, p)                                # nuevo inodo; los demás enlaces duros no se tocan
        st2 = os.stat(p)
        js = N.info(p)
        uid = next((t['properties'].get('uid') for t in js['tracks'] if t['type'] == 'audio'), None)
        piezas = [(t, round(a, 2), round(b, 2)) for t, a, b, _ in plan['piezas']]
        quitado = res.get('quitado_s', 0)
        txt = N.NOMBRE_VEL[plan['velocidad']] + f" · desfase {plan['tramos'][0][1]:+.2f} s"
        if len(plan['tramos']) > 1:
            txt += f" · {len(plan['tramos']) - 1} corte{'s' if len(plan['tramos']) > 2 else ''}"
        orig = [(a, b) for t, a, b in piezas if t == 'orig' and b - a > N.RELLENO_MIN]
        if quitado:
            txt += f' · {quitado:.0f} s sin castellano recortados'
        elif orig:
            txt += f" · {sum(b - a for a, b in orig):.0f} s con audio original"
        estado[C.clave_archivo(p, st2)] = {
            **base, 'r': 'mezclado', 'txt': txt, 'uid': uid, 'velocidad': plan['velocidad'], 'tempo': round(plan['tempo'], 7),
            'tramos': [(round(t, 2), round(l, 3)) for t, l in plan['tramos']], 'piezas': piezas, 'd': round(plan['d_video'], 1),
            'partes': [(round(a, 2), round(b, 2)) for a, b in res['partes']] if res['partes'] else None, 'quitado': quitado,
            'calidad': plan['calidad'], 'ganancia_db': round(plan.get('ganancia_db', 0), 1), 'ref': plan['ref'],
            'recodificado': res['recodificado'], 'comprobacion': {x: res['comprobacion'].get(x) for x in ('bien', 'mal', 'omitida')},
            'papelera': guardado, 'srt': srts, 'motivo': motivo}
        estado[k] = {**base, 'r': 'sustituido', 'txt': 'sustituido por la versión con castellano'}
        detalle = f"calidad {e['calidad']} · {plan['detalle'][-1]}"
        C.resultado(item['titulo'], e['clave'], 'castellano mezclado · ' + txt, True, detalle, C_k(item))
        log(ne, 'mezclado', txt)
        avisar(f'{ne}: castellano añadido', txt + '.', 2, 'episodio')
        reescanear(item)
        return True
    except Exception as ex:
        if os.path.exists(tmp):
            os.remove(tmp)
        traceback.print_exc()
        estado[k] = {**base, 'r': 'error', 'txt': str(ex)[:400]}
        C.resultado(item['titulo'], e['clave'], 'error al mezclar', False, str(ex)[:400], C_k(item))
        avisar(f'{ne}: error al mezclar', str(ex)[:300], 4, 'error')
        return False
    finally:
        C.vivo.update(trabajando='', paso='')
        C.escribir(C.ESTADO, estado)


def toca(previo, aj):
    """¿Hay que (re)intentar este archivo? Nuevo: sí. Error: con espera creciente. Sin sincronizar: si cambian los
    ajustes. Mezclado, origen, deshecho...: no."""
    if not previo:
        return True
    v = C.estado_de(previo)
    r = v.get('r')
    if v.get('era_mezcla'):                             # falló un «rehacer»: sigue la mezcla anterior, no se insiste solo
        return False
    if r == 'sustituido':                               # vuelve a estar en la biblioteca (se deshizo a mano)
        return True
    if r == 'error':
        n = v.get('intentos', 1)
        return n < REINTENTOS and time.time() - v.get('t', 0) > 3600 * n
    if r == 'sin_sincronizar':                          # también los de la versión anterior (sin huella): una vez
        return v.get('aj') != A.huella(aj)
    return False


def mezclar_pendientes():
    c = C.cfg()
    estado = C.leer(C.ESTADO, {})
    protegidos = inodos_protegidos()
    for clave_item, item in c['items'].items():
        if item.get('fase') == 'pausado':
            continue
        try:
            eps = C.episodios(item)
        except Exception as ex:
            log(item['titulo'], 'no puedo leer episodios', ex)
            continue
        for e in eps:
            p = e['path']
            es = C.ruta_audio(item, e['clave'])
            if not p or not os.path.exists(p) or not os.path.exists(es):
                continue
            st = os.stat(p)
            k = C.clave_archivo(p, st)
            if (st.st_dev, st.st_ino) in protegidos or C.esta_protegido(p) or time.time() - st.st_ctime < 300:
                continue                                  # protegido (solo lectura) o recién importado
            aj = A.efectivos(c.get('ajustes'), item, (item.get('eps') or {}).get(e['clave'], {}).get('ajustes'))
            if not toca(estado.get(k), aj):
                continue
            meta = C.leer(os.path.splitext(es)[0] + '.json', {})
            if meta.get('origen') == os.path.basename(p) or 'Spanish' in e['idiomas']:
                estado[k] = {'r': 'origen', 'txt': 'es el archivo del que salió el castellano o ya está en castellano'}
                continue
            j = N.info(p)
            if N.tiene_castellano(j):
                estado[k] = {'r': 'ya_spa', 'txt': 'ya tiene castellano'}
                continue
            procesar(item, e, aj, estado)
            if C.acciones_pendientes():                  # lo pedido desde la web va antes
                C.escribir(C.ESTADO, estado)
                return
    C.escribir(C.ESTADO, estado)


# ---------------------------------------------------------------- acciones pedidas desde la web
def hacer_acciones():
    while True:
        acc = C.tomar_accion()                       # de una en una: si se reinicia, el resto sigue en la cola
        if not acc:
            return
        c = C.cfg()
        item = c['items'].get(acc['item'])
        if not item:
            C.accion_hecha()
            continue
        try:
            C.vivo['cola'] = len(C.acciones_pendientes())
            e = next((x for x in C.episodios(item) if x['clave'] == acc['clave']), None)
            if not e or not e['path'] or not os.path.exists(e['path']):
                C.resultado(item['titulo'], acc['clave'], f"{acc['accion']}: no hay archivo", False, '', acc['item'])
                continue
            if acc.get('ajustes') is not None:           # ajustes del episodio: se guardan para la próxima vez también
                def guarda(c2):
                    it = c2['items'].get(acc['item'])
                    if it is not None:
                        eps = it.setdefault('eps', {})
                        if acc['ajustes']:
                            eps.setdefault(acc['clave'], {})['ajustes'] = acc['ajustes']
                        else:
                            eps.pop(acc['clave'], None)
                C.cambiar_cfg(guarda)
            ajg = c.get('ajustes')
            aj = A.efectivos(ajg, item, acc.get('ajustes') or (item.get('eps') or {}).get(acc['clave'], {}).get('ajustes'))
            estado = C.leer(C.ESTADO, {})
            k = C.clave_archivo(e['path'])
            actual = C.estado_de(estado.get(k))
            originales = C.papelera_de(item, acc['clave'])
            if acc['accion'] in ('reintentar', 'rehacer'):
                if not os.path.exists(C.ruta_audio(item, acc['clave'])):
                    C.resultado(item['titulo'], acc['clave'], 'no hay castellano guardado para este episodio', False, '', acc['item'])
                    continue
                propia = actual.get('r') == 'mezclado' or actual.get('era_mezcla')
                if propia or tiene_spa(e['path']):
                    if originales:                       # rehacer desde el original guardado
                        procesar(item, e, aj, estado, fuente=originales[0][0], motivo='rehecho desde el original')
                    elif propia:                         # rehacer quitando el castellano que puso duberr
                        js = N.info(e['path'])
                        mio = [t['id'] for t in js['tracks'] if t['type'] == 'audio' and
                               (t['properties'].get('uid') == actual.get('uid') or
                                (actual.get('uid') is None and N.es_spa(t) and t['properties'].get('track_name') == 'Castellano'))][:1]
                        procesar(item, e, aj, estado, quitar_ids=tuple(mio), motivo='rehecho')
                    else:
                        C.resultado(item['titulo'], acc['clave'], 'ya tiene castellano propio: no lo toco', False, '', acc['item'])
                else:
                    estado.pop(k, None)
                    procesar(item, e, aj, estado, motivo='reintento pedido')
            elif acc['accion'] == 'deshacer':
                deshacer(item, e, estado, actual, originales)
        except Exception as ex:
            traceback.print_exc()
            C.resultado(item['titulo'], acc['clave'], f"{acc['accion']}: error", False, str(ex)[:300], acc['item'])
        finally:
            C.accion_hecha()


def deshacer(item, e, estado, actual, originales):
    p = e['path']
    tmp = os.path.join(os.path.dirname(p), f".{e['clave']}.duberr.tmp.mkv")
    if originales:
        orig, meta = originales[0]
        os.link(orig, tmp)
        os.rename(tmp, p)
        texto = f"devuelto el original del {meta.get('fecha', '?')}"
        for s in subtitulos_externos(p):                 # subtítulos externos que se ajustaron al recorte
            guardado = C.srt_guardado(item, e['clave'], os.path.basename(s))
            if guardado:
                shutil.copyfile(guardado, s + '.tmp')
                os.replace(s + '.tmp', s)
    elif actual.get('r') == 'mezclado' or actual.get('era_mezcla'):
        js = N.info(p)
        aud = [t for t in js['tracks'] if t['type'] == 'audio']
        mio = [t['id'] for t in aud if t['properties'].get('uid') == actual.get('uid')] or \
              [t['id'] for t in aud if N.es_spa(t) and t['properties'].get('track_name') == 'Castellano'][:1]
        resto = [t['id'] for t in aud if t['id'] not in mio]
        if not mio or not resto:
            C.resultado(item['titulo'], e['clave'], 'deshacer: no encuentro la pista que puso duberr', False, '', C_k(item))
            return
        cmd = ['mkvmerge', '-q', '-o', tmp, '-a', ','.join(map(str, resto)), '--default-track-flag', f'{resto[0]}:1', p]
        r = N.subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode not in (0, 1):
            if os.path.exists(tmp):
                os.remove(tmp)
            raise RuntimeError('mkvmerge: ' + r.stdout[-300:])
        os.rename(tmp, p)
        texto = 'quitada la pista en castellano' + (' (lo recortado no se puede recuperar: no había original en la papelera)' if actual.get('partes') else '')
    else:
        C.resultado(item['titulo'], e['clave'], 'deshacer: no hay nada que deshacer', False, '', C_k(item))
        return
    estado[C.clave_archivo(p)] = {'r': 'deshecho', 'txt': texto, 'fecha': time.strftime('%Y-%m-%d %H:%M'), 't': time.time(),
                                  'item': C_k(item), 'clave': e['clave']}
    C.escribir(C.ESTADO, estado)
    C.resultado(item['titulo'], e['clave'], 'deshecho: ' + texto, True, '', C_k(item))
    log(C.nombre_ep(item, e['clave']), 'deshecho:', texto)
    reescanear(item)


def purgar_papelera():
    dias = A.efectivos(generales()).get('papelera_dias', 14)
    if not os.path.isdir(C.PAPELERA):
        return
    limite = time.time() - dias * 86400
    for r, _, fs in os.walk(C.PAPELERA):
        for f in fs:
            if not f.endswith('.json'):
                continue
            meta = C.leer(os.path.join(r, f), {})
            if meta.get('t', time.time()) < limite:
                for x in (os.path.join(r, f[:-5]), os.path.join(r, f)):
                    if os.path.exists(x):
                        os.remove(x)
                log('papelera: caducado', f[:-5])


# ---------------------------------------------------------------- bucle de trabajo
def migrar():
    """Formato antiguo de config.json: buscar_ya -> buscar_pedido/buscar_hecho."""
    def fn(c):
        for it in c['items'].values():
            if 'buscar_ya' in it:
                it['buscar_hecho'] = it.get('buscar_hecho', 0)
                it['buscar_pedido'] = it['buscar_hecho'] + (1 if it.pop('buscar_ya') else 0)
            for x in [x for x in it if x.startswith('_web_')]:
                it.pop(x)
    C.cambiar_cfg(fn)


def trabajador():
    ultima_purga = 0
    migrar()
    a = C.recuperar_accion()
    if a:
        log('acción a medias recuperada:', a['clave'], a['accion'])
    while True:
        try:
            ids = {'sonarr': asegurar_perfiles(sonarr), 'radarr': asegurar_perfiles(radarr)}
            hacer_acciones()
            try:
                C.vivo.update(trabajando='Revisando la carpeta de entrada', paso='')
                procesar_entrada()
            except Exception as ex:
                log('error en la carpeta de entrada:', ex)
                traceback.print_exc()
            for clave, antes in list(C.cfg()['items'].items()):
                item = json.loads(json.dumps(antes))
                try:
                    C.vivo.update(trabajando=f"Revisando {item['titulo']}", paso='')
                    orquestar(item, ids['sonarr' if item['tipo'] == 'serie' else 'radarr'])
                    item.pop('error', None)
                except Exception as ex:
                    item['error'] = str(ex)[:200]
                    log(item['titulo'], 'error en el orquestador', ex)
                    traceback.print_exc()
                guardar_item(clave, antes, item)
            C.vivo.update(trabajando='', paso='')
            hacer_acciones()
            mezclar_pendientes()
            if time.time() - ultima_purga > 6 * 3600:
                purgar_papelera()
                ultima_purga = time.time()
            if len(cache_info) > 5000:
                cache_info.clear()
        except OSError as ex:                           # Sonarr/Radarr no responden: se reintenta en la próxima pasada
            log('no puedo hablar con Sonarr/Radarr:', C.sin_claves(ex))
        except Exception as ex:
            log('error en la pasada:', C.sin_claves(ex))
            traceback.print_exc()
        C.vivo.update(trabajando='', paso='', ultima_pasada=time.strftime('%H:%M'))
        if not C.acciones_pendientes():
            C.despertar.wait(INTERVALO)
        C.despertar.clear()


if __name__ == '__main__':
    os.makedirs(C.AUDIO, exist_ok=True)
    log(f'duberr {C.VERSION} en marcha · web en {web.BIND}:{web.PUERTO} · revisión cada {INTERVALO} s · medios {C.MEDIA} · datos {C.DATOS}')
    log('carpetas protegidas:', ', '.join(C.PROTEGIDAS) or 'ninguna', '· mapeo de rutas:', len(mapeo()), 'regla(s)')
    for tipo in (sonarr, radarr):
        if not tipo.ok():
            log(f'AVISO: falta {tipo.tipo.upper()}_KEY: sin ella duberr no puede hablar con {tipo.tipo.capitalize()}')
    for r, _, fs in os.walk(C.MEDIA):                 # restos de una mezcla cortada (reinicio)
        if r.startswith(C.PAPELERA):
            continue
        for f in fs:
            if f.endswith(('.duberr.tmp.mkv', '.duberr-tempo.tmp.mka', '.duberr-audio.tmp.mka', '.audio-es.tmp.mkv',
                           '.audio-es-tempo.tmp.mka')) or re.search(r'\.duberr\.tmp-\d+\.mkv$', f):
                os.remove(os.path.join(r, f))
                log('borrado temporal a medias', f)
    threading.Thread(target=trabajador, daemon=True).start()
    web.servir()
