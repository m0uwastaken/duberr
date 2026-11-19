"""duberr · núcleo: medir dónde encaja el castellano en el vídeo, montar la pista y mezclarla.

Cómo mide (versión 3):
  * Decodifica entera la pista de referencia del vídeo (mejor el idioma original) y el castellano, a 16 kHz, y saca
    cada 10 ms la energía de 8 bandas de frecuencia. Lo que se compara es el «ataque» de cada banda (cuándo sube el
    sonido): la música y los efectos son comunes a los dos doblajes aunque las voces no lo sean.
  * Correlación por bandas con blanqueo parcial (PHAT 0,5). Frente a la energía total de antes, acierta 17-19 de
    19 ventanas donde antes acertaba 3-7, y los picos son el doble de nítidos.
  * 1.ª pasada: ventanas de 30 s con margen amplio -> desfases candidatos y velocidad (normal, 25->23,976...).
  * 2.ª pasada: cada 5 s se puntúa cada candidato y un Viterbi elige el camino: tramos, cada uno con su desfase.
  * Afinado: desfase de cada tramo, deriva de velocidad que quede (p. ej. 24 frente a 23,976) y punto exacto de cada
    corte. De ahí sale qué parte del vídeo no tiene castellano (se rellena con el original o se recorta del vídeo)
    y qué parte del castellano sobra (no está en el vídeo: se salta).
  * Si la pista «castellana» es casi idéntica a la original, no es castellano (mal etiquetada): no se mezcla.

Seguridad (archivos con enlaces duros desde otras carpetas):
  * Nunca escribe sobre un archivo existente: crea un .tmp en la misma carpeta y lo renombra encima.
  * Antes de mezclar se mide; después se vuelve a medir el resultado. Si algo no cuadra, no se sustituye nada.
"""
import json, os, re, subprocess, time, urllib.request
import numpy as np

from arr import env

SR, HOP, NFFT = 16000, 160, 512
FPS = SR // HOP                     # 100 rasgos por segundo (10 ms)
BORDES = (60, 150, 300, 600, 1200, 2400, 4000, 5600, 7900)
BETA = 0.5                          # blanqueo parcial de la correlación

VELOCIDADES = {'1': 1.0, '25->23.976': 24000 / 1001 / 25, '23.976->25': 25 / (24000 / 1001)}
NOMBRE_VEL = {'1': 'velocidad normal', '25->23.976': '25 fps corregido', '23.976->25': '23,976 fps corregido'}
TOL_CAND = 4                        # frames (40 ms): desfases más separados son candidatos distintos
DERIVA_MIN = 12                     # frames (120 ms): deriva total a partir de la que se corrige la velocidad
RELLENO_MIN = 1.5                   # s: huecos más cortos se dejan tal cual (copia sin recodificar)

# Exigencia: z = calidad de un pico (desviaciones sobre el ruido). El ruido rara vez pasa de 6.
EXIGENCIA = {
    'alta':   {'z': 9.0, 'z_solo': 14.0, 'min_fuertes': 6, 'penal': 30.0, 'verif_z': 9.0, 'verif_min': 5},
    'normal': {'z': 7.0, 'z_solo': 12.0, 'min_fuertes': 4, 'penal': 22.0, 'verif_z': 7.0, 'verif_min': 3},
    'baja':   {'z': 6.0, 'z_solo': 10.0, 'min_fuertes': 3, 'penal': 15.0, 'verif_z': 6.0, 'verif_min': 2},
}
DEFECTO = {'busca_s': 600, 'velocidad': 'auto', 'exigencia': 'normal', 'max_cortes': 8, 'tono': 'mantener',
           'sin_castellano': 'rellenar', 'recorte_min_s': 4.0, 'recorte_ajuste': 'conservar', 'comprobacion': 'normal', 'codec': 'ac3',
           'pistas': 'todas', 'fundido_ms': 15, 'igualar_volumen': True, 'subs_forzados': True, 'pista_ref': 'auto',
           'desfase_manual': None, 'ajuste_fino_s': 0.0}


def log(*a):
    print(time.strftime('%Y-%m-%d %H:%M:%S'), *a, flush=True)


def avisar(titulo, mensaje, prioridad=3):
    url = env('NTFY_URL', defecto='https://ntfy.sh').rstrip('/')
    topic = env('NTFY_TOPIC')
    token = env('NTFY_TOKEN')
    log('AVISO', titulo, '|', mensaje)
    if not topic:
        return
    req = urllib.request.Request(url, data=json.dumps({'topic': topic, 'title': titulo, 'message': mensaje,
                                 'tags': ['tv'], 'priority': prioridad}).encode(), headers={'Content-Type': 'application/json'})
    if token:
        req.add_header('Authorization', 'Bearer ' + token)
    try:
        urllib.request.urlopen(req, timeout=20).read()
    except Exception as e:
        log('ntfy falló:', e)


def progreso(texto):
    """Lo cambia duberr.py para enseñar en la web por qué paso va."""


class Fallo(Exception):
    """No se puede sincronizar con fiabilidad (no es un error del programa)."""


# ------------------------------------------------------------------------------------------------ archivos y pistas
def info(p):
    j = json.loads(subprocess.run(['mkvmerge', '-J', p], capture_output=True, text=True, check=True).stdout)
    props = j.setdefault('container', {}).setdefault('properties', {})
    if not props.get('duration'):                 # MP4/AVI: mkvmerge no siempre da la duración
        r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', p],
                           capture_output=True, text=True)
        try:
            props['duration'] = int(float(r.stdout.strip()) * 1e9)
        except ValueError:
            pass
    return j


def duracion(j):
    return j['container']['properties']['duration'] / 1e9


def _p(t, k, d=None):
    return t.get('properties', {}).get(k, d)


_LATINO = re.compile(r'latin|\blat\b|\blat\.|latam|hispanoam|m[eé]xic|neutro|es-(419|mx|ar|co|cl|pe|ve|us)', re.I)
_COMENT = re.compile(r'coment|comment|descrip|audiodesc|\bad\b', re.I)
IDIOMAS = {'English': ('eng', 'en'), 'Japanese': ('jpn', 'ja'), 'French': ('fre', 'fra', 'fr'), 'German': ('ger', 'deu', 'de'),
           'Italian': ('ita', 'it'), 'Korean': ('kor', 'ko'), 'Chinese': ('chi', 'zho', 'zh'), 'Portuguese': ('por', 'pt'),
           'Russian': ('rus', 'ru'), 'Danish': ('dan', 'da'), 'Swedish': ('swe', 'sv'), 'Norwegian': ('nor', 'nob', 'no'),
           'Dutch': ('dut', 'nld', 'nl'), 'Hindi': ('hin', 'hi'), 'Turkish': ('tur', 'tr'), 'Polish': ('pol', 'pl'),
           'Hebrew': ('heb', 'he'), 'Persian': ('per', 'fas', 'fa'), 'Arabic': ('ara', 'ar'), 'Thai': ('tha', 'th'),
           'Finnish': ('fin', 'fi'), 'Icelandic': ('ice', 'isl', 'is'), 'Catalan': ('cat', 'ca')}


def es_spa(t):
    return _p(t, 'language') in ('spa', 'es') or (_p(t, 'language_ietf') or '').lower().startswith('es')


def es_latino(t):
    return bool(_LATINO.search(f"{_p(t, 'language_ietf') or ''} {_p(t, 'track_name') or ''}"))


def es_comentario(t):
    return bool(_COMENT.search(_p(t, 'track_name') or '')) or bool(_p(t, 'flag_commentary')) or \
        bool(_p(t, 'flag_visual_impaired'))


def pistas_castellano(j):
    return [t for t in j['tracks'] if t['type'] == 'audio' and es_spa(t) and not es_latino(t) and not es_comentario(t)]


def tiene_castellano(j):
    return bool(pistas_castellano(j))


def pistas_referencia(j, original=None, preferida='auto'):
    """Pistas con las que comparar, de mejor a peor: idioma original, inglés, otras (nunca castellano ni comentarios)."""
    aud = [t for t in j['tracks'] if t['type'] == 'audio' and not es_comentario(t)]
    cand = [t for t in aud if not (es_spa(t) and not es_latino(t))]
    if not cand:
        return []
    cod = set(IDIOMAS.get(original or '', ()))
    if preferida not in ('', 'auto', None):
        cod = {preferida}

    def orden(t):
        l = _p(t, 'language') or 'und'
        return (0 if l in cod else 1 if l in ('eng', 'en') else 2 if l not in ('und', '') else 3,
                0 if not es_spa(t) else 1, cand.index(t))
    return [t['id'] for t in sorted(cand, key=orden)]


def pista_referencia(j, original=None):
    r = pistas_referencia(j, original)
    return r[0] if r else None


def nombre_pista(j, tid):
    t = next((t for t in j['tracks'] if t['id'] == tid), {})
    return f"{_p(t, 'language') or 'und'} {t.get('codec', '')} {_p(t, 'audio_channels') or ''}ch".strip()


# ------------------------------------------------------------------------------------------------ rasgos de audio
def _bandas(cmd):
    """Ejecuta ffmpeg (f32le mono a SR por stdout) y devuelve la energía de cada banda cada 10 ms: (T, 8) float32."""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    win = np.hanning(NFFT).astype(np.float32)
    f = np.fft.rfftfreq(NFFT, 1 / SR)
    idx = [(int(np.searchsorted(f, a)), int(np.searchsorted(f, b))) for a, b in zip(BORDES, BORDES[1:])]
    trozos, resto = [], np.zeros(0, np.float32)
    while True:
        b = p.stdout.read(SR * 4 * 60)
        if not b:
            break
        x = np.concatenate([resto, np.frombuffer(b[:len(b) // 4 * 4], np.float32)])
        n = (len(x) - NFFT) // HOP + 1
        if n <= 0:
            resto = x
            continue
        fr = np.lib.stride_tricks.as_strided(x, (n, NFFT), (4 * HOP, 4))
        P = np.abs(np.fft.rfft(fr * win, axis=1)) ** 2
        trozos.append(np.stack([P[:, a:c].sum(1) for a, c in idx], 1).astype(np.float32))
        resto = x[n * HOP:].copy()
    err = p.stderr.read()
    p.wait()
    if p.returncode != 0:
        raise RuntimeError('ffmpeg: ' + err.decode(errors='replace')[-300:])
    return np.concatenate(trozos) if trozos else np.zeros((0, len(idx)), np.float32)


def bandas_pista(ruta, mapa, tempo=1.0, inicio=None, largo=None):
    af = (f'atempo={tempo:.8f},' if abs(tempo - 1) > 1e-9 else '') + f'aresample={SR}'
    cmd = ['ffmpeg', '-v', 'error', '-nostdin']
    if inicio is not None:
        cmd += ['-ss', f'{max(0.0, inicio):.3f}']
    if largo is not None:
        cmd += ['-t', f'{largo:.3f}']
    return _bandas(cmd + ['-i', ruta, '-map', mapa, '-af', af, '-ac', '1', '-f', 'f32le', '-'])


def ataques(B):
    """Ataque por banda (subida de energía en dB), normalizado: lo que se compara."""
    L = np.log10(B + 1e-10)
    L = np.maximum(L, np.percentile(L, 99.9, axis=0) - 8)
    d = np.diff(L, axis=0, prepend=L[:1])
    np.maximum(d, 0, out=d)
    d -= d.mean(0)
    d /= d.std(0) + 1e-9
    return d.astype(np.float32)


def _xcorr(ref, otra):
    """Correlación de ref (W, B) a lo largo de otra (L, B), sumada por bandas. Índice j = ref empieza en otra[j]."""
    n = 1 << int(np.ceil(np.log2(len(ref) + len(otra))))
    X = np.fft.rfft(otra, n, axis=0) * np.conj(np.fft.rfft(ref, n, axis=0))
    X /= np.abs(X) ** BETA + 1e-9
    return np.fft.irfft(X, n, axis=0)[:len(otra) - len(ref) + 1].sum(1)


def _z(c, excluir, ancho=50):
    m = np.ones(len(c), bool)
    for j in excluir:
        m[max(0, j - ancho):j + ancho] = False
    r = c[m] if m.sum() > 100 else c
    return (c - r.mean()) / (r.std() + 1e-9)


def _parabola(c, i):
    if 0 < i < len(c) - 1:
        a, b, d = c[i - 1], c[i], c[i + 1]
        den = a - 2 * b + d
        if den < 0:
            return i + 0.5 * (a - d) / den
    return float(i)


def _trozo(S, a, b):
    """S[a:b] rellenando con ceros lo que cae fuera."""
    out = np.zeros((b - a, S.shape[1]), np.float32)
    i, j = max(0, a), min(len(S), b)
    if j > i:
        out[i - a:j - a] = S[i:j]
    return out


# ------------------------------------------------------------------------------------------------ medición
def _media2(X):
    """Rasgos a 50 Hz (máximo de cada par): la búsqueda gruesa va el doble de rápida."""
    n = len(X) // 2
    return np.maximum(X[0:2 * n:2], X[1:2 * n:2])


def _etapa1(E, S, busca, W=1500, H=750):
    """Búsqueda gruesa (50 Hz): ventanas de 30 s cada 15 s con margen ±busca. Devuelve [(t0, desfase, z)] en frames
    de 10 ms (desfase = frame del castellano - frame del vídeo)."""
    E2, S2 = _media2(E), _media2(S)
    b2 = busca // 2
    picos = []
    for t0 in range(0, max(1, len(E2) - W + 1), H):
        ref = E2[t0:t0 + W]
        if len(ref) < W or ref.std() < 0.2:
            continue
        a = t0 - b2
        c = _xcorr(ref, _trozo(S2, a, t0 + W + b2))
        zs = _z(c, [int(np.argmax(c))], 25)
        for _ in range(3):
            i = int(np.argmax(zs))
            if zs[i] < 4.5:
                break
            picos.append((2 * t0, 2 * (a + _parabola(c, i) - t0), float(zs[i])))
            zs[max(0, i - 50):i + 50] = -1e9
    return picos


def _recta(picos, z, n_frames):
    """Pendiente (frames por frame) si los picos fiables siguen una recta inclinada mejor que una horizontal."""
    P = np.array([(t0 + 1500, lag) for t0, lag, q in picos if q >= z], float)
    if len(P) < 6:
        return 0.0
    plano = max(int((np.abs(P[:, 1] - y) <= 8).sum()) for y in P[:, 1])
    rng = np.random.default_rng(0)
    mejor, m_mejor, inl_mejor = 0, 0.0, None
    for _ in range(400):
        i, j = rng.choice(len(P), 2, replace=False)
        if abs(P[i, 0] - P[j, 0]) < 6000:
            continue
        m = (P[j, 1] - P[i, 1]) / (P[j, 0] - P[i, 0])
        if abs(m) > 0.003:
            continue
        inl = np.abs(P[:, 1] - (P[i, 1] + m * (P[:, 0] - P[i, 0]))) <= 8
        if inl.sum() > mejor:
            mejor, m_mejor, inl_mejor = int(inl.sum()), m, inl
    if mejor >= 6 and mejor >= 0.6 * len(P) and mejor > 1.3 * plano and abs(m_mejor) * n_frames >= DERIVA_MIN:
        return float(np.polyfit(P[inl_mejor, 0], P[inl_mejor, 1], 1)[0])
    return 0.0


def _candidatos(picos, ex):
    """Desfases que se repiten (o uno solo muy claro). El ruido casi nunca cae dos veces en el mismo sitio."""
    fuertes = sorted([p for p in picos if p[2] >= 5.0], key=lambda p: p[1])
    grupos = []
    for p in fuertes:
        if grupos and p[1] - grupos[-1][-1][1] <= TOL_CAND:
            grupos[-1].append(p)
        else:
            grupos.append([p])
    cands = []
    for g in grupos:
        ventanas = {p[0] for p in g}
        qmax = max(p[2] for p in g)
        if (len(ventanas) >= 2 and qmax >= ex['z']) or qmax >= ex['z_solo']:
            o = sorted(g, key=lambda p: p[1])
            pesos = np.cumsum([p[2] for p in o])
            lag = o[int(np.searchsorted(pesos, pesos[-1] / 2))][1]
            cands.append({'lag': lag, 'apoyo': sum(p[2] for p in g), 'ventanas': len(ventanas), 'qmax': qmax})
    cands.sort(key=lambda c: -c['apoyo'])
    return cands[:10]


def _cobertura(picos, cands, z):
    return len({p[0] for p in picos if p[2] >= z and any(abs(p[1] - c['lag']) <= 8 for c in cands)})


def _etapa2(E, S, lags, W=2000, H=500):
    """Cada 5 s, ventana de 20 s: z de cada candidato y su desfase fino. Devuelve t0s, Z (n, K), F (n, K)."""
    lags_i = [int(round(l)) for l in lags]
    lo, hi = min(lags_i) - 3000, max(lags_i) + 3000
    t0s, Z, F = [], [], []
    for t0 in range(0, max(1, len(E) - W // 2), H):
        ref = E[t0:t0 + W]
        if len(ref) < W // 2:
            break
        t0s.append(t0)
        if ref.std() < 0.2:
            Z.append([0.0] * len(lags))
            F.append(list(map(float, lags)))
            continue
        c = _xcorr(ref, _trozo(S, t0 + lo, t0 + len(ref) + hi))
        js = [l - lo for l in lags_i]
        zs = _z(c, js + [int(np.argmax(c))])
        fila_z, fila_f = [], []
        for j in js:
            a, b = max(0, j - TOL_CAND - 1), min(len(c), j + TOL_CAND + 2)
            k = a + int(np.argmax(zs[a:b]))
            fila_z.append(float(zs[k]))
            fila_f.append(lo + _parabola(c, k))
        Z.append(fila_z)
        F.append(fila_f)
    return np.array(t0s), np.array(Z), np.array(F)


def _camino(Z, lags, ex, H=500):
    """Mejor sucesión de tramos (Viterbi). El castellano nunca vuelve atrás: pasar del desfase a al b < a deja a-b
    de vídeo sin castellano (ventanas que no puntúan). Cada cambio cuesta, y saltarse castellano cuesta más cuanto
    más se salta. Devuelve [[estado, ventana_ini, ventana_fin]] (entre dos rachas puede quedar un hueco)."""
    Zc = np.clip(Z, 0, 25)
    n, K = Zc.shape
    G = [[max(0, int(round((lags[j] - lags[k]) / H))) for k in range(K)] for j in range(K)]
    C = [[ex['penal'] + 0.002 * max(0.0, lags[k] - lags[j]) + 0.001 * max(0.0, lags[j] - lags[k])
          for k in range(K)] for j in range(K)]
    dp = np.full((n, K), -np.inf)
    de = np.full((n, K, 2), -1, int)
    dp[0] = Zc[0]
    for w in range(1, n):
        for k in range(K):
            mejor, origen = dp[w - 1, k], (w - 1, k)
            for j in range(K):
                if j != k:
                    w0 = w - 1 - G[j][k]
                    if w0 >= 0 and dp[w0, j] - C[j][k] > mejor:
                        mejor, origen = dp[w0, j] - C[j][k], (w0, j)
            dp[w, k] = mejor + Zc[w, k]
            de[w, k] = origen
    w, k = n - 1, int(np.argmax(dp[n - 1]))
    rachas, fin = [], n - 1
    while True:
        pw, pk = de[w, k]
        if pw < 0:
            rachas.append([k, w, fin])
            break
        if pk != k:
            rachas.append([k, w, fin])
            fin, w, k = pw, pw, pk
        else:
            w = pw
    return rachas[::-1]


def _suave(X):
    k = np.array([1, 2, 3, 2, 1], np.float32) / 9
    return np.stack([np.convolve(X[:, b], k, 'same') for b in range(X.shape[1])], 1)


def _afinar_corte(Es, Ss, la, lb, t_ini, t_fin):
    """Instante (frame del vídeo) en que se pasa del desfase la al lb. Si lb < la, al vídeo le sobran la-lb frames
    tras el corte (no están en castellano); si lb > la, al castellano le sobran (se saltan)."""
    la_i, lb_i = int(round(la)), int(round(lb))
    g = max(0, la_i - lb_i)
    t_ini, t_fin = max(0, t_ini), min(len(Es), t_fin)
    if t_fin - t_ini <= g + 10:
        return None
    t = np.arange(t_ini, t_fin)

    def prod(lag):
        idx = t + lag
        ok = (idx >= 0) & (idx < len(Ss))
        p = np.zeros(len(t), np.float32)
        p[ok] = (Es[t[ok]] * Ss[idx[ok]]).sum(1)
        return p
    CA = np.concatenate([[0], np.cumsum(prod(la_i))])
    CB = np.concatenate([[0], np.cumsum(prod(lb_i))])
    n = len(t)
    tcs = np.arange(0, n - g + 1)
    Fo = CA[tcs] + (CB[n] - CB[tcs + g])
    return t_ini + int(np.argmax(Fo))


def piezas_de(tramos, d_video, d_es):
    """tramos [(t_video, desfase)] -> piezas contiguas [('es', a, b, desfase) | ('orig', a, b, None)] que cubren el vídeo.
    desfase: segundo del castellano = segundo del vídeo + desfase."""
    es = []
    for k, (tc, lag) in enumerate(tramos):
        ini = 0.0 if k == 0 else tc + max(0.0, tramos[k - 1][1] - lag)
        fin = tramos[k + 1][0] if k + 1 < len(tramos) else d_video
        a, b = max(ini, -lag), min(fin, d_es - lag)
        if b > a + 0.02:
            es.append(['es', a, b, lag])
    out, t = [], 0.0
    for p in es:
        if p[1] > t + 0.02:
            out.append(['orig', t, p[1], None])
        p[1] = max(p[1], t)
        if p[2] > p[1] + 0.02:
            out.append(p)
            t = p[2]
    if d_video > t + 0.02:
        out.append(['orig', t, d_video, None])
    return [tuple(p) for p in out]


def _subtramos(rach, lag_r, Z, Fz, z):
    """Dentro de un tramo, un trozo seguido (>= 3 ventanas fiables) cuyo desfase fino se aparta >= 40 ms es otro tramo
    (cortes de unos fotogramas que la búsqueda gruesa no separa: un tramo de 2 min desplazado 70 ms)."""
    nr, nl = [], []
    for (s, a, b), lr in zip(rach, lag_r):
        ws = [w for w in range(a, b + 1) if Z[w, s] >= z]
        if len(ws) < 6:
            nr.append([s, a, b]); nl.append(lr)
            continue
        r = np.array([Fz[w, s] - lr for w in ws])
        rs = np.array([np.median(r[max(0, i - 1):i + 2]) for i in range(len(r))])
        trozos, act = [], None
        for i, w in enumerate(ws):
            if abs(rs[i]) >= TOL_CAND and (act is None or abs(rs[i] - np.median(act[1])) < 2.5):
                act = act or [i, []]
                act[1].append(rs[i])
            else:
                if act and len(act[1]) >= 3:
                    trozos.append((act[0], act[0] + len(act[1]) - 1, float(np.median(r[act[0]:act[0] + len(act[1])]))))
                act = [i, [rs[i]]] if abs(rs[i]) >= TOL_CAND else None
        if act and len(act[1]) >= 3:
            trozos.append((act[0], act[0] + len(act[1]) - 1, float(np.median(r[act[0]:act[0] + len(act[1])]))))
        ini = a
        for i0, i1, m in trozos:
            w0, w1 = ws[i0], ws[i1]
            if w0 > ini:
                nr.append([s, ini, w0 - 1]); nl.append(lr)
            nr.append([s, w0, w1]); nl.append(lr + m)
            ini = w1 + 1
        if ini <= b:
            nr.append([s, ini, b]); nl.append(lr)
    return nr, nl


def _iguales(BE, BS, piezas):
    """Fracción del castellano casi idéntica a la referencia (si es casi todo, la pista no es castellano)."""
    LE, LS = np.log10(BE + 1e-10), np.log10(BS + 1e-10)
    W, total, iguales = 500, 0, 0
    for tipo, a, b, lag in piezas:
        if tipo != 'es':
            continue
        for t in range(int(a * FPS), int(b * FPS) - W, 500):
            s = t + int(round(lag * FPS))
            if s < 0 or s + W > len(LS) or t + W > len(LE):
                continue
            x = LE[t:t + W] - LE[t:t + W].mean(0)
            y = LS[s:s + W] - LS[s:s + W].mean(0)
            if x.std() < 0.05 or y.std() < 0.05:
                continue
            total += 1
            iguales += float((x * y).sum() / np.sqrt((x * x).sum() * (y * y).sum() + 1e-12)) >= 0.93
    return iguales / total if total else 0.0


def _ganancia(BE, BS, piezas):
    """dB para que el audio original de relleno suene tan alto como el castellano (mediana por segundos)."""
    d = []
    for tipo, a, b, lag in piezas:
        if tipo != 'es':
            continue
        k = int(round(lag * FPS))
        for t in range(int(a * FPS), int(b * FPS) - FPS, FPS):
            if t + k < 0 or t + k + FPS > len(BS) or t + FPS > len(BE):
                continue
            e, s = BE[t:t + FPS].sum(), BS[t + k:t + k + FPS].sum()
            if e > 1e-6 and s > 1e-6:
                d.append(10 * np.log10(s / e))
    if len(d) < 20:
        return 0.0
    d = np.array(d)
    d = d[d > np.percentile(d, 5)]
    return float(np.clip(np.median(d), -12, 12))


def _velocidades(aj, d_eng, d_es):
    if aj.get('velocidad', 'auto') in VELOCIDADES:
        return {aj['velocidad']: VELOCIDADES[aj['velocidad']]}
    # primero la que mejor cuadra por duración (un castellano de 25 fps dura un 4 % menos)
    out = sorted(((k, v) for k, v in VELOCIDADES.items() if abs(d_es / v - d_eng) <= max(aj['busca_s'], 360)),
                 key=lambda kv: abs(d_es / kv[1] - d_eng))
    return dict(out) or {'1': 1.0}


def medir(video, ref_id, es, aj=None, BE=None):
    """Plan para meter 'es' en 'video': dict con velocidad, tempo, tramos [(s_vídeo, desfase)], piezas, calidad y
    detalle. Lanza Fallo si no hay sincronía fiable. BE: bandas ya calculadas de la referencia (reintentos)."""
    aj = {**DEFECTO, **(aj or {})}
    ex = EXIGENCIA.get(aj['exigencia'], EXIGENCIA['normal'])
    jv, je = info(video), info(es)
    d_eng, d_es = duracion(jv), duracion(je)
    detalle = []
    t_ini = time.time()
    progreso('leyendo el audio original')
    if BE is None:
        BE = bandas_pista(video, f'0:{ref_id}')
    progreso('buscando dónde encaja')
    E = ataques(BE)
    detalle.append(f'referencia: pista {ref_id} ({nombre_pista(jv, ref_id)}), {d_eng / 60:.1f} min; castellano {d_es / 60:.1f} min')

    if aj.get('desfase_manual') not in (None, ''):
        vel = aj['velocidad'] if aj.get('velocidad') in VELOCIDADES else '1'
        tempo = VELOCIDADES[vel]
        tramos = [(0.0, -float(aj['desfase_manual']))]
        BS = bandas_pista(es, '0:a:0', tempo)
        piezas = piezas_de(tramos, d_eng, len(BS) / FPS)
        detalle.append(f'desfase manual {float(aj["desfase_manual"]):+.3f} s ({NOMBRE_VEL[vel]}), sin medir')
        return {'velocidad': vel, 'tempo': tempo, 'tramos': tramos, 'piezas': piezas, 'manual': True,
                'calidad': {'z_mediana': 0, 'cobertura': 0, 'fuertes': 0, 'deriva_ms': 0}, 'detalle': detalle,
                'ref': ref_id, 'ganancia_db': _ganancia(BE, BS, piezas) if aj['igualar_volumen'] else 0.0,
                'igual_frac': 0.0, 'd_video': d_eng, 'd_es': len(BS) / FPS, 'BE': BE}

    # 1) velocidad y desfases candidatos
    busca = int(aj['busca_s'] * FPS)
    mejor = None
    for vel, tempo in _velocidades(aj, d_eng, d_es).items():
        BS = bandas_pista(es, '0:a:0', tempo)
        S = ataques(BS)
        b = min(busca + int(abs(len(S) - len(E))), busca + 600 * FPS)
        picos = _etapa1(E, S, b)
        cands = _candidatos(picos, ex)
        m = _recta(picos, ex['z'], len(E)) if aj.get('velocidad', 'auto') == 'auto' else 0.0
        if m:                                       # deriva clara (p. ej. 24 frente a 23,976): corregir y repetir
            tempo *= (1 + m)
            detalle.append(f'{NOMBRE_VEL[vel]}: deriva de {m * len(E) * 10:.0f} ms en todo el vídeo, velocidad {tempo:.6f}')
            BS = bandas_pista(es, '0:a:0', tempo)
            S = ataques(BS)
            picos = _etapa1(E, S, b)
            cands = _candidatos(picos, ex)
        cob = _cobertura(picos, cands, ex['z'])
        n_v = max(1, len({p[0] for p in picos}))
        txt = ', '.join(f"{c['lag'] / FPS:+.2f}s×{c['ventanas']}" for c in cands[:5])
        detalle.append(f'{NOMBRE_VEL[vel]}: {cob} ventanas fiables; candidatos {txt or "ninguno"}')
        if cands and (mejor is None or cob > mejor[0]):
            mejor = (cob, vel, tempo, BS, S, cands)
        if cob >= 0.5 * len(E) / 1500:              # la mitad de las ventanas cuadran: no hace falta probar más
            break
    if not mejor or mejor[0] < 2:
        raise Fallo('no encuentro dónde encaja el castellano (' + '; '.join(detalle[1:])[:300] + ')')
    _, vel, tempo, BS, S, cands = mejor

    # 2) tramos: camino de desfases ventana a ventana, deriva residual y desfase fino
    for vuelta in range(3):
        lags = [c['lag'] for c in cands]
        t0s, Z, Fz = _etapa2(E, S, lags)
        for _ in range(6):
            rach = _camino(Z, lags, ex)
            debiles = []
            for s, a, b in rach:
                ok = [w for w in range(a, b + 1) if Z[w, s] >= ex['z'] and Z[w, s] >= Z[w].max() - 0.5]
                if len(ok) < 2 and not any(Z[w, s] >= ex['z_solo'] for w in ok):
                    debiles.append((s, a, b))
            if not debiles or len(rach) == 1:
                break
            for s, a, b in debiles:                 # tramos sin apoyo: fuera y otra vez
                Z[a:b + 1, s] = 0
        fuertes = [(w, s) for s, a, b in rach for w in range(a, b + 1) if Z[w, s] >= ex['z']]
        if len(fuertes) < ex['min_fuertes']:
            raise Fallo(f'solo {len(fuertes)} ventanas fiables (mínimo {ex["min_fuertes"]}): ' + '; '.join(detalle[1:])[:250])
        lag_r = []
        for s, a, b in rach:
            v = sorted((Fz[w, s], Z[w, s]) for w in range(a, b + 1) if Z[w, s] >= ex['z']) or [(lags[s], 1.0)]
            pes = np.cumsum([x[1] for x in v])
            lag_r.append(v[int(np.searchsorted(pes, pes[-1] / 2))][0])
        xs, ys, ps = [], [], []
        for (s, a, b), lr in zip(rach, lag_r):
            for w in range(a, b + 1):
                if Z[w, s] >= ex['z'] and abs(Fz[w, s] - lr) < 15:
                    xs.append(t0s[w] + 1000)
                    ys.append(Fz[w, s] - lr)
                    ps.append(Z[w, s])
        pend = float(np.polyfit(xs, ys, 1, w=np.sqrt(ps))[0]) if len(xs) >= 8 else 0.0
        # deriva >= 120 ms en todo el vídeo: se corrige la velocidad (obliga a recodificar). Menos que eso (±60 ms,
        # imperceptible) se deja centrado y el castellano se copia sin tocarlo.
        if abs(pend) * len(E) >= DERIVA_MIN and vuelta < 2:
            tempo *= (1 + pend)
            detalle.append(f'deriva de {pend * len(E) * 10:.0f} ms: velocidad ajustada a {tempo:.6f}')
            BS = bandas_pista(es, '0:a:0', tempo)
            S = ataques(BS)
            cands = [{**c, 'lag': (c['lag'] - pend * len(E) / 2) * (1 - pend)} for c in cands]
            continue
        break

    rach, lag_r = _subtramos(rach, lag_r, Z, Fz, ex['z'])
    progreso('afinando cortes')
    # 3) cortes: el instante exacto de cada cambio de desfase
    tramos = []
    Es, Ss = _suave(E), _suave(S)
    for k, ((s, a, b), lag) in enumerate(zip(rach, lag_r)):
        if not tramos:
            tramos.append([0, lag])
            continue
        la = tramos[-1][1]
        if abs(lag - la) < 3:                       # < 30 ms: el mismo tramo
            continue
        ps_, pa, pb = rach[k - 1]
        ult = max([w for w in range(pa, pb + 1) if Z[w, ps_] >= ex['z']], default=pb)
        pri = min([w for w in range(a, b + 1) if Z[w, s] >= ex['z']], default=a)
        tc = _afinar_corte(Es, Ss, la, lag, t0s[ult], t0s[pri] + 2000)
        tramos.append([tc if tc is not None else (t0s[ult] + 2000 + t0s[pri]) // 2, lag])
    limpio = []                                     # cortes en orden y sin tramos de menos de 1 s
    for t, l in tramos:
        if limpio and t <= limpio[-1][0] + FPS:
            limpio[-1][1] = l
        elif limpio and abs(l - limpio[-1][1]) < 3:
            continue
        else:
            limpio.append([t, l])
        if len(limpio) > 1 and abs(limpio[-1][1] - limpio[-2][1]) < 3:
            limpio.pop()
    tramos = limpio
    if len(tramos) - 1 > aj['max_cortes']:
        raise Fallo(f'{len(tramos) - 1} cortes (máximo {aj["max_cortes"]}): las dos versiones son demasiado distintas')
    fino = float(aj.get('ajuste_fino_s') or 0)
    tramos = [(t / FPS, l / FPS - fino) for t, l in tramos]
    piezas = piezas_de(tramos, d_eng, len(S) / FPS)
    zs = [Z[w, s] for w, s in fuertes]
    activas = int((Z.max(1) > 0).sum())
    calidad = {'z_mediana': round(float(np.median(zs)), 1), 'fuertes': len(fuertes),
               'cobertura': round(len(fuertes) / max(1, activas), 2), 'deriva_ms': round(pend * len(E) * 10)}
    igual = _iguales(BE, BS, piezas)
    if igual > 0.6:
        raise Fallo(f'la pista «castellana» es casi idéntica a la original en el {igual:.0%} del vídeo: no parece '
                    'castellano (¿mal etiquetada?)')
    txt = ', '.join(f'min {t / 60:.2f} -> {l:+.2f}s' for t, l in tramos[1:])
    detalle.append(f'{NOMBRE_VEL[vel]}, desfase {tramos[0][1]:+.3f}s' + (f', cortes: {txt}' if txt else '') +
                   f' · {len(fuertes)} ventanas fiables (z~{calidad["z_mediana"]}), {time.time() - t_ini:.0f} s')
    return {'velocidad': vel, 'tempo': tempo, 'tramos': tramos, 'piezas': piezas, 'calidad': calidad, 'ref': ref_id,
            'detalle': detalle, 'igual_frac': round(igual, 3),
            'ganancia_db': _ganancia(BE, BS, piezas) if aj['igualar_volumen'] else 0.0,
            'd_video': d_eng, 'd_es': len(S) / FPS, 'BE': BE}


# ------------------------------------------------------------------------------------------------ montaje
def _audio_es(j):
    return next(t for t in j['tracks'] if t['type'] == 'audio')


def _hms(t):
    t = max(0.0, t)
    return f'{int(t // 3600):02d}:{int(t % 3600 // 60):02d}:{t % 60:06.3f}'


def mmss(t):
    return f'{int(t // 60)}:{int(t % 60):02d}'


def claves_cerca(video, tiempos, margen=25):
    """Fotogramas clave del vídeo cerca de esos instantes (sin leer el archivo entero)."""
    if not tiempos:
        return []
    iv = ','.join(f'{max(0.0, t - margen):.3f}%{t + margen:.3f}' for t in sorted(tiempos))
    r = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'packet=pts_time,flags',
                        '-of', 'csv=p=0', '-read_intervals', iv, video], capture_output=True, text=True)
    ks = set()
    for linea in r.stdout.splitlines():
        partes = linea.split(',')
        if len(partes) >= 2 and 'K' in partes[1]:
            try:
                ks.add(round(float(partes[0]), 4))
            except ValueError:
                pass
    return sorted(ks)


def partes_a_conservar(plan, video, aj):
    """Tramos del vídeo que se quedan al recortar lo que no tiene castellano. None = no se recorta nada."""
    d = plan['d_video']
    quitar = []
    if aj['sin_castellano'] == 'recortar':
        quitar += [(a, b) for tipo, a, b, _ in plan['piezas'] if tipo == 'orig' and b - a >= aj['recorte_min_s']]
    if not quitar:
        return None
    quitar.sort()
    unidas = []
    for a, b in quitar:
        if unidas and a <= unidas[-1][1] + 0.5:
            unidas[-1][1] = max(unidas[-1][1], b)
        else:
            unidas.append([a, b])
    kf = claves_cerca(video, [x for ab in unidas for x in ab])
    if not kf:
        raise RuntimeError('no encuentro los fotogramas clave para recortar')
    conservar_cast = aj['recorte_ajuste'] != 'quitar'
    huecos = []
    for a, b in unidas:
        if a <= 0.5:
            ca = 0.0
        else:
            ca = min([k for k in kf if k >= a - 0.001], default=None) if conservar_cast else \
                max([k for k in kf if k <= a + 0.001], default=None)
        if b >= d - 0.5:
            cb = d + 1
        else:
            cb = max([k for k in kf if k <= b + 0.001], default=None) if conservar_cast else \
                min([k for k in kf if k >= b - 0.001], default=None)
        if ca is None or cb is None or cb - ca < 1.0:
            continue
        huecos.append((ca, cb))
    if not huecos:
        return None
    partes, t = [], 0.0
    for a, b in huecos:
        if a > t + 0.05:
            partes.append((t, a))
        t = b
    if t < d - 0.05:
        partes.append((t, d))
    return partes


def a_salida(t, partes):
    """Instante del vídeo original -> instante en el vídeo recortado (None si se quitó)."""
    if not partes:
        return t
    acc = 0.0
    for a, b in partes:
        if a <= t < b:
            return acc + t - a
        acc += b - a
    return None


def montar_audio(video, es, plan, aj, carpeta):
    """Pista en castellano ya colocada en la línea de tiempo del vídeo. Devuelve (ruta, sync_ms, recodificado)."""
    aj = {**DEFECTO, **(aj or {})}
    sal = os.path.join(carpeta, '.duberr-audio.tmp.mka')
    je = info(es)
    t_es = _audio_es(je)
    canales = _p(t_es, 'audio_channels') or 2
    sr_es = _p(t_es, 'audio_sampling_frequency') or 48000
    tempo, piezas, d = plan['tempo'], plan['piezas'], plan['d_video']
    es_p = [p for p in piezas if p[0] == 'es']
    grandes = [p for p in piezas if p[0] == 'orig' and p[2] - p[1] > RELLENO_MIN]
    if abs(tempo - 1) < 1e-9 and len(es_p) == 1 and not grandes:
        lag = es_p[0][3]
        largo = d + lag + 1
        subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-i', es, '-map', '0:a:0', '-t', f'{largo:.3f}', '-c', 'copy',
                        '-metadata:s:a:0', 'language=spa', '-f', 'matroska', sal], check=True)
        return sal, int(round(-lag * 1000)), False
    if aj['codec'] == 'flac':
        L = {1: 'mono', 2: 'stereo', 6: '5.1', 8: '7.1'}.get(canales, 'stereo' if canales <= 2 else '5.1')
        cod = ['-c:a', 'flac', '-sample_fmt', 's16']
    else:
        L = {1: 'mono', 2: 'stereo'}.get(canales, '5.1')
        cod = ['-c:a', 'ac3', '-b:a', '640k' if canales > 2 else '256k' if canales == 2 else '192k']
    fmt = f'aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts={L}'
    if abs(tempo - 1) < 1e-9:
        tem = ''
    elif aj['tono'] == 'natural':
        tem = f'asetrate={int(round(sr_es * tempo))},aresample=48000,'
    else:
        tem = f'atempo={tempo:.8f},'
    fade = (aj.get('fundido_ms') or 0) / 1000
    vol = 'volume=0' if aj['sin_castellano'] == 'silencio' else f'volume={plan.get("ganancia_db", 0.0):.2f}dB'
    n_es = len(es_p)
    partes = [f'[0:a:0]{tem}{fmt},asplit={n_es}' + ''.join(f'[s{k}]' for k in range(n_es))] if n_es > 1 else []
    entradas, k_es = '', 0
    for k, (tipo, a, b, lag) in enumerate(piezas):
        largo = b - a
        f = ''
        if fade and largo > 4 * fade:
            if k > 0:
                f += f',afade=t=in:d={fade:.3f}'
            if k < len(piezas) - 1:
                f += f',afade=t=out:st={largo - fade:.4f}:d={fade:.3f}'
        if tipo == 'es':
            src = f'[s{k_es}]' if n_es > 1 else f'[0:a:0]{tem}{fmt},'
            partes.append(f'{src}atrim=start={a + lag:.4f}:end={b + lag:.4f},asetpts=PTS-STARTPTS{f}[p{k}]')
            k_es += 1
        else:
            partes.append(f'[1:{plan["ref"]}]{fmt},atrim=start={a:.4f}:end={b:.4f},asetpts=PTS-STARTPTS,{vol}{f}[p{k}]')
        entradas += f'[p{k}]'
    partes.append(f'{entradas}concat=n={len(piezas)}:v=0:a=1[out]')
    grafo = ';'.join(partes)
    r = subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-i', es, '-i', video, '-filter_complex', grafo,
                        '-map', '[out]', '-t', f'{d:.3f}', *cod, '-metadata:s:a:0', 'language=spa', '-f', 'matroska', sal],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError('ffmpeg al montar el audio: ' + r.stderr[-400:])
    return sal, 0, True


def mezclar(video, es, plan, salida, aj=None, quitar_ids=()):
    """Mete el castellano en 'video' -> 'salida' (nuevo). Devuelve dict con la comprobación y lo recortado."""
    aj = {**DEFECTO, **(aj or {})}
    j = info(video)
    carpeta = os.path.dirname(salida)
    partes = partes_a_conservar(plan, video, aj)
    progreso('montando el audio')
    audio, sync_ms, recod = montar_audio(video, es, plan, aj, carpeta)
    progreso('uniendo pistas' + (' y recortando' if partes else ''))
    try:
        ref = plan['ref']
        vid = [t['id'] for t in j['tracks'] if t['type'] == 'video']
        aud = [t['id'] for t in j['tracks'] if t['type'] == 'audio' and t['id'] not in quitar_ids]
        sub = [t for t in j['tracks'] if t['type'] == 'subtitles']
        if aj['pistas'] == 'castellano_y_original':
            aud = [ref]
        aud = [ref] + [i for i in aud if i != ref]
        orden = [f'0:{i}' for i in vid] + ['1:0'] + [f'0:{i}' for i in aud] + [f'0:{t["id"]}' for t in sub]
        cmd = ['mkvmerge', '-q', '-o', salida, '--track-order', ','.join(orden)]
        if partes:
            spec = ','.join(('+' if i else '') + f'{_hms(a)}-{_hms(b)}' for i, (a, b) in enumerate(partes))
            cmd += ['--split', 'parts:' + spec]
        if len(aud) < sum(1 for t in j['tracks'] if t['type'] == 'audio'):
            cmd += ['-a', ','.join(map(str, aud))]
        for i in aud:
            cmd += ['--default-track-flag', f'{i}:0']
        for t in sub:
            forzado = es_spa(t) and not es_latino(t) and (_p(t, 'forced_track') or re.search(r'forz|forced', _p(t, 'track_name') or '', re.I))
            if forzado and aj['subs_forzados']:
                cmd += ['--default-track-flag', f'{t["id"]}:1', '--forced-display-flag', f'{t["id"]}:1']
            else:
                cmd += ['--default-track-flag', f'{t["id"]}:0']
        cmd += [video, '--language', '0:spa', '--track-name', '0:Castellano', '--default-track-flag', '0:1',
                '--sync', f'0:{sync_ms}', audio]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode not in (0, 1):  # 1 = avisos de mkvmerge
            raise RuntimeError('mkvmerge: ' + (r.stdout + r.stderr)[-500:])
    finally:
        if os.path.exists(audio):
            os.remove(audio)
    if partes:                                      # mkvmerge puede numerar la salida al partir
        base, ext = os.path.splitext(salida)
        hechos = sorted(x for x in os.listdir(carpeta) if x.startswith(os.path.basename(base)) and x.endswith(ext))
        if not os.path.exists(salida):
            if len(hechos) == 1:
                os.rename(os.path.join(carpeta, hechos[0]), salida)
            else:
                for x in hechos:
                    os.remove(os.path.join(carpeta, x))
                raise RuntimeError(f'mkvmerge dejó {len(hechos)} archivos al recortar')
    js = info(salida)
    esperado = sum(b - a for a, b in partes) if partes else duracion(j)
    if abs(duracion(js) - esperado) > (3 if partes else 5) or not tiene_castellano(js):
        raise RuntimeError(f'el resultado no cuadra (duración {duracion(js):.0f} s, esperaba {esperado:.0f} s, o falta la pista spa)')
    progreso('comprobando el resultado')
    comp = verificar(salida, plan, partes, aj)
    return {'comprobacion': comp, 'partes': partes, 'recodificado': recod,
            'quitado_s': round(duracion(j) - duracion(js), 1) if partes else 0.0}


def verificar(salida, plan, partes, aj):
    """Mide el resultado: el castellano (1.ª pista de audio) contra la referencia (2.ª) debe dar ~0 en las piezas
    con castellano. Solo falla si hay contradicciones o ninguna prueba a favor."""
    nivel = aj.get('comprobacion', 'normal')
    if nivel == 'no':
        return {'omitida': True}
    ex = EXIGENCIA['alta' if nivel == 'estricta' else aj.get('exigencia', 'normal') if aj.get('exigencia') in EXIGENCIA else 'normal']
    zonas = []
    for tipo, a, b, _ in plan['piezas']:
        if tipo != 'es':
            continue
        a2, b2 = a_salida(a + 1, partes), a_salida(b - 1, partes)
        if a2 is not None and b2 is not None:
            a2 = max(a2, 3.0)
            if b2 - a2 > 35:
                zonas.append((a2, b2))
    total = sum(b - a for a, b in zonas)
    if not zonas:
        return {'omitida': True, 'motivo': 'sin tramos largos con castellano'}
    n = 16
    puntos, paso = [], total / n
    for k in range(n):
        x = (k + 0.5) * paso
        for a, b in zonas:
            if x <= b - a:
                puntos.append(max(a, min(b - 32, a + x)))
                break
            x -= b - a
    buenos, malos, res = 0, 0, []
    for t in puntos:
        try:
            A = ataques(bandas_pista(salida, '0:a:1', inicio=t, largo=30))      # referencia
            B = ataques(bandas_pista(salida, '0:a:0', inicio=t - 2, largo=34))  # castellano con 2 s de margen
        except Exception:
            continue
        if len(A) < 2000 or len(B) < len(A) + 100 or A.std() < 0.2:
            continue
        c = _xcorr(A, B)
        i = int(np.argmax(c))
        z = float(_z(c, [i])[i])
        off = (_parabola(c, i) - 200) / FPS
        res.append((round(t), round(float(off), 3), round(z, 1)))
        if z >= ex['verif_z']:
            if abs(off) <= (0.045 if z >= 10 else 0.08):
                buenos += 1
            else:
                malos += 1
    if malos > max(0, buenos // 6) or buenos < ex['verif_min']:
        raise RuntimeError(f'la comprobación del resultado no cuadra ({buenos} bien, {malos} mal): {res}')
    return {'bien': buenos, 'mal': malos, 'puntos': res}


# ------------------------------------------------------------------------------------------------ subtítulos externos
_SRT_T = re.compile(r'(\d+):(\d\d):(\d\d)[,.](\d{3})\s*-->\s*(\d+):(\d\d):(\d\d)[,.](\d{3})')


def remapear_srt(texto, partes):
    """Ajusta un .srt al vídeo recortado: quita lo que cae en tramos quitados y adelanta el resto."""
    def seg(h, m, s, ms):
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000

    def fmt(t):
        ms = int(round(t * 1000))
        return f'{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}'
    bloques = re.split(r'\r?\n\s*\r?\n', texto.strip())
    out = []
    for bl in bloques:
        lineas = bl.splitlines()
        k = next((i for i, l in enumerate(lineas) if _SRT_T.search(l)), None)
        if k is None:
            continue
        m = _SRT_T.search(lineas[k])
        a, b = seg(*m.groups()[:4]), seg(*m.groups()[4:])
        a2 = b2 = None
        acc = 0.0
        for pa, pb in partes:
            if b > pa and a < pb:
                a2 = acc + max(a, pa) - pa
                b2 = acc + min(b, pb) - pa
                break
            acc += pb - pa
        if a2 is None or b2 - a2 < 0.2:
            continue
        out.append(f'{len(out) + 1}\n{fmt(a2)} --> {fmt(b2)}\n' + '\n'.join(lineas[k + 1:]))
    return '\n\n'.join(out) + '\n'


# ------------------------------------------------------------------------------------------------ guardar el castellano
def guardar_pista(origen, destino, idioma_arr=None, unica_vale=False):
    """Copia (sin recodificar) la pista en castellano de 'origen' a 'destino' (.mka) + .json.
    Devuelve (True, nota) o (False, motivo). Nunca guarda latino ni comentarios. Una pista sin etiqueta solo vale si
    es la única y Sonarr/Radarr dicen que el archivo está en castellano (idioma_arr)."""
    if os.path.exists(destino):
        return True, 'ya guardado'
    j = info(origen)
    aud = [t for t in j['tracks'] if t['type'] == 'audio']
    spa = pistas_castellano(j)
    if spa:
        nombrada = [t for t in spa if re.search(r'castell|españa|spain|es-es', f"{_p(t, 'track_name') or ''} {_p(t, 'language_ietf') or ''}", re.I)]
        t = max(nombrada or spa, key=lambda t: (_p(t, 'audio_channels', 0), -t['id']))
        nota = 'spa' + (' (había latino también)' if any(es_spa(x) and es_latino(x) for x in aud) else '')
    elif any(es_spa(x) and es_latino(x) for x in aud) and not [x for x in aud if not es_spa(x)]:
        return False, 'solo hay castellano latino'
    elif len(aud) == 1 and idioma_arr and 'spanish' in [x.lower() for x in idioma_arr] and \
            _p(aud[0], 'language') in (None, 'und', ''):
        t, nota = aud[0], 'sin etiqueta (única pista; Sonarr/Radarr la dan por castellano)'
    elif len(aud) == 1 and unica_vale and not es_latino(aud[0]):
        t, nota = aud[0], 'única pista (archivo dejado en la carpeta de entrada)'
    else:
        return False, 'sin pista en castellano' + (f' ({len(aud)} pistas y ninguna marcada spa)' if len(aud) > 1 else '')
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    r = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=r_frame_rate',
                        '-of', 'csv=p=0', origen], capture_output=True, text=True)
    tmp = destino + '.tmp'
    subprocess.run(['mkvmerge', '-q', '-o', tmp, '-D', '-S', '-B', '-M', '-T', '--no-chapters', '--no-global-tags',
                    '-a', str(t['id']), '--language', f"{t['id']}:spa", '--track-name', f"{t['id']}:Castellano", origen], check=True)
    os.rename(tmp, destino)
    meta = {'origen': os.path.basename(origen), 'pista': t['id'], 'codec': t['codec'],
            'canales': _p(t, 'audio_channels'), 'nota': nota, 'fps': r.stdout.strip(),
            'duracion_s': duracion(j), 'guardado': time.strftime('%Y-%m-%d %H:%M')}
    with open(os.path.splitext(destino)[0] + '.json', 'w') as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=1)
    return True, nota
