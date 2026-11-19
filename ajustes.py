"""duberr · ajustes del proceso. Se resuelven por capas: valores de fábrica <- generales <- serie/película <- episodio.
Los generales se cambian en /ajustes; los de una serie o un episodio, en su ficha (vacío = heredar)."""
import hashlib, json

import nucleo as N

# (clave, etiqueta, tipo, opciones, ayuda, ámbito)   ámbito: 'todo' | 'general' | 'concreto' (solo serie/episodio)
GRUPOS = [
    ('Sincronización', 'Cómo se busca dónde encaja el castellano.', [
        ('exigencia', 'Exigencia', 'select', [('alta', 'Alta'), ('normal', 'Normal'), ('baja', 'Baja')],
         'Cuánta coincidencia hace falta para dar la sincronía por buena. Baja acepta audios pobres (DVDrip, '
         'grabaciones de la tele) con más riesgo de error; alta solo mezcla lo que está muy claro.', 'todo'),
        ('busca_s', 'Margen de búsqueda', 'number', {'min': 60, 'max': 1800, 'paso': 30, 'unidad': 's'},
         'Diferencia máxima entre el castellano y el vídeo (resúmenes, anuncios u openings de distinta duración). '
         'Más margen es más lento y da algo más de pie a confundir escenas parecidas.', 'todo'),
        ('velocidad', 'Velocidad del castellano', 'select',
         [('auto', 'Detectar'), ('1', 'La misma que el vídeo'), ('25->23.976', '25 fps (PAL) → 23,976'),
          ('23.976->25', '23,976 → 25 fps')],
         'Las versiones europeas de tele y DVD van a 25 fps, un 4 % más rápidas que el Bluray. Detectar prueba las '
         'tres y corrige además derivas pequeñas (24 frente a 23,976).', 'todo'),
        ('max_cortes', 'Cortes máximos', 'number', {'min': 0, 'max': 20, 'paso': 1, 'unidad': ''},
         'Cuántas veces puede cambiar el desfase dentro de un episodio (escena quitada, pausa publicitaria, opening '
         'distinto). 0 = solo se acepta un desfase constante.', 'todo'),
        ('pista_ref', 'Pista de referencia', 'select',
         [('auto', 'Idioma original'), ('eng', 'Inglés'), ('jpn', 'Japonés'), ('fre', 'Francés'), ('ger', 'Alemán'),
          ('ita', 'Italiano'), ('kor', 'Coreano')],
         'Con qué pista del vídeo se compara. El idioma original suele ser la mejor: el doblaje se hace sobre su '
         'música y efectos.', 'todo'),
        ('ajuste_fino_s', 'Ajuste fino', 'number', {'min': -5, 'max': 5, 'paso': 0.01, 'unidad': 's'},
         'Desplaza el castellano después de medir: positivo lo retrasa, negativo lo adelanta. Para corregir a oído '
         'un desfase pequeño.', 'concreto'),
        ('desfase_manual', 'Desfase fijo, sin medir', 'number', {'min': -1800, 'max': 1800, 'paso': 0.01, 'unidad': 's', 'vacio': True},
         'Último recurso si la medición falla: no se mide nada y se aplica este desfase a todo el episodio '
         '(positivo = retrasar el castellano). Se combina con «Velocidad».', 'concreto'),
    ]),
    ('Partes sin castellano', 'Escenas del vídeo que no existen en la versión en castellano (opening, escenas añadidas '
     'en el Bluray, cortes de la tele). Lo que sobra del castellano y no está en el vídeo se descarta siempre.', [
        ('sin_castellano', 'Qué hacer con ellas', 'select',
         [('rellenar', 'Rellenar con el audio original'), ('recortar', 'Recortarlas del vídeo'), ('silencio', 'Dejarlas en silencio')],
         'Recortar quita esas escenas del vídeo (y de todas sus pistas y subtítulos), así todo lo que se ve está en '
         'castellano. El original queda en la papelera para poder deshacer.', 'todo'),
        ('recorte_min_s', 'Recortar a partir de', 'number', {'min': 1, 'max': 300, 'paso': 0.5, 'unidad': 's'},
         'Solo se recortan los tramos sin castellano de al menos esta duración; los más cortos se rellenan.', 'todo'),
        ('recorte_ajuste', 'Punto de corte', 'select',
         [('conservar', 'No perder castellano'), ('quitar', 'No dejar nada del original')],
         'El vídeo solo se puede cortar en fotogramas clave (cada 1-5 s). «No perder castellano» puede dejar algún '
         'segundo del original junto al corte; «No dejar nada» puede comerse algún segundo de castellano.', 'todo'),
        ('igualar_volumen', 'Igualar el volumen del relleno', 'bool', None,
         'Sube o baja el audio original de relleno para que no suene más fuerte o más flojo que el castellano.', 'todo'),
        ('fundido_ms', 'Fundido en las uniones', 'number', {'min': 0, 'max': 300, 'paso': 5, 'unidad': 'ms'},
         'Fundido corto en cada unión de tramos para que no se oiga un chasquido.', 'todo'),
    ]),
    ('Resultado', 'Cómo queda el archivo.', [
        ('tono', 'Corrección de 25 fps', 'select',
         [('mantener', 'Mantener el tono del castellano'), ('natural', 'Tono de cine (remuestrear)')],
         'Al ralentizar un castellano de 25 fps: «mantener» deja las voces como sonaban en la tele; «tono de cine» '
         'las deja un 4 % más graves, como en el original, y sin artefactos de estiramiento.', 'todo'),
        ('codec', 'Códec si hay que recodificar', 'select',
         [('ac3', 'AC3 · compatible con todo'), ('flac', 'FLAC · sin pérdida')],
         'Solo cuando hay que tocar el audio (velocidad, cortes, relleno). Si no, el castellano se copia tal cual.', 'todo'),
        ('pistas', 'Pistas de audio', 'select',
         [('todas', 'Conservar todas'), ('castellano_y_original', 'Solo castellano y la de referencia')],
         'Quitar el resto de doblajes (francés, alemán...) ahorra espacio.', 'todo'),
        ('subs_forzados', 'Subtítulos forzados en castellano', 'bool', None,
         'Si el vídeo trae subtítulos forzados en castellano (carteles, diálogos en otro idioma), se activan por defecto.', 'todo'),
    ]),
    ('Comprobación y seguridad', '', [
        ('comprobacion', 'Comprobación final', 'select',
         [('estricta', 'Estricta'), ('normal', 'Normal'), ('no', 'Sin comprobar')],
         'Después de mezclar se vuelve a medir el resultado y, si no cuadra, no se sustituye nada. Sin comprobar es '
         'el último recurso: puede dejar un castellano desincronizado.', 'todo'),
        ('papelera_dias', 'Papelera', 'number', {'min': 0, 'max': 90, 'paso': 1, 'unidad': 'días'},
         'Días que se guarda el archivo sustituido para poder deshacer. Si ese archivo también tiene otros enlaces duros '
         '(p. ej. otra carpeta del servidor) no ocupa nada más. 0 = no guardar.', 'general'),
        ('avisos', 'Avisos al móvil', 'select',
         [('todos', 'Cada episodio'), ('importantes', 'Solo errores y finales'), ('ninguno', 'Ninguno')], '', 'general'),
    ]),
]

FABRICA = {**N.DEFECTO, 'papelera_dias': 14, 'avisos': 'todos'}

PRESETS = {
    'recomendado': ('Recomendado', 'Los valores de fábrica.', {}),
    'permisivo': ('Si falla', 'Acepta audios más pobres y versiones más distintas.',
                  {'exigencia': 'baja', 'busca_s': 900, 'max_cortes': 12}),
    'estricto': ('Estricto', 'Solo lo muy claro; comprobación más dura.',
                 {'exigencia': 'alta', 'max_cortes': 4, 'comprobacion': 'estricta'}),
}

DEF = {d[0]: d for _, _, ds in GRUPOS for d in ds}
DEL_NUCLEO = [k for k in N.DEFECTO]


def limpiar(valores, ambito):
    """Valida lo que llega de un formulario. Devuelve solo las claves con valor (vacío = heredar)."""
    out = {}
    for k, v in valores.items():
        d = DEF.get(k)
        if not d or v in (None, '') or (ambito == 'general' and d[5] == 'concreto') or (ambito != 'general' and d[5] == 'general'):
            continue
        tipo, opc = d[2], d[3]
        if tipo == 'select':
            if v in [o[0] for o in opc]:
                out[k] = v
        elif tipo == 'bool':
            out[k] = v in (True, 'si', '1', 'on', 'true')
        else:
            try:
                x = float(str(v).replace(',', '.'))
            except ValueError:
                continue
            x = min(opc['max'], max(opc['min'], x))
            out[k] = int(x) if float(opc['paso']).is_integer() and x.is_integer() else round(x, 3)
    return out


def efectivos(generales=None, item=None, episodio=None):
    aj = dict(FABRICA)
    for capa in (generales or {}, (item or {}).get('ajustes') or {}, episodio or {}):
        aj.update({k: v for k, v in capa.items() if k in FABRICA and v not in (None, '')})
    return aj


def huella(aj):
    """Resumen de los ajustes que cambian el resultado (para reintentar solo si cambian)."""
    return hashlib.sha1(json.dumps({k: aj.get(k) for k in DEL_NUCLEO}, sort_keys=True).encode()).hexdigest()[:10]


def texto(k, v):
    d = DEF.get(k)
    if not d:
        return str(v)
    if d[2] == 'select':
        return next((o[1] for o in d[3] if o[0] == v), str(v))
    if d[2] == 'bool':
        return 'sí' if v else 'no'
    return f"{v} {d[3].get('unidad', '')}".strip()
