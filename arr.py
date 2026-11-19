"""duberr · cliente mínimo de Sonarr y Radarr (API v3) y utilidades de entorno.

Las claves y direcciones vienen del entorno (SONARR_URL, SONARR_KEY, RADARR_URL, RADARR_KEY).
Mapeo de rutas (DUBERR_PATH_MAP): si Sonarr/Radarr ven la biblioteca en otra ruta que duberr, todas las rutas que
devuelven (archivos, cola, importación manual) se traducen a las de duberr, y las que se les envían, a las suyas.
"""
import json, os, re, urllib.request, urllib.parse


def env(*nombres, defecto=''):
    """Primer valor no vacío entre las variables dadas (la primera es la actual; las siguientes, nombres heredados)."""
    for n in nombres:
        v = os.environ.get(n)
        if v:
            return v
    return defecto


# ---------------------------------------------------------------- mapeo de rutas
CLAVES_RUTA = ('path', 'outputPath', 'rootFolderPath', 'folder')   # campos de la API que llevan una ruta de disco
_mapa_cache = (None, [])


def _es_win(s):
    return '\\' in s or re.match(r'^[A-Za-z]:', s) is not None


def mapeo(texto=None):
    """«remoto=>local» separados por «;» (o saltos de línea) -> [(remoto, local)]. Sin barra final."""
    global _mapa_cache
    if texto is None:
        texto = os.environ.get('DUBERR_PATH_MAP', '')
    if _mapa_cache[0] == texto:
        return _mapa_cache[1]
    pares = []
    for linea in re.split(r'[\n;]', texto or ''):
        if '=>' in linea:
            a, b = (x.strip() for x in linea.split('=>', 1))
            if a and b:
                pares.append((a.rstrip('/\\'), b.rstrip('/')))
    _mapa_cache = (texto, pares)
    return pares


def _resto(p, prefijo):
    """Lo que queda de p tras quitar el prefijo (con «/» como separador), o None si p no está dentro de él."""
    if _es_win(prefijo):                                   # rutas de Windows: sin distinguir mayúsculas ni \ de /
        pn, qn = p.replace('\\', '/'), prefijo.replace('\\', '/')
        if pn.lower() == qn.lower():
            return ''
        return pn[len(qn):] if pn.lower().startswith(qn.lower() + '/') else None
    if p == prefijo:
        return ''
    if prefijo == '':                                      # «/» como raíz
        return p if p.startswith('/') else None
    return p[len(prefijo):] if p.startswith(prefijo + '/') else None


def ruta_local(p, texto=None):
    """Ruta como la ve Sonarr/Radarr -> ruta como la ve duberr. Si ningún mapeo encaja, queda igual."""
    if not isinstance(p, str) or not p:
        return p
    for remoto, local in sorted(mapeo(texto), key=lambda x: -len(x[0])):
        r = _resto(p, remoto)
        if r is not None:
            return local + r
    return p


def ruta_remota(p, texto=None):
    """Ruta como la ve duberr -> ruta como la ve Sonarr/Radarr (la inversa de ruta_local)."""
    if not isinstance(p, str) or not p:
        return p
    for remoto, local in sorted(mapeo(texto), key=lambda x: -len(x[1])):
        r = _resto(p, local)
        if r is not None:
            return remoto + (r.replace('/', '\\') if _es_win(remoto) else r)
    return p


def _recorrer(obj, fn):
    """Aplica fn a todos los valores de texto cuyo campo sea una ruta (en listas y diccionarios anidados)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in CLAVES_RUTA and isinstance(v, str):
                obj[k] = fn(v)
            elif isinstance(v, (dict, list)):
                _recorrer(v, fn)
    elif isinstance(obj, list):
        for x in obj:
            _recorrer(x, fn)
    return obj


class Arr:
    def __init__(self, tipo):
        self.tipo = tipo                                   # 'sonarr' | 'radarr'
        self.url = env(tipo.upper() + '_URL', defecto=f'http://{tipo}:{8989 if tipo == "sonarr" else 7878}').rstrip('/')
        self.key = env(tipo.upper() + '_KEY')
        self._spa = None

    def ok(self):
        return bool(self.key)

    def _req(self, metodo, ruta, datos=None, crudo=False, **q):
        """crudo=True: sin traducir rutas (para leer un objeto y devolverlo tal cual con PUT)."""
        if not crudo:
            if 'folder' in q:
                q['folder'] = ruta_remota(q['folder'])
            if datos is not None and metodo == 'POST':
                datos = _recorrer(json.loads(json.dumps(datos)), ruta_remota)
        url = f'{self.url}/api/v3/{ruta}' + ('?' + urllib.parse.urlencode(q) if q else '')
        cuerpo = json.dumps(datos).encode() if datos is not None else None
        req = urllib.request.Request(url, data=cuerpo, method=metodo,
                                     headers={'X-Api-Key': self.key, 'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=60) as r:
            t = r.read()
            res = json.loads(t) if t else None
        return res if crudo else _recorrer(res, ruta_local)

    def get(self, ruta, crudo=False, **q):
        return self._req('GET', ruta, crudo=crudo, **q)

    def post(self, ruta, datos):
        return self._req('POST', ruta, datos)

    def put(self, ruta, datos):
        """Se envía tal cual: el objeto debe venir de get(..., crudo=True) para no alterar sus rutas."""
        return self._req('PUT', ruta, datos, crudo=True)

    # --- perfiles ---
    def perfiles(self):
        return self.get('qualityprofile')

    def perfil_por_nombre(self, nombre):
        return next((p for p in self.perfiles() if p['name'] == nombre), None)

    def comando(self, nombre, **kw):
        return self.post('command', {'name': nombre, **kw})

    # --- idiomas ---
    def idioma_spanish(self):
        """{'id', 'name'} del idioma «Spanish» de esta instalación (se pregunta a la API; el 3 fijo es el último recurso)."""
        if self._spa is None:
            try:
                for x in self.get('language', crudo=True) or []:
                    if (x.get('name') or '').strip().lower() == 'spanish':
                        self._spa = {'id': x['id'], 'name': x['name']}
                        break
                else:
                    self._spa = {'id': 3, 'name': 'Spanish'}
            except Exception:
                return {'id': 3, 'name': 'Spanish'}       # sin respuesta: no se guarda, se vuelve a preguntar
        return self._spa


sonarr, radarr = Arr('sonarr'), Arr('radarr')
