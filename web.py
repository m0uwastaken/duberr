"""duberr · interfaz web (puerto 3005 por defecto). Sin dependencias: HTML del servidor y un poco de JS.

Seguridad (ver SECURITY.md): contraseña opcional (DUBERR_PASSWORD, HTTP Basic), comprobación de Origin/Referer en todos los
POST, webhook de Sonarr/Radarr con token propio, nada de claves en las respuestas. Interfaz estable para monitorizar:
GET /salud (texto «ok») y GET /api/estado (JSON).
"""
import base64, hashlib, hmac, html, json, os, re, socket, subprocess, threading, time, traceback
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse, quote

import ajustes as A
import comun as C
import nucleo as N
from arr import sonarr, radarr, env, mapeo

PUERTO = int(env('DUBERR_PORT', 'PUERTO', defecto='3005'))
BIND = env('DUBERR_BIND', defecto='0.0.0.0')
PASSWORD = env('DUBERR_PASSWORD')
ORIGENES_EXTRA = [x.strip().lower() for x in env('DUBERR_ALLOWED_ORIGINS').split(',') if x.strip()]   # host[:puerto] tras un proxy
MAX_CUERPO = 2_000_000
WEBHOOKS = {'/api/webhook/sonarr': 'sonarr', '/api/webhook/radarr': 'radarr'}
# eventType que hacen que duberr mire ese elemento ya, sin esperar al intervalo (Sonarr y Radarr usan los mismos nombres)
EVENTOS_DESPERTAR = {'download', 'rename', 'seriesadd', 'movieadded', 'manualinteractionrequired'}
ESPERA_IMPORTADO = 305          # s: el trabajador ignora lo importado hace menos de 300 s; segundo aviso justo después
_ctx = threading.local()
MESES = ('enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre')
esc = html.escape

# ------------------------------------------------------------------------------------------------ iconos (trazo 1.6)
_I = {
    'biblio': '<rect x="3" y="4" width="6" height="16" rx="1.5"/><rect x="11" y="4" width="4" height="16" rx="1"/><path d="M17.5 5.5l3 .8-3.6 13.6-3-.8"/>',
    'mas': '<path d="M12 5v14M5 12h14"/>',
    'act': '<path d="M3 12h4l2.5-6 5 12 2.5-6H21"/>',
    'ajustes': '<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/>',
    'buscar': '<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2"/>',
    'pausa': '<path d="M9 5v14M15 5v14"/>',
    'play': '<path d="M7 5l12 7-12 7z"/>',
    'papelera': '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
    'repetir': '<path d="M4 12a8 8 0 0 1 14-5.3L20 9M20 4v5h-5M20 12a8 8 0 0 1-14 5.3L4 15M4 20v-5h5"/>',
    'deshacer': '<path d="M9 14L4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
    'flecha': '<path d="M9 6l6 6-6 6"/>',
    'atras': '<path d="M15 6l-6 6 6 6"/>',
    'ok': '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    'aviso': '<path d="M12 4l9 16H3z"/><path d="M12 10v4M12 17.5v.01"/>',
    'x': '<path d="M6 6l12 12M18 6L6 18"/>',
    'calidad': '<path d="M12 3l2.6 5.6 6.1.7-4.5 4.2 1.2 6L12 16.6 6.6 19.5l1.2-6L3.3 9.3l6.1-.7z"/>',
    'onda': '<path d="M3 12h2M7 8v8M11 5v14M15 9v6M19 7v10M21 12h0"/>',
    'carpeta': '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
}


def ic(n, t=18):
    return (f'<svg class="i" width="{t}" height="{t}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            f'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{_I[n]}</svg>')


LOGO = ('<svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true"><rect width="24" height="24" rx="6" fill="var(--acc)"/>'
        '<path d="M6.5 9.5v5M10 7v10M13.5 9v6M17 11v2" stroke="var(--acc-tx)" stroke-width="2" stroke-linecap="round"/></svg>')

CSS = r"""
:root{--bg:#0b0b0d;--s1:#121215;--s2:#18181c;--s3:#202026;--line:#25252b;--line2:#33333b;--tx:#ececef;--tx2:#a3a3ad;--tx3:#6c6c76;
--acc:#e3b04b;--acc-tx:#17130a;--acc-s:rgba(227,176,75,.13);--ok:#58c48c;--ok-s:rgba(88,196,140,.13);--warn:#e99a4c;--warn-s:rgba(233,154,76,.14);
--err:#ee6b65;--err-s:rgba(238,107,101,.13);--info:#7ea8ff;--info-s:rgba(126,168,255,.13);--sh:0 10px 30px rgba(0,0,0,.35);--r:14px;--r2:9px;color-scheme:dark}
@media (prefers-color-scheme:light){:root{--bg:#f4f3ef;--s1:#fff;--s2:#f8f7f4;--s3:#efede8;--line:#e6e3dc;--line2:#d6d2c9;--tx:#16161a;--tx2:#55555e;--tx3:#8b8b94;
--acc:#a8740f;--acc-tx:#fff;--acc-s:rgba(168,116,15,.1);--ok:#1f8a55;--ok-s:rgba(31,138,85,.1);--warn:#b8641a;--warn-s:rgba(184,100,26,.1);--err:#c63b35;--err-s:rgba(198,59,53,.09);
--info:#2f62d0;--info-s:rgba(47,98,208,.09);--sh:0 8px 24px rgba(30,25,15,.08);color-scheme:light}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}[hidden]{display:none!important}
body{margin:0;background:var(--bg);color:var(--tx);font:15px/1.5 ui-sans-serif,-apple-system,"SF Pro Text","Segoe UI Variable Text","Segoe UI",Roboto,system-ui,sans-serif;
-webkit-font-smoothing:antialiased;font-feature-settings:"tnum" 1,"cv11" 1}
a{color:inherit;text-decoration:none}.i{flex:none;display:block}
.top{position:sticky;top:0;z-index:20;background:color-mix(in srgb,var(--bg) 82%,transparent);backdrop-filter:saturate(1.4) blur(14px);-webkit-backdrop-filter:saturate(1.4) blur(14px);border-bottom:1px solid var(--line)}
.top>div{max-width:1080px;margin:0 auto;padding:0 20px;height:58px;display:flex;align-items:center;gap:22px}
.marca{display:flex;align-items:center;gap:9px;font-weight:650;font-size:17px;letter-spacing:-.01em}
.nav{display:flex;gap:4px;margin-left:8px}.pestanas-m{display:none;position:fixed;left:0;right:0;bottom:0;z-index:30;justify-content:space-around;background:color-mix(in srgb,var(--s1) 90%,transparent);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);border-top:1px solid var(--line);padding:6px 6px calc(6px + env(safe-area-inset-bottom))}.pestanas-m a{display:flex;flex-direction:column;align-items:center;gap:3px;font-size:11px;font-weight:560;padding:6px 10px;flex:1;color:var(--tx3)}.pestanas-m a.on{color:var(--acc)}.nav a{display:flex;align-items:center;gap:7px;padding:7px 11px;border-radius:8px;color:var(--tx2);font-size:14px;font-weight:550}
.nav a:hover{color:var(--tx);background:var(--s2)}.nav a.on{color:var(--tx);background:var(--s3)}
.vivo{margin-left:auto;display:flex;align-items:center;gap:8px;font-size:13px;color:var(--tx2);min-width:0}
.vivo span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:46vw}
.punto{width:7px;height:7px;border-radius:9px;background:var(--tx3);flex:none}.punto.on{background:var(--acc);box-shadow:0 0 0 0 var(--acc-s);animation:latido 1.6s infinite}
@keyframes latido{0%{box-shadow:0 0 0 0 color-mix(in srgb,var(--acc) 55%,transparent)}100%{box-shadow:0 0 0 9px transparent}}
main{max-width:1080px;margin:0 auto;padding:26px 20px 90px}
h1{font-size:26px;line-height:1.2;letter-spacing:-.02em;margin:0;font-weight:680}h2{font-size:17px;margin:0;font-weight:640;letter-spacing:-.01em}
.sub{color:var(--tx2);font-size:14px;margin:4px 0 0}.eti{font-size:11.5px;letter-spacing:.07em;text-transform:uppercase;color:var(--tx3);font-weight:600}
.cab{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;margin:0 0 22px;flex-wrap:wrap}
.seccion{margin:34px 0 12px;display:flex;align-items:center;gap:10px}.seccion .eti{white-space:nowrap}.seccion::after{content:"";flex:1;height:1px;background:var(--line)}
.panel{background:var(--s1);border:1px solid var(--line);border-radius:var(--r)}
.fila{display:grid;grid-template-columns:76px 1fr auto;gap:18px;padding:16px;align-items:start;border-radius:var(--r);transition:background .15s}
a.fila:hover{background:var(--s2)}.lista>*+*{border-top:1px solid var(--line);border-radius:0}.lista>:first-child{border-radius:var(--r) var(--r) 0 0}.lista>:last-child{border-radius:0 0 var(--r) var(--r)}.lista>:only-child{border-radius:var(--r)}
.poster{width:76px;aspect-ratio:2/3;border-radius:7px;background:var(--s3) center/cover;box-shadow:0 0 0 1px var(--line) inset}
.titulo{font-weight:640;font-size:16.5px;letter-spacing:-.01em}.meta{color:var(--tx3);font-size:13px;margin-top:2px}
.badge{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:600;padding:3px 9px;border-radius:99px;background:var(--s3);color:var(--tx2);white-space:nowrap}
.badge::before{content:"";width:6px;height:6px;border-radius:9px;background:currentColor}
.b-castellano{background:var(--warn-s);color:var(--warn)}.b-calidad{background:var(--info-s);color:var(--info)}.b-terminado{background:var(--ok-s);color:var(--ok)}
.b-solo_castellano{background:var(--acc-s);color:var(--acc)}.b-pausado{color:var(--tx3)}
.cifras{display:grid;grid-template-columns:repeat(3,minmax(0,150px));gap:22px;margin-top:14px}
.cifra .n{font-size:13px;color:var(--tx2);display:flex;justify-content:space-between;gap:8px}.cifra .n b{color:var(--tx);font-weight:600}
.barra{height:3px;border-radius:3px;background:var(--line);margin-top:6px;overflow:hidden}.barra i{display:block;height:100%;border-radius:3px}
.mapa{margin-top:14px;display:grid;gap:5px}.mapa .t{display:flex;align-items:center;gap:8px}.mapa .t>span{font-size:11.5px;color:var(--tx3);width:24px;font-weight:600}
.celdas{display:flex;flex-wrap:wrap;gap:3px}.celdas i{width:11px;height:11px;border-radius:3px;background:var(--line2)}
.e-ok{background:var(--ok)!important}.e-cola{background:var(--info)!important}.e-espera{background:var(--acc)!important;opacity:.55}.e-falta{background:var(--warn)!important}
.e-fallo{background:var(--err)!important}.e-vacio{background:var(--line2)!important}
.leyenda{display:flex;gap:16px;flex-wrap:wrap;font-size:12.5px;color:var(--tx3);margin:10px 0 0}.leyenda span{display:flex;align-items:center;gap:6px}.leyenda i{width:9px;height:9px;border-radius:3px;display:block}
.ahora{display:flex;align-items:center;gap:16px;padding:16px 18px;margin-bottom:8px;overflow:hidden;position:relative}
.ahora .onda{color:var(--acc)}.ahora .q{font-weight:600}.ahora .p{color:var(--tx2);font-size:13.5px}
.ahora::after{content:"";position:absolute;left:0;bottom:0;height:2px;width:30%;background:linear-gradient(90deg,transparent,var(--acc),transparent);animation:barrido 2.2s ease-in-out infinite}
@keyframes barrido{0%{left:-30%}100%{left:100%}}
.btn{display:inline-flex;align-items:center;gap:7px;height:36px;padding:0 14px;border-radius:var(--r2);border:1px solid var(--line2);background:var(--s1);color:var(--tx);font:inherit;font-size:13.5px;font-weight:560;cursor:pointer;white-space:nowrap}
.btn:hover{background:var(--s2);border-color:color-mix(in srgb,var(--line2) 60%,var(--tx3))}.btn.pri{background:var(--acc);border-color:var(--acc);color:var(--acc-tx)}.btn.pri:hover{filter:brightness(1.06)}
.btn.peligro{color:var(--err)}.btn.peq{height:30px;padding:0 10px;font-size:12.5px}.btn.solo{width:32px;padding:0;justify-content:center;height:30px}
.btns{display:flex;gap:8px;flex-wrap:wrap}form.in{display:inline}
.vacio{padding:56px 20px;text-align:center}.vacio p{color:var(--tx2);max-width:420px;margin:8px auto 20px}
/* ficha */
.heroe{position:relative;border-radius:var(--r);overflow:hidden;border:1px solid var(--line);background:var(--s1)}
.heroe .fondo{position:absolute;inset:0;background:center/cover;opacity:.22;filter:blur(2px) saturate(1.1);transform:scale(1.04)}
.heroe .fondo::after{content:"";position:absolute;inset:0;background:linear-gradient(90deg,var(--s1) 18%,color-mix(in srgb,var(--s1) 55%,transparent))}
.heroe>.cont{position:relative;display:grid;grid-template-columns:132px 1fr;gap:24px;padding:24px}
.heroe .poster{width:132px}.volver{display:inline-flex;align-items:center;gap:4px;color:var(--tx2);font-size:13.5px;margin-bottom:14px}.volver:hover{color:var(--tx)}
table{width:100%;border-collapse:collapse}th{font-size:11.5px;letter-spacing:.06em;text-transform:uppercase;color:var(--tx3);font-weight:600;text-align:left;padding:10px 14px;border-bottom:1px solid var(--line)}
td{padding:11px 14px;border-bottom:1px solid var(--line);vertical-align:middle;font-size:14px}tr:last-child td{border-bottom:0}
td.num{color:var(--tx3);font-weight:600;width:56px;font-size:13px}.ept{font-weight:540}.ept small{display:block;color:var(--tx3);font-size:12.5px;font-weight:400}
.estado{display:inline-flex;align-items:center;gap:7px;font-size:13px;color:var(--tx2);white-space:nowrap}.estado i{width:8px;height:8px;border-radius:3px;display:block}
.linea{position:relative;height:8px;border-radius:4px;background:var(--line);overflow:hidden;min-width:120px}.linea i{position:absolute;top:0;bottom:0}
.l-es{background:var(--ok)}.l-orig{background:var(--warn)}.l-fuera{background:repeating-linear-gradient(135deg,var(--err) 0 3px,transparent 3px 6px)}
details.ep>summary{list-style:none;cursor:pointer}details.ep>summary::-webkit-details-marker{display:none}
.desp{padding:4px 14px 18px;color:var(--tx2);font-size:13.5px}.desp .kv{display:grid;grid-template-columns:150px 1fr;gap:4px 14px;margin:8px 0 14px}.desp .kv b{color:var(--tx3);font-weight:500}
.mono{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-size:12.5px}
/* formularios */
.grupo{padding:20px 22px}.grupo+.grupo{border-top:1px solid var(--line)}.grupo h3{margin:0;font-size:15px;font-weight:640}.grupo>p{margin:3px 0 14px;color:var(--tx3);font-size:13px}
.campo{display:grid;grid-template-columns:minmax(0,1fr) 260px;gap:20px;padding:12px 0;align-items:center}.campo+.campo{border-top:1px dashed var(--line)}
.campo label{font-weight:560;font-size:14px}.campo .ay{color:var(--tx3);font-size:12.8px;margin-top:2px;line-height:1.45}
select,input[type=number],input[type=search],input[type=text]{width:100%;height:36px;border-radius:var(--r2);border:1px solid var(--line2);background:var(--s2);color:var(--tx);font:inherit;font-size:14px;padding:0 11px}
select{appearance:none;-webkit-appearance:none;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%23888' stroke-width='2'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right 11px center;padding-right:30px}
select:focus,input:focus{outline:2px solid color-mix(in srgb,var(--acc) 50%,transparent);outline-offset:1px}
.unidad{display:flex;align-items:center;gap:8px}.unidad span{color:var(--tx3);font-size:13px;min-width:30px}
.interruptor{position:relative;display:inline-block;width:40px;height:24px;flex:none;vertical-align:middle}.interruptor input{opacity:0;position:absolute;inset:0;margin:0;cursor:pointer;z-index:1}
.interruptor i{position:absolute;inset:0;border-radius:20px;background:var(--line2);transition:.15s}.interruptor i::after{content:"";position:absolute;width:18px;height:18px;left:3px;top:3px;border-radius:20px;background:#fff;transition:.15s}
.interruptor input:checked+i{background:var(--acc)}.interruptor input:checked+i::after{left:19px}
.heredado{font-size:12px;color:var(--tx3);margin-top:4px}.cambiado label::after{content:" ·";color:var(--acc);font-weight:800}
.presets{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:18px}.preset{padding:14px 16px;text-align:left;cursor:pointer;font:inherit;color:inherit}
.preset:hover{border-color:var(--line2);background:var(--s2)}.preset.on{border-color:var(--acc);box-shadow:0 0 0 1px var(--acc) inset}
.presets.mini{margin-bottom:12px;gap:8px}.presets.mini .preset{padding:10px 12px}.presets.mini .preset span{font-size:12px}.presets-f .ay{color:var(--tx3);font-size:12.8px}.preset b{display:block;font-size:14px}.preset span{color:var(--tx3);font-size:12.8px}
/* añadir */
.herram{display:flex;gap:10px;align-items:center;margin-bottom:18px;flex-wrap:wrap}.herram .busca{position:relative;flex:1;min-width:220px}
.herram .busca .i{position:absolute;left:11px;top:9px;color:var(--tx3)}.herram .busca input{padding-left:36px}
.pestanas{display:inline-flex;background:var(--s2);border:1px solid var(--line);border-radius:10px;padding:3px}.pestanas button{border:0;background:none;color:var(--tx2);font:inherit;font-size:13.5px;font-weight:560;padding:6px 14px;border-radius:7px;cursor:pointer}
.pestanas button.on{background:var(--s1);color:var(--tx);box-shadow:0 1px 2px rgba(0,0,0,.25)}
.rejilla{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:22px 16px}
.tarjeta{cursor:pointer;background:none;border:0;padding:0;text-align:left;color:inherit;font:inherit}.tarjeta .poster{width:100%;border-radius:10px;transition:transform .2s,box-shadow .2s;position:relative}
.tarjeta:hover .poster{transform:translateY(-2px);box-shadow:var(--sh),0 0 0 1px var(--line) inset}.tarjeta b{display:block;font-size:14px;font-weight:600;margin-top:9px;line-height:1.3}
.tarjeta span{font-size:12.5px;color:var(--tx3)}.tarjeta .marca-d{position:absolute;top:8px;right:8px;background:var(--acc);color:var(--acc-tx);border-radius:99px;padding:2px 8px;font-size:11px;font-weight:700}
dialog{border:1px solid var(--line2);border-radius:16px;background:var(--s1);color:var(--tx);padding:0;width:min(460px,calc(100vw - 32px));box-shadow:0 30px 80px rgba(0,0,0,.5)}
dialog::backdrop{background:rgba(0,0,0,.55);backdrop-filter:blur(3px)}dialog .dc{padding:22px}dialog .pie{display:flex;justify-content:flex-end;gap:8px;padding:14px 22px;border-top:1px solid var(--line);background:var(--s2)}
.temps{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:8px;margin:16px 0 4px}.temps label{display:flex;align-items:center;gap:9px;padding:10px 12px;border:1px solid var(--line2);border-radius:var(--r2);cursor:pointer;font-size:14px}
.temps label:has(input:checked){border-color:var(--acc);background:var(--acc-s)}.temps input{accent-color:var(--acc);width:16px;height:16px;margin:0}.temps small{margin-left:auto;color:var(--tx3);font-size:12px}
.nota{display:flex;gap:12px;padding:14px 16px;font-size:13.5px;color:var(--tx2);margin-bottom:22px}.nota .i{color:var(--acc);margin-top:2px}.nota b{color:var(--tx)}
/* actividad */
.act{display:grid;grid-template-columns:28px 1fr auto;gap:12px;padding:13px 16px;align-items:start}.act .ico{width:28px;height:28px;border-radius:8px;display:grid;place-items:center}
.act .ico.ok{background:var(--ok-s);color:var(--ok)}.act .ico.ko{background:var(--err-s);color:var(--err)}.act .q{font-size:14px}.act .q b{font-weight:600}
.act .d{font-size:12.8px;color:var(--tx3);margin-top:2px;word-break:break-word}.act .h{font-size:12.5px;color:var(--tx3);white-space:nowrap}
.dia{margin:26px 0 8px}.filtros{display:flex;gap:6px}.filtros a{padding:6px 12px;border-radius:99px;font-size:13px;color:var(--tx2);border:1px solid var(--line)}.filtros a.on{background:var(--tx);color:var(--bg);border-color:var(--tx)}
details.otras>summary{cursor:pointer;list-style:none}details.otras>summary::-webkit-details-marker{display:none}details.otras>summary .eti::before{content:'▸ ';}details.otras[open]>summary .eti::before{content:'▾ '}
.previa{margin:6px 0 16px;padding:14px;border:1px solid var(--line);border-radius:12px;background:var(--s2)}
.previa-cab{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:10px}.previa .pestanas button{padding:5px 11px;font-size:12.5px}
.saltos{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px}.chip{border:1px solid var(--line2);background:var(--s1);color:var(--tx);font:inherit;font-size:12.5px;font-weight:550;padding:5px 10px;border-radius:99px;cursor:pointer}
.chip span{color:var(--tx3);font-weight:500;margin-left:3px}.chip:hover{border-color:var(--acc)}
.previa .pos{width:100%;accent-color:var(--acc);margin:0 0 10px}.pantalla{position:relative;border-radius:10px;overflow:hidden;background:#000;aspect-ratio:16/9}
.pantalla video{width:100%;height:100%;display:block}.pantalla .est{position:absolute;inset:0;display:grid;place-items:center;text-align:center;padding:18px 26px;color:#c9c9cf;font-size:13px;line-height:1.5;background:rgba(0,0,0,.72);pointer-events:none}
.previa.cargando .est::before{content:"";display:block;width:22px;height:22px;margin:0 auto 10px;border-radius:50%;border:2px solid #555;border-top-color:var(--acc);animation:gira .8s linear infinite}
@keyframes gira{to{transform:rotate(360deg)}}
.dl{display:flex;flex-direction:column;gap:3px;margin-top:10px}.dl div{font-size:12.8px;color:var(--tx3)}
.aviso{display:flex;gap:10px;align-items:flex-start;padding:10px 12px;border-radius:var(--r2);background:var(--warn-s);color:var(--warn);font-size:13.5px;margin-top:12px}
.aviso.err{background:var(--err-s);color:var(--err)}
.datos{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--line);border-radius:var(--r);overflow:hidden;border:1px solid var(--line);margin-bottom:8px}
.datos>div{background:var(--s1);padding:14px 16px}.datos b{display:block;font-size:22px;font-weight:650;letter-spacing:-.02em}.datos span{font-size:12.5px;color:var(--tx3)}
@media (max-width:760px){
 .top>div{height:52px;padding:0 16px}.top .nav{display:none}.pestanas-m{display:flex}
 main{padding:20px 16px 110px}h1{font-size:23px}.fila{grid-template-columns:56px 1fr;gap:14px;padding:14px}.poster{width:56px}.fila>.der{grid-column:1/-1;display:none}
 .cifras{grid-template-columns:repeat(3,1fr);gap:12px}.heroe>.cont{grid-template-columns:84px 1fr;gap:16px;padding:18px}.heroe .poster{width:84px}
 .campo{grid-template-columns:1fr;gap:8px}.presets{grid-template-columns:1fr}.presets.mini{grid-template-columns:repeat(3,1fr)}.presets.mini .preset span{display:none}.datos{grid-template-columns:repeat(2,1fr)}
 .rejilla{grid-template-columns:repeat(3,1fr);gap:16px 10px}.tarjeta b{font-size:12.5px}.oc-m{display:none}td,th{padding:10px 10px}
 .desp .kv{grid-template-columns:1fr}.vivo span{max-width:52vw}
}
"""

# ------------------------------------------------------------------------------------------------ vista previa
PREVIA = f'{C.CACHE}/previa'
LARGO_PREVIA = 24
_previa_sem = threading.Semaphore(2)


def generar_previa(k, clave, t, modo):
    """Clip corto (480-540p H.264 + AAC) para escuchar el resultado en el navegador. modo: es | orig | ambos
    (castellano por el oído izquierdo y original por el derecho: si hay desfase se oye eco)."""
    it = C.cfg()['items'].get(k)
    e = next((x for x in C.episodios_cache(it) if x['clave'] == clave), None) if it else None
    if not e or not e['path'] or not os.path.exists(e['path']):
        raise ValueError('no hay archivo')
    st = os.stat(e['path'])
    fuente = e['path']
    if modo == 'orig':                                   # recortado: el original completo, en el punto equivalente
        orig = original_completo(it, e)
        if orig:
            fuente, t = orig[0], a_original(t, orig[1])
    h = hashlib.sha1(f"{fuente}|{st.st_ino}|{st.st_size}|{t:.1f}|{modo}".encode()).hexdigest()[:20]
    os.makedirs(PREVIA, exist_ok=True)
    out = f'{PREVIA}/{h}.mp4'
    if os.path.exists(out):
        return out
    with _previa_sem:
        if os.path.exists(out):
            return out
        j = N.info(fuente)
        spa = [x['id'] for x in N.pistas_castellano(j)]
        ref = N.pistas_referencia(j, it.get('idioma_original'))
        d = N.duracion(j)
        t = max(0.0, min(t, d - 3))
        v = '[0:v:0]scale=-2:\'min(540,ih)\',format=yuv420p[v]'
        if modo == 'ambos' and spa and ref:
            a = (f'[0:{spa[0]}]aformat=channel_layouts=mono[a1];[0:{ref[0]}]aformat=channel_layouts=mono[a2];'
                 '[a1][a2]join=inputs=2:channel_layout=stereo[a]')
        else:
            pista = spa[0] if (modo == 'es' and spa) or not ref else ref[0]
            a = f'[0:{pista}]aformat=channel_layouts=stereo[a]'
        tmp = out + '.tmp.mp4'
        r = subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-ss', f'{t:.2f}', '-t', str(LARGO_PREVIA), '-i', fuente,
                            '-filter_complex', f'{v};{a}', '-map', '[v]', '-map', '[a]', '-map_chapters', '-1', '-map_metadata', '-1', '-sn', '-dn', '-c:v', 'libx264', '-preset', 'veryfast',
                            '-crf', '27', '-profile:v', 'high', '-level', '4.0', '-c:a', 'aac', '-b:a', '160k',
                            '-movflags', '+faststart', '-f', 'mp4', tmp], capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise RuntimeError('ffmpeg: ' + r.stderr[-300:])
        os.replace(tmp, out)
    viejos = sorted((os.path.getmtime(os.path.join(PREVIA, f)), f) for f in os.listdir(PREVIA) if f.endswith('.mp4'))
    for mt, f in viejos[:-40] + [x for x in viejos[-40:] if time.time() - x[0] > 86400]:
        try:
            os.remove(os.path.join(PREVIA, f))
        except OSError:
            pass
    return out


def original_completo(it, e):
    """Si el archivo actual está recortado y el original sigue en la papelera: (ruta del original, partes conservadas)."""
    try:
        v = C.estado_de(C.leer(C.ESTADO, {}).get(C.clave_archivo(e['path'])))
    except OSError:
        return None
    if v and v.get('partes'):
        guardados = C.papelera_de(it, e['clave'])
        if guardados:
            return guardados[0][0], v['partes']
    return None


def a_original(t, partes):
    """Instante del vídeo recortado -> instante en el original."""
    if t <= 0.5:                                         # «Principio»: también lo recortado al empezar
        return 0.0
    acc = 0.0
    for a, b in partes:
        if t < acc + (b - a):
            return a + t - acc
        acc += b - a
    return partes[-1][1] if partes else t


def previa_html(k, e, v, it=None):
    """Reproductor de la ficha: saltos a los puntos delicados, barra para ir a cualquier punto y tres modos de audio."""
    partes = (v or {}).get('partes')
    d = ((v or {}).get('d') or e.get('duracion') or 0)
    if v and v.get('quitado'):
        d -= v['quitado']
    if not d:
        return ''
    saltos = [('Principio', 0.0)]
    if v and v.get('r') == 'mezclado' and v.get('piezas'):
        es_p = [p for p in v['piezas'] if p[0] == 'es']
        if es_p and es_p[0][1] > 2:
            x = N.a_salida(max(0.0, es_p[0][1] - 6), partes)
            if x is not None:
                saltos.append(('Empieza el castellano', x))
        for tc, _ in (v.get('tramos') or [])[1:]:
            x = N.a_salida(max(0.0, tc - 8), partes)
            if x is not None:
                saltos.append(('Corte', x))
        for tp, a, b in v['piezas']:
            if tp == 'orig' and b - a > N.RELLENO_MIN and a > 2:
                x = N.a_salida(max(0.0, a - 6), partes)
                if x is not None:
                    saltos.append(('Recortado' if partes and N.a_salida(a + 1, partes) is None else 'Audio original', x))
    saltos += [('Mitad', d / 2), ('Final', max(0.0, d - 120))]
    vistos, botones = set(), []
    for et, x in sorted(saltos, key=lambda s: s[1]):
        if round(x / 5) in vistos:
            continue
        vistos.add(round(x / 5))
        botones.append(f'<button type="button" class="chip" data-t="{x:.1f}">{esc(et)} <span>{N.mmss(x)}</span></button>')
    tiene_es = (v and v.get('r') in ('mezclado', 'origen', 'ya_spa')) or _spa_archivo(e)
    nota_orig = ''
    if partes:
        if it and original_completo(it, e):
            nota_orig = ' «Original» enseña la versión completa, sin recortar, en el mismo punto.'
        else:
            nota_orig = ' El original completo no está en la papelera: «Original» enseña el audio original del archivo ya recortado.'
    modos = (('es', 'Castellano'), ('ambos', 'Los dos'), ('orig', 'Original')) if tiene_es else (('orig', 'Original'),)
    return (f'<div class="previa" data-k="{esc(k)}" data-c="{e["clave"]}"><div class="previa-cab"><span class="eti">Vista previa</span>'
            f'<div class="pestanas modos">' + ''.join(f'<button type="button" data-m="{m}" class="{"on" if i == 0 else ""}">{t}</button>'
                                                      for i, (m, t) in enumerate(modos)) + '</div></div>'
            f'<div class="saltos">{"".join(botones)}</div>'
            f'<input type="range" class="pos" min="0" max="{int(d)}" step="1" value="0" aria-label="Posición">'
            f'<div class="pantalla"><video playsinline controls preload="none"></video><div class="est">Elige un punto: se prepara un clip de '
            f'{LARGO_PREVIA} s (tarda unos segundos). «Los dos» pone el castellano a la izquierda y el original a la derecha: '
            f'si están sincronizados suenan como uno; si no, se oye eco.{nota_orig}</div></div></div>')


JS = r"""
const $=(s,e=document)=>e.querySelector(s),$$=(s,e=document)=>[...e.querySelectorAll(s)];
async function vivo(){try{const r=await fetch('/api/vivo');const v=await r.json();const t=$('#vivo-t'),p=$('#vivo-p');
 if(v.trabajando){t.textContent=v.trabajando+(v.paso?' · '+v.paso:'');p.classList.add('on')}else{t.textContent=v.cola?`${v.cola} en cola`:'En espera'+(v.ultima_pasada?' · '+v.ultima_pasada:'');p.classList.remove('on')}
 const a=$('#ahora');if(a){if(v.trabajando){a.hidden=false;$('.q',a).textContent=v.trabajando;$('.p',a).textContent=(v.paso||'')+(v.desde?' · '+mmss(Date.now()/1000-v.desde):'')}else{if(!a.hidden)location.reload()}}
}catch(e){}}
function mmss(s){s=Math.max(0,Math.round(s));return Math.floor(s/60)+':'+String(s%60).padStart(2,'0')}
setInterval(vivo,4000);vivo();
function previa(box,t){const v=$('video',box),est=$('.est',box),m=$('.modos .on',box).dataset.m;box.dataset.t=t;$('.pos',box).value=t;
 est.hidden=false;est.textContent='Preparando el clip desde '+mmss(t)+'…';box.classList.add('cargando');
 v.src=`/previa?k=${encodeURIComponent(box.dataset.k)}&c=${box.dataset.c}&t=${t}&a=${m}`;v.load();v.play().catch(()=>{});
 v.oncanplay=()=>{box.classList.remove('cargando');est.hidden=true};
 v.onerror=()=>{box.classList.remove('cargando');est.hidden=false;est.textContent='No se pudo preparar el clip.'}}
document.addEventListener('click',e=>{const b=e.target.closest('.previa .chip');if(b){previa(b.closest('.previa'),+b.dataset.t);return}
 const m=e.target.closest('.previa .modos button');if(m){const box=m.closest('.previa');$$('.modos button',box).forEach(x=>x.classList.toggle('on',x===m));
 if(box.dataset.t!==undefined)previa(box,+box.dataset.t)}});
document.addEventListener('change',e=>{if(e.target.matches('.previa .pos'))previa(e.target.closest('.previa'),+e.target.value)});
document.addEventListener('submit',e=>{const m=e.target.dataset.confirmar;if(m&&!confirm(m))e.preventDefault()});
document.addEventListener('click',e=>{const b=e.target.closest('[data-abrir]');if(b){const d=document.getElementById(b.dataset.abrir);d&&d.showModal()}
 const c=e.target.closest('[data-cerrar]');if(c){c.closest('dialog').close()}});
const busca=$('#busca');if(busca){const f=()=>{const q=busca.value.toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g,'');const tab=$('.pestanas .on')?.dataset.tab;
 $$('.tarjeta').forEach(t=>{t.hidden=!(t.dataset.n.includes(q)&&(!tab||t.dataset.tipo===tab))})};busca.addEventListener('input',f);
 $$('.pestanas button').forEach(b=>b.addEventListener('click',()=>{$$('.pestanas button').forEach(x=>x.classList.remove('on'));b.classList.add('on');f()}));f()}
"""


def pagina(titulo, cuerpo, activa=''):
    nav = ''.join(f'<a href="{u}" class="{"on" if u == activa else ""}">{ic(i, 19)}<span>{t}</span></a>'
                  for u, i, t in (('/', 'biblio', 'Biblioteca'), ('/anadir', 'mas', 'Añadir'),
                                  ('/actividad', 'act', 'Actividad'), ('/ajustes', 'ajustes', 'Ajustes')))
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="theme-color" content="#0b0b0d">
<title>{esc(titulo)} · duberr</title><link rel="icon" href="/favicon.svg"><style>{CSS}</style></head><body>
<header class="top"><div><a class="marca" href="/">{LOGO}duberr</a><nav class="nav">{nav}</nav>
<div class="vivo"><i class="punto" id="vivo-p"></i><span id="vivo-t">…</span></div></div></header>
<main>{cuerpo}</main><nav class="pestanas-m">{nav}</nav><script>{JS}</script></body></html>"""


def boton(ruta, campos, texto, clase='btn peq', icono=None, confirmar=None):
    ocultos = ''.join(f'<input type="hidden" name="{esc(k)}" value="{esc(str(v))}">' for k, v in campos.items())
    conf = f' data-confirmar="{esc(confirmar)}"' if confirmar else ''
    return (f'<form class="in" method="post" action="{ruta}"{conf}>{ocultos}'
            f'<button class="{clase}">{ic(icono, 16) if icono else ""}{texto}</button></form>')


def img(tipo, id_, clase='poster'):
    return f'/img/{tipo}/{int(id_)}/{clase}'


def tipo_arr(it):
    return 'sonarr' if it['tipo'] == 'serie' else 'radarr'


# ------------------------------------------------------------------------------------------------ estado por episodio
def _spa_archivo(e):
    a = (e.get('audio') or '').lower()
    return 'spa' in a.split('/') or 'spanish' in a


def estado_ep(it, e, estado):
    """('ok'|'cola'|'espera'|'falta'|'fallo'|'vacio', texto, entrada_estado)."""
    if not e['path']:
        return 'vacio', 'Sin archivo', None
    v = None
    if e['path'] and os.path.exists(e['path']):
        try:
            v = estado.get(C.clave_archivo(e['path']))
        except OSError:
            v = None
    v = C.estado_de(v) if v else None
    guardado = os.path.exists(C.ruta_audio(it, e['clave']))
    if v:
        r = v.get('r')
        if r == 'mezclado':
            return 'ok', 'Mezclado', v
        if v.get('era_mezcla'):
            return 'ok', 'Mezclado · el último rehacer falló', v
        if r in ('origen', 'ya_spa'):
            return 'ok', 'Con castellano', v
        if r == 'sin_sincronizar':
            return 'fallo', 'No sincroniza', v
        if r == 'error':
            return 'fallo', 'Error', v
        if r == 'deshecho':
            return 'espera', 'Deshecho', v
    if _spa_archivo(e):
        return 'ok', 'Con castellano', v
    if guardado:
        if e['calidad'] in C.OBJETIVO or it.get('modo') == 'solo_castellano' or it.get('fase') == 'terminado':
            return 'cola', 'Por mezclar', v
        return 'espera', 'Esperando calidad', v
    return 'falta', 'Falta castellano', v


ESTADOS = (('ok', 'Con castellano'), ('cola', 'Por mezclar'), ('espera', 'Esperando calidad'), ('falta', 'Falta castellano'),
           ('fallo', 'No sincroniza'), ('vacio', 'Sin archivo'))


def leyenda():
    return '<div class="leyenda">' + ''.join(f'<span><i class="e-{k}"></i>{t}</span>' for k, t in ESTADOS) + '</div>'


def linea(v, d=None):
    """Barra de tiempo de una mezcla: castellano, audio original y lo recortado."""
    if not v or not v.get('piezas'):
        return ''
    d = d or v.get('d') or max(b for _, a, b in v['piezas'])
    if not d:
        return ''
    out = []
    partes = v.get('partes')
    for t, a, b in v['piezas']:
        cls = 'l-es' if t == 'es' else 'l-orig'
        if partes and t == 'orig' and not any(pa <= a + 0.5 and b - 0.5 <= pb for pa, pb in partes):
            cls = 'l-fuera'
        out.append(f'<i class="{cls}" style="left:{100 * a / d:.2f}%;width:{max(0.4, 100 * (b - a) / d):.2f}%"></i>')
    tit = 'verde: castellano · naranja: audio original' + (' · rayado: recortado' if partes else '')
    return f'<div class="linea" title="{tit}">{"".join(out)}</div>'


# ------------------------------------------------------------------------------------------------ vistas
def ahora_html():
    v = C.vivo
    t = v.get('trabajando') or ''
    return (f'<div class="panel ahora" id="ahora" {"hidden" if not t else ""}>{ic("onda", 22)}<div><div class="eti">Ahora</div>'
            f'<div class="q">{esc(t)}</div><div class="p">{esc(v.get("paso") or "")}</div></div></div>')


def resumen_item(it, estado):
    eps = [e for e in C.episodios_cache(it) if C.elegido(it, e)]
    est = [(e, estado_ep(it, e, estado)[0]) for e in eps]
    return eps, est


def vista_biblioteca():
    c = C.cfg()
    estado = C.leer(C.ESTADO, {})
    cab = ('<div class="cab"><div><h1>Biblioteca</h1><p class="sub">Lo que duberr está poniendo en castellano y en la mejor calidad.</p></div>'
           '<a class="btn pri" href="/anadir">' + ic('mas', 16) + 'Añadir</a></div>')
    if not c['items']:
        return pagina('Biblioteca', cab + f'{ahora_html()}<div class="panel vacio">{ic("onda", 30)}<h2 style="margin-top:12px">Nada en marcha</h2>'
                      '<p>Elige series, temporadas o películas y duberr buscará el castellano, la mejor calidad y los juntará.</p>'
                      '<a class="btn pri" href="/anadir">Elegir qué doblar</a></div>', '/')
    grupos = {'activo': [], 'fin': []}
    total = {'eps': 0, 'ok': 0, 'fallo': 0, 'cola': 0}
    for k, it in sorted(c['items'].items(), key=lambda x: x[1]['titulo'].lower()):
        try:
            eps, est = resumen_item(it, estado)
        except Exception as ex:
            eps, est = [], []
            it['error'] = C.sin_claves(ex)
        n = len(eps) or 1
        cuenta = {s: sum(1 for _, x in est if x == s) for s, _ in ESTADOS}
        total['eps'] += len(eps)
        total['ok'] += cuenta['ok']
        total['fallo'] += cuenta['fallo']
        total['cola'] += cuenta['cola']
        r = it.get('resumen', {})
        fase = it.get('fase', '')
        solo = it.get('modo') == 'solo_castellano'
        cifras = [('Castellano', r.get('con_castellano', 0), 'var(--acc)'),
                  ('Calidad' if not solo else 'Por mezclar', r.get('en_calidad', 0) if not solo else cuenta['cola'], 'var(--info)'),
                  ('Con castellano', cuenta['ok'], 'var(--ok)')]
        cif = ''.join(f'<div class="cifra"><div class="n"><span>{t}</span><b>{v}/{len(eps)}</b></div>'
                      f'<div class="barra"><i style="width:{100 * v / n:.0f}%;background:{col}"></i></div></div>' for t, v, col in cifras)
        porT = {}
        for e, s in est:
            porT.setdefault(e['temporada'], []).append((e, s))
        mapa = ''.join(f'<div class="t"><span>{"T" + str(t) if t is not None else ""}</span><div class="celdas">' +
                       ''.join(f'<i class="e-{s}" title="{e["clave"]} · {dict(ESTADOS)[s]}"></i>' for e, s in xs) + '</div></div>'
                       for t, xs in sorted(porT.items(), key=lambda x: x[0] if x[0] is not None else -1)) if it['tipo'] == 'serie' else ''
        temps = ('T' + ', T'.join(map(str, it.get('temporadas', [])))) if it['tipo'] == 'serie' and it.get('temporadas') else ''
        meta = ' · '.join(x for x in ('Serie' if it['tipo'] == 'serie' else 'Película', str(it.get('anio') or ''), temps,
                                      'solo añadir castellano' if solo else '') if x)
        avisos = ''
        if cuenta['fallo']:
            avisos = f'<div class="aviso err">{ic("aviso", 16)}{cuenta["fallo"]} sin sincronizar: entra para reintentarlo con otros ajustes</div>'
        elif it.get('error'):
            avisos = f'<div class="aviso err">{ic("aviso", 16)}{esc(C.sin_claves(it["error"])[:160])}</div>'
        elif it.get('aviso'):
            avisos = f'<div class="aviso">{ic("aviso", 16)}{esc(it["aviso"])}</div>'
        fila = (f'<a class="fila" href="/item?k={quote(k)}"><div class="poster" style="background-image:url({img(tipo_arr(it), it["id"])})"></div>'
                f'<div style="min-width:0"><div class="titulo">{esc(it["titulo"])}</div><div class="meta">{esc(meta)}</div>'
                f'<div class="cifras">{cif}</div><div class="mapa">{mapa}</div>{avisos}</div>'
                f'<div class="der"><span class="badge b-{fase}">{C.FASES.get(fase, fase)}</span></div></a>')
        grupos['fin' if fase == 'terminado' else 'activo'].append(fila)
    datos = (f'<div class="datos"><div><b>{len(c["items"])}</b><span>series y películas</span></div>'
             f'<div><b>{total["ok"]}</b><span>con castellano</span></div><div><b>{total["cola"]}</b><span>por mezclar</span></div>'
             f'<div><b>{total["fallo"]}</b><span>sin sincronizar</span></div></div>')
    cuerpo = cab + ahora_html() + datos
    if grupos['activo']:
        cuerpo += '<div class="seccion"><span class="eti">En marcha</span></div><div class="panel lista">' + ''.join(grupos['activo']) + '</div>'
    if grupos['fin']:
        cuerpo += '<div class="seccion"><span class="eti">Terminado</span></div><div class="panel lista">' + ''.join(grupos['fin']) + '</div>'
    return pagina('Biblioteca', cuerpo + leyenda(), '/')


def _campos_ajustes(valores, base, ambito, prefijo=''):
    """Formulario de ajustes. valores: lo puesto en esta capa; base: lo heredado (para enseñarlo)."""
    out = []
    for nombre, ayuda_g, defs in A.GRUPOS:
        filas = []
        for k, et, tipo, opc, ayuda, amb in defs:
            if (ambito == 'general' and amb == 'concreto') or (ambito != 'general' and amb == 'general'):
                continue
            v = valores.get(k)
            hered = base.get(k)
            nm = prefijo + k
            if tipo == 'select':
                ops = ([f'<option value="">Heredar ({esc(A.texto(k, hered))})</option>'] if ambito != 'general' else [])
                ops += [f'<option value="{esc(o)}" {"selected" if v == o or (ambito == "general" and v is None and hered == o) else ""}>{esc(t)}</option>' for o, t in opc]
                ctrl = f'<select name="{nm}">{"".join(ops)}</select>'
            elif tipo == 'bool':
                if ambito == 'general':
                    on = v if v is not None else hered
                    ctrl = f'<input type="hidden" name="{nm}" value="no"><label class="interruptor"><input type="checkbox" name="{nm}" value="si" {"checked" if on else ""}><i></i></label>'
                else:
                    ops = [('', f'Heredar ({A.texto(k, hered)})'), ('si', 'Sí'), ('no', 'No')]
                    sel = '' if v is None else ('si' if v else 'no')
                    ctrl = f'<select name="{nm}">' + ''.join(f'<option value="{o}" {"selected" if o == sel else ""}>{t}</option>' for o, t in ops) + '</select>'
            else:
                val = v if v is not None else (hered if ambito == 'general' and hered is not None else '')
                ph = f'placeholder="{esc(str(hered))}"' if ambito != 'general' and hered not in (None, '') else ('placeholder="vacío"' if opc.get('vacio') else '')
                ctrl = (f'<div class="unidad"><input type="number" name="{nm}" value="{esc(str(val))}" {ph} min="{opc["min"]}" max="{opc["max"]}" '
                        f'step="{opc["paso"]}" inputmode="decimal"><span>{esc(opc.get("unidad", ""))}</span></div>')
            cambiado = v is not None and (ambito != 'general' or v != A.FABRICA.get(k))
            filas.append(f'<div class="campo{" cambiado" if cambiado else ""}"><div><label>{esc(et)}</label><div class="ay">{esc(ayuda)}</div></div><div>{ctrl}</div></div>')
        if filas:
            out.append(f'<div class="grupo"><h3>{esc(nombre)}</h3>{f"<p>{esc(ayuda_g)}</p>" if ayuda_g else ""}{"".join(filas)}</div>')
    return ''.join(out)


def presets_html(accion, ocultos, actual, titulo=''):
    """Botones de preajuste (Recomendado / Si falla / Estricto). actual: ajustes de esta capa, para marcar el activo."""
    campos = ''.join(f'<input type="hidden" name="{esc(x)}" value="{esc(str(y))}">' for x, y in ocultos.items())
    bts = ''.join(f'<button class="panel preset{" on" if (actual or {}) == vals else ""}" name="preset" value="{p}"><b>{esc(t)}</b><span>{esc(d)}</span></button>'
                  for p, (t, d, vals) in A.PRESETS.items())
    cab = f'<p class="ay" style="margin:0 0 8px">{esc(titulo)}</p>' if titulo else ''
    return f'<form method="post" action="{accion}" class="presets-f">{campos}{cab}<div class="presets mini">{bts}</div></form>'


def vista_item(k):
    c = C.cfg()
    it = c['items'].get(k)
    if not it:
        return pagina('No está', '<div class="panel vacio"><h2>No está en duberr</h2><p><a class="btn" href="/">Volver</a></p></div>')
    estado = C.leer(C.ESTADO, {})
    eps = C.episodios_cache(it, 5)
    fase = it.get('fase', '')
    solo = it.get('modo') == 'solo_castellano'
    t = tipo_arr(it)
    acciones = [] if solo else [boton('/accion', {'a': 'buscar', 'k': k}, 'Buscar ahora', 'btn', 'buscar')]
    if not solo and fase != 'castellano' and (it.get('resumen') or {}).get('sin_castellano', 1):
        acciones.append(boton('/accion', {'a': 'castellano', 'k': k}, 'Buscar castellano', 'btn', 'buscar',
                              'Se pide a Sonarr/Radarr el castellano de los episodios que aún no lo tienen, aunque ya esté en calidad. ¿Seguir?'))
    if fase == 'castellano':
        acciones.append(boton('/accion', {'a': 'calidad', 'k': k}, 'Pasar ya a calidad', 'btn', 'calidad',
                              'Se dejará de buscar el castellano que falta y se buscará la mejor calidad. ¿Seguir?'))
    mezclados = [e for e in eps if e['path'] and estado_ep(it, e, estado)[2] and estado_ep(it, e, estado)[2].get('r') == 'mezclado']
    if mezclados:
        acciones.append(boton('/accion', {'a': 'rehacer_todo', 'k': k}, f'Rehacer {len(mezclados)}', 'btn', 'repetir',
                              f'Se vuelven a medir y mezclar los {len(mezclados)} episodios ya mezclados con los ajustes actuales '
                              '(por ejemplo, para recortar lo que no tiene castellano). Tarda unos minutos por episodio. ¿Seguir?'))
    acciones.append(boton('/accion', {'a': 'reanudar' if fase == 'pausado' else 'pausar', 'k': k},
                          'Reanudar' if fase == 'pausado' else 'Pausar', 'btn', 'play' if fase == 'pausado' else 'pausa'))
    acciones.append(boton('/accion', {'a': 'quitar', 'k': k}, 'Quitar', 'btn peligro', 'x',
                          f'¿Quitar {it["titulo"]} de duberr? Se le devuelve su perfil de calidad; no se borra nada.'))
    temps = ('Temporadas ' + ', '.join(map(str, it.get('temporadas', [])))) if it['tipo'] == 'serie' else 'Película'
    idioma = it.get('idioma_original')
    meta = ' · '.join(x for x in ('Serie' if it['tipo'] == 'serie' else 'Película', str(it.get('anio') or ''), temps,
                                  f'original en {idioma}' if idioma else '') if x)
    heroe = (f'<a class="volver" href="/">{ic("atras", 16)}Biblioteca</a><div class="heroe"><div class="fondo" style="background-image:url({img(t, it["id"], "fanart")})"></div>'
             f'<div class="cont"><div class="poster" style="background-image:url({img(t, it["id"])})"></div><div style="min-width:0">'
             f'<span class="badge b-{fase}">{C.FASES.get(fase, fase)}</span><h1 style="margin-top:10px">{esc(it["titulo"])}</h1><p class="sub">{esc(meta)}</p>'
             f'<div class="btns" style="margin-top:18px">{"".join(acciones)}</div>'
             + (f'<div class="aviso">{ic("carpeta", 16)}<div>¿Tienes episodios en castellano? Déjalos en <b>{esc(C.ENTRADA)}/{esc(it["titulo"])}</b>'
                f' (con SxxEyy en el nombre) y duberr sacará la pista.</div></div>' if solo or (it.get('resumen') or {}).get('sin_castellano') else '')
             + '</div></div></div>')
    pend = {(a['clave'], a['accion']) for a in C.acciones_pendientes() if a['item'] == k}
    secciones, otras = [], []
    porT = {}
    for e in eps:
        if e['temporada'] == 0 and not C.elegido(it, e) and not os.path.exists(C.ruta_audio(it, e['clave'])):
            continue
        porT.setdefault(e['temporada'], []).append(e)
    for temp, lista in sorted(porT.items(), key=lambda x: (x[0] is None, x[0] if x[0] is not None else 0)):
        filas = []
        for e in lista:
            s, txt, v = estado_ep(it, e, estado)
            ajep = ((it.get('eps') or {}).get(e['clave']) or {}).get('ajustes') or {}
            en_cola = [a for (cl, a) in pend if cl == e['clave']]
            if en_cola:
                txt = {'reintentar': 'Reintento en cola', 'rehacer': 'En cola para rehacer', 'deshacer': 'En cola para deshacer'}.get(en_cola[0], 'En cola')
            det = []
            if v:
                if v.get('txt'):
                    det.append(('Resultado', esc(v['txt'])))
                if v.get('fecha'):
                    det.append(('Fecha', esc(v['fecha']) + (f" · {esc(v.get('motivo'))}" if v.get('motivo') else '')))
                if v.get('tramos'):
                    det.append(('Tramos', '<span class="mono">' + ' → '.join(f'{N.mmss(a)} {l:+.2f}s' for a, l in v['tramos']) + '</span>'))
                if v.get('calidad'):
                    q = v['calidad']
                    det.append(('Medición', f"{q.get('fuertes')} ventanas fiables · nitidez {q.get('z_mediana')} · cobertura {round(100 * (q.get('cobertura') or 0))} %"))
                if v.get('comprobacion') and not v['comprobacion'].get('omitida'):
                    det.append(('Comprobación', f"{v['comprobacion'].get('bien')} puntos bien, {v['comprobacion'].get('mal')} mal"))
                if v.get('papelera'):
                    det.append(('Original', 'guardado en la papelera (se puede deshacer)'))
            if ajep:
                det.append(('Ajustes propios', esc(', '.join(f'{A.DEF[x][1].lower()}: {A.texto(x, y)}' for x, y in ajep.items() if x in A.DEF))))
            if e['path']:
                det.append(('Archivo', f'<span class="mono">{esc(os.path.basename(e["path"]))}</span>'))
            hay_audio = os.path.exists(C.ruta_audio(it, e['clave']))
            bts = []
            if hay_audio and e['path']:
                if s == 'fallo':
                    bts.append(boton('/episodio', {'k': k, 'c': e['clave'], 'a': 'reintentar'}, 'Reintentar', 'btn peq', 'repetir'))
                if v and (v.get('r') == 'mezclado' or v.get('era_mezcla')):
                    bts.append(boton('/episodio', {'k': k, 'c': e['clave'], 'a': 'rehacer'}, 'Rehacer', 'btn peq', 'repetir',
                                     'Se vuelve a medir y mezclar con los ajustes actuales. ¿Seguir?'))
                if v and (v.get('r') == 'mezclado' or v.get('era_mezcla')):
                    bts.append(boton('/episodio', {'k': k, 'c': e['clave'], 'a': 'deshacer'}, 'Deshacer', 'btn peq', 'deshacer',
                                     'Se devuelve el archivo como estaba antes de duberr (o se quita la pista en castellano). ¿Seguir?'))
                if v and v.get('r') == 'deshecho':
                    bts.append(boton('/episodio', {'k': k, 'c': e['clave'], 'a': 'reintentar'}, 'Volver a mezclar', 'btn peq', 'repetir'))
            form_aj = ''
            if hay_audio and e['path']:
                base = A.efectivos(c.get('ajustes'), it)
                form_aj = (f'<details style="margin-top:12px"><summary class="btn peq" style="display:inline-flex">{ic("ajustes", 16)}Ajustes de este episodio</summary>'
                           f'<div style="margin-top:10px">' + presets_html('/episodio', {'k': k, 'c': e['clave'], 'a': 'preset'}, ajep,
                                                                          'Volver a mezclar este episodio con un preajuste:') + '</div>'
                           f'<form method="post" action="/episodio" class="panel" style="margin-top:10px"><input type="hidden" name="k" value="{esc(k)}">'
                           f'<input type="hidden" name="c" value="{e["clave"]}"><input type="hidden" name="a" value="ajustes">'
                           f'{_campos_ajustes(ajep, base, "episodio", "aj_")}<div class="pie" style="display:flex;justify-content:flex-end;gap:8px;padding:14px 22px;border-top:1px solid var(--line)">'
                           f'<button class="btn pri">{ic("repetir", 16)}Guardar y volver a mezclar</button></div></form></details>')
            dl = ''.join(f'<b>{x}</b><div>{y}</div>' for x, y in det)
            cabecera = (f'<table><tr><td class="num">{e["episodio"] if e["episodio"] is not None else "—"}</td>'
                        f'<td class="ept">{esc(e["titulo"] or "")}<small>{esc(e["calidad"] or "sin archivo")}</small></td>'
                        f'<td class="oc-m" style="width:30%">{linea(v) if v and v.get("r") == "mezclado" else ""}</td>'
                        f'<td style="width:170px;text-align:right"><span class="estado"><i class="e-{s}"></i>{esc(txt)}</span></td></tr></table>')
            filas.append(f'<details class="ep"{" open" if len(eps) == 1 else ""}><summary>{cabecera}</summary><div class="desp">'
                         f'{linea(v) if v and v.get("r") == "mezclado" else ""}<div class="kv">{dl}</div>'
                         f'{previa_html(k, e, v, it) if e["path"] else ""}<div class="btns">{"".join(bts)}</div>{form_aj}</div></details>')
        titulo_t = ('Especiales' if temp == 0 else f'Temporada {temp}') if it['tipo'] == 'serie' else 'Película'
        elegida = it['tipo'] == 'pelicula' or temp in it.get('temporadas', [])
        bloque = (f'<div class="seccion"><span class="eti">{titulo_t}</span></div>'
                  f'<div class="panel lista">{"".join(f"<div>{x}</div>" for x in filas)}</div>')
        (secciones if elegida else otras).append(bloque)
    base = A.efectivos(c.get('ajustes'))
    ajustes = (f'<div class="seccion" id="ajustes"><span class="eti">Ajustes de {"esta serie" if it["tipo"] == "serie" else "esta película"}</span></div>'
               + presets_html('/ajustes', {'ambito': 'item', 'k': k}, it.get('ajustes') or {}) +
               f'<form method="post" action="/ajustes" class="panel"><input type="hidden" name="ambito" value="item"><input type="hidden" name="k" value="{esc(k)}">'
               f'<div class="grupo" style="padding-bottom:6px"><p style="margin:0">Vacío = lo de <a href="/ajustes" style="color:var(--acc)">Ajustes</a>. '
               f'Si cambias algo, los episodios que no sincronizaron se reintentan solos.</p></div>'
               f'{_campos_ajustes(it.get("ajustes") or {}, base, "item")}'
               f'<div style="display:flex;justify-content:flex-end;gap:8px;padding:14px 22px;border-top:1px solid var(--line)">'
               f'<button class="btn pri">Guardar</button></div></form>')
    if otras:
        secciones.append(f'<details class="otras"><summary class="seccion"><span class="eti">Otras temporadas ({len(otras)}) · no elegidas</span></summary>'
                         + ''.join(otras) + '</details>')
    return pagina(it['titulo'], heroe + leyenda() + ''.join(secciones) + ajustes)


def vista_anadir():
    c = C.cfg()
    try:
        series = sorted(sonarr.get('series'), key=lambda s: s.get('sortTitle') or s['title'])
        pelis = sorted(radarr.get('movie'), key=lambda m: m.get('sortTitle') or m['title'])
    except Exception as ex:
        return pagina('Añadir', f'<div class="panel vacio"><h2>No puedo hablar con Sonarr o Radarr</h2><p>{esc(C.sin_claves(ex))}</p></div>', '/anadir')
    import unicodedata

    def norm(t):
        return ''.join(ch for ch in unicodedata.normalize('NFD', t.lower()) if unicodedata.category(ch) != 'Mn')
    tarjetas, dialogos = [], []
    for s in series:
        k = f"sonarr:{s['id']}"
        ya = c['items'].get(k)
        st = s.get('statistics', {})
        temps = [x for x in s['seasons'] if x['seasonNumber'] > 0] + [x for x in s['seasons'] if x['seasonNumber'] == 0]
        did = f"d-s{s['id']}"
        cajas = ''.join(
            f'<label><input type="checkbox" name="t" value="{x["seasonNumber"]}" {"checked" if ya and x["seasonNumber"] in ya.get("temporadas", []) else ""}>'
            f'{"Especiales" if x["seasonNumber"] == 0 else "T" + str(x["seasonNumber"])}<small>{x.get("statistics", {}).get("episodeFileCount", 0)}/{x.get("statistics", {}).get("totalEpisodeCount", 0)}</small></label>'
            for x in temps)
        tarjetas.append(f'<button class="tarjeta" data-abrir="{did}" data-tipo="serie" data-n="{esc(norm(s["title"] + " " + str(s.get("year", ""))))}">'
                        f'<div class="poster" style="background-image:url({img("sonarr", s["id"])})">{"<span class=marca-d>en duberr</span>" if ya else ""}</div>'
                        f'<b>{esc(s["title"])}</b><span>{s.get("year", "")} · {st.get("episodeFileCount", 0)}/{st.get("totalEpisodeCount", 0)} ep.</span></button>')
        dialogos.append(f'<dialog id="{did}"><form method="post" action="/anadir"><div class="dc"><div class="eti">Serie · {s.get("year", "")}</div>'
                        f'<h2 style="font-size:20px;margin-top:4px">{esc(s["title"])}</h2><p class="sub">Elige las temporadas que quieres en castellano y en la mejor calidad. '
                        f'El castellano se guarda de todas.</p><input type="hidden" name="k" value="{k}"><div class="temps">{cajas}</div></div>'
                        f'<div class="pie"><button type="button" class="btn" data-cerrar>Cancelar</button><button class="btn pri">{"Guardar" if ya else "Empezar"}</button></div></form></dialog>')
    for m in pelis:
        k = f"radarr:{m['id']}"
        ya = c['items'].get(k)
        did = f"d-p{m['id']}"
        tarjetas.append(f'<button class="tarjeta" data-abrir="{did}" data-tipo="pelicula" data-n="{esc(norm(m["title"] + " " + str(m.get("year", ""))))}">'
                        f'<div class="poster" style="background-image:url({img("radarr", m["id"])})">{"<span class=marca-d>en duberr</span>" if ya else ""}</div>'
                        f'<b>{esc(m["title"])}</b><span>{m.get("year", "")} · {"descargada" if m.get("hasFile") else "sin descargar"}</span></button>')
        cuerpo_d = ('<p class="sub">Ya está en duberr.</p>' if ya else
                    '<p class="sub">duberr buscará una versión en castellano, guardará su audio y después buscará la mejor calidad para mezclarlo.</p>')
        boton_d = (f'<a class="btn pri" href="/item?k={quote(k)}">Abrir</a>' if ya else '<button class="btn pri">Empezar</button>')
        dialogos.append(f'<dialog id="{did}"><form method="post" action="/anadir"><div class="dc"><div class="eti">Película · {m.get("year", "")}</div>'
                        f'<h2 style="font-size:20px;margin-top:4px">{esc(m["title"])}</h2>{cuerpo_d}<input type="hidden" name="k" value="{k}"></div>'
                        f'<div class="pie"><button type="button" class="btn" data-cerrar>Cancelar</button>{boton_d}</div></form></dialog>')
    cuerpo = ('<div class="cab"><div><h1>Añadir</h1><p class="sub">Series y películas de Sonarr y Radarr.</p></div></div>'
              f'<div class="panel nota">{ic("carpeta", 18)}<div><b>Solo añadir castellano</b> sin cambiar tu versión: deja los episodios en castellano '
              f'(cualquier calidad) en <b>{esc(C.ENTRADA)}/&lt;Título tal cual en Sonarr o Radarr&gt;/</b> (dentro del volumen de datos de duberr), con el episodio en el nombre '
              '(S01E05, 1x05, Cap.105; especiales S00E01). duberr saca la pista, la sincroniza y la mezcla.</div></div>'
              f'<div class="herram"><div class="busca">{ic("buscar", 17)}<input type="search" id="busca" placeholder="Buscar título" autocomplete="off"></div>'
              '<div class="pestanas"><button data-tab="" class="on">Todo</button><button data-tab="serie">Series</button><button data-tab="pelicula">Películas</button></div></div>'
              f'<div class="rejilla">{"".join(tarjetas)}</div>{"".join(dialogos)}')
    return pagina('Añadir', cuerpo, '/anadir')


def vista_actividad(filtro='todo'):
    r = C.leer(C.RESULTADOS, [])
    if filtro == 'mezclas':
        r = [x for x in r if 'mezclado' in x['texto'] or 'deshecho' in x['texto']]
    elif filtro == 'errores':
        r = [x for x in r if not x['ok']]
    fil = ''.join(f'<a href="/actividad?f={f}" class="{"on" if f == filtro else ""}">{t}</a>'
                  for f, t in (('todo', 'Todo'), ('mezclas', 'Mezclas'), ('errores', 'Problemas')))
    cab = f'<div class="cab"><div><h1>Actividad</h1><p class="sub">Lo último que ha hecho duberr.</p></div><div class="filtros">{fil}</div></div>'
    if not r:
        return pagina('Actividad', cab + '<div class="panel vacio"><p>Nada por aquí todavía.</p></div>', '/actividad')
    hoy = time.strftime('%Y-%m-%d')
    ayer = time.strftime('%Y-%m-%d', time.localtime(time.time() - 86400))
    out, dia = [cab], None
    filas = []
    for x in r[:300]:
        d = x['fecha'][:10]
        if d != dia:
            if filas:
                out.append('<div class="panel lista">' + ''.join(filas) + '</div>')
                filas = []
            dia = d
            nombre = 'Hoy' if d == hoy else 'Ayer' if d == ayer else f'{int(d[8:])} de {MESES[int(d[5:7]) - 1]}'
            out.append(f'<div class="seccion dia"><span class="eti">{esc(nombre)}</span></div>')
        ep = x['ep'] if x['ep'] != 'pelicula' else ''
        enlace = f'/item?k={quote(x["k"])}' if x.get('k') else None
        qu = f'<b>{esc(x["item"])}</b>{" · " + esc(ep) if ep else ""}'
        quien = f'<a href="{enlace}">{qu}</a>' if enlace else qu
        detalle = f'<div class="d">{esc((x.get("detalle") or "")[:260])}</div>' if x.get('detalle') else ''
        filas.append(f'<div class="act"><div class="ico {"ok" if x["ok"] else "ko"}">{ic("ok" if x["ok"] else "aviso", 15)}</div>'
                     f'<div style="min-width:0"><div class="q">{quien}</div>'
                     f'<div class="q" style="color:var(--tx2);font-size:13.5px">{esc(x["texto"])}</div>{detalle}</div>'
                     f'<div class="h">{x["fecha"][11:]}</div></div>')
    if filas:
        out.append('<div class="panel lista">' + ''.join(filas) + '</div>')
    return pagina('Actividad', ''.join(out), '/actividad')


def vista_ajustes():
    c = C.cfg()
    g = c.get('ajustes') or {}
    pap = C.papelera_resumen()
    presets = presets_html('/ajustes', {'ambito': 'general'}, {x: y for x, y in g.items() if x not in ('papelera_dias', 'avisos')})
    cuerpo = ('<div class="cab"><div><h1>Ajustes</h1><p class="sub">Cómo sincroniza y mezcla duberr. Valen para todo; cada serie, película o '
              'episodio puede cambiarlos en su ficha cuando algo falle.</p></div></div>'
              f'{presets}'
              f'<form method="post" action="/ajustes" class="panel"><input type="hidden" name="ambito" value="general">'
              f'{_campos_ajustes(g, A.FABRICA, "general")}'
              f'<div style="display:flex;justify-content:flex-end;gap:8px;padding:14px 22px;border-top:1px solid var(--line);background:var(--s2);border-radius:0 0 var(--r) var(--r)">'
              f'<button class="btn pri">Guardar ajustes</button></div></form>'
              '<div class="seccion"><span class="eti">Papelera</span></div>'
              f'<div class="panel grupo"><p style="margin:0">{pap["archivos"]} archivos guardados · ocupan {pap["ocupa"] / 1e9:.1f} GB propios '
              f'({pap["total"] / 1e9:.1f} GB en total; el resto lo comparten con otros enlaces duros). Están en <span class="mono">{esc(C.PAPELERA)}</span> '
              'y se borran solos al caducar.</p></div>' + integracion_html())
    return pagina('Ajustes', cuerpo, '/ajustes')


def integracion_html():
    """Datos para conectar Sonarr/Radarr (webhook) y estado de la configuración. No muestra claves de Sonarr/Radarr."""
    tok = esc(C.token_webhook())
    host = esc(getattr(_ctx, 'host', '') or f'localhost:{PUERTO}')
    si = lambda b: 'sí' if b else 'no'
    pares = ' · '.join(f'{esc(a)} ⇒ {esc(b)}' for a, b in mapeo()) or 'ninguno'
    filas = [('Sonarr', f'clave de API configurada: {si(sonarr.ok())}'), ('Radarr', f'clave de API configurada: {si(radarr.ok())}'),
             ('Contraseña de la web', si(PASSWORD)), ('Mapeo de rutas', pares),
             ('Carpetas protegidas', esc(' · '.join(C.PROTEGIDAS) or 'ninguna')), ('Carpeta de medios', esc(C.MEDIA))]
    kv = ''.join(f'<b>{k}</b><div>{v}</div>' for k, v in filas)
    return ('<div class="seccion"><span class="eti">Integración con Sonarr y Radarr</span></div>'
            '<div class="panel grupo"><p style="margin:0 0 10px">En Sonarr/Radarr: Ajustes → Conectar → Webhook, método POST, y marca '
            '«Al importar», «Al importar una actualización», «Al renombrar» y «Al añadir». Así duberr reacciona al momento, sin esperar al intervalo. '
            'Cambia el servidor por el que ve tu Sonarr/Radarr (p. ej. <span class="mono">http://duberr:3005</span>).</p>'
            f'<div class="desp"><div class="kv"><b>Webhook Sonarr</b><div class="mono">http://{host}/api/webhook/sonarr?token={tok}</div>'
            f'<b>Webhook Radarr</b><div class="mono">http://{host}/api/webhook/radarr?token={tok}</div>'
            f'<b>Token</b><div class="mono">{tok}</div></div>'
            '<p style="margin:0 0 10px">El token también vale como cabecera <span class="mono">X-Api-Key</span>. '
            'Se puede fijar con <span class="mono">DUBERR_WEBHOOK_TOKEN</span>.</p>'
            f'<div class="kv">{kv}</div></div></div>')


# ------------------------------------------------------------------------------------------------ acciones
def accion_web(campos):
    k = campos.get('k', [''])[0]
    a = campos.get('a', [''])[0]
    if a == 'rehacer_todo':
        it = C.cfg()['items'].get(k)
        if it:
            estado = C.leer(C.ESTADO, {})
            for e in C.episodios(it):
                v = estado_ep(it, e, estado)[2]
                if e['path'] and v and v.get('r') == 'mezclado':
                    C.pedir_accion(k, e['clave'], 'rehacer')
        return f'/item?k={quote(k)}'

    def fn(c):
        it = c['items'].get(k)
        if not it:
            return
        if a == 'buscar':
            it['buscar_pedido'] = it.get('buscar_pedido', 0) + 1
        elif a == 'castellano':
            it['castellano_pedido'] = it.get('castellano_pedido', 0) + 1
        elif a == 'calidad':
            it['forzar_calidad'] = True
        elif a == 'pausar' and it.get('fase') != 'pausado':
            it['fase_antes_pausa'] = it.get('fase')
            it['fase'] = 'pausado'
        elif a == 'reanudar':
            it['fase'] = it.get('fase_antes_pausa') or ('solo_castellano' if it.get('modo') == 'solo_castellano' else 'calidad')
        elif a == 'quitar':
            try:                                         # devolverle su perfil original
                arr_ = sonarr if it['tipo'] == 'serie' else radarr
                ruta = f"{'series' if it['tipo'] == 'serie' else 'movie'}/{it['id']}"
                if it.get('perfil_original'):
                    obj = arr_.get(ruta, crudo=True)
                    obj['qualityProfileId'] = it['perfil_original']
                    arr_.put(ruta, obj)
            except Exception as ex:
                C.log('no pude devolver el perfil original', ex)
            del c['items'][k]
            C.log(it['titulo'], 'quitado de duberr')
    C.cambiar_cfg(fn)
    C.despertar.set()
    return '/' if a == 'quitar' else f'/item?k={quote(k)}'


def anadir_web(campos):
    k = campos.get('k', [''])[0]
    tipo, id_ = k.split(':')
    id_ = int(id_)
    temps = sorted(int(x) for x in campos.get('t', []))
    titulo = sonarr.get(f'series/{id_}')['title'] if tipo == 'sonarr' else radarr.get(f'movie/{id_}')['title']

    def fn(c):
        if k in c['items']:
            it = c['items'][k]
            if tipo == 'sonarr':
                it['temporadas'] = temps
                it['buscar_pedido'] = it.get('buscar_pedido', 0) + 1
                if it.get('fase') == 'terminado':
                    it['fase'] = 'castellano'
                if it.get('modo') == 'solo_castellano' and temps:
                    it.pop('modo', None)
                    it['fase'] = 'castellano'
            return True
        if tipo == 'sonarr' and not temps:
            return False
        c['items'][k] = {'tipo': 'serie' if tipo == 'sonarr' else 'pelicula', 'id': id_, 'titulo': titulo,
                         'fase': 'castellano', 'creado': time.strftime('%Y-%m-%d %H:%M'), 'buscar_pedido': 1,
                         'buscar_hecho': 0, 'perfil_original': None}
        if tipo == 'sonarr':
            c['items'][k]['temporadas'] = temps
        C.log(titulo, 'añadido a duberr', temps)
        return True
    ok = C.cambiar_cfg(fn)
    C.despertar.set()
    return f'/item?k={quote(k)}' if ok else '/anadir'


def ajustes_web(campos):
    ambito = campos.get('ambito', ['general'])[0]
    preset = campos.get('preset', [''])[0]
    if ambito == 'general':
        if preset in A.PRESETS:
            nuevos = dict(A.PRESETS[preset][2])
            def fn(c):
                pap, av = c['ajustes'].get('papelera_dias'), c['ajustes'].get('avisos')
                c['ajustes'] = {**nuevos, **({'papelera_dias': pap} if pap is not None else {}), **({'avisos': av} if av else {})}
        else:
            vals = A.limpiar({k: v[-1] for k, v in campos.items()}, 'general')
            # en los generales solo se guarda lo que difiere de fábrica
            vals = {k: v for k, v in vals.items() if v != A.FABRICA.get(k)}
            def fn(c):
                c['ajustes'] = vals
        C.cambiar_cfg(fn)
        C.despertar.set()
        return '/ajustes'
    k = campos.get('k', [''])[0]
    vals = A.limpiar({x: v[-1] for x, v in campos.items()}, 'item')
    if preset in A.PRESETS:                      # preajuste para esta serie o película (Recomendado = heredar)
        vals = dict(A.PRESETS[preset][2])

    def fn(c):
        if k in c['items']:
            c['items'][k]['ajustes'] = vals
    C.cambiar_cfg(fn)
    C.despertar.set()
    return f'/item?k={quote(k)}#ajustes'


def episodio_web(campos):
    k = campos.get('k', [''])[0]
    clave = campos.get('c', [''])[0]
    a = campos.get('a', [''])[0]
    if a == 'ajustes':
        vals = A.limpiar({x[3:]: v[-1] for x, v in campos.items() if x.startswith('aj_')}, 'episodio')
        C.pedir_accion(k, clave, 'reintentar', vals)
    elif a == 'preset' and campos.get('preset', [''])[0] in A.PRESETS:
        C.pedir_accion(k, clave, 'reintentar', dict(A.PRESETS[campos['preset'][0]][2]))
    elif a in ('reintentar', 'rehacer', 'deshacer'):
        C.pedir_accion(k, clave, a)
    return f'/item?k={quote(k)}'


FAVICON = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><rect width="24" height="24" rx="6" fill="#e3b04b"/>'
           '<path d="M6.5 9.5v5M10 7v10M13.5 9v6M17 11v2" stroke="#17130a" stroke-width="2" stroke-linecap="round"/></svg>')


class Web(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _enviar(self, cuerpo, tipo='text/html; charset=utf-8', codigo=200, cache=None, extra=()):
        b = cuerpo if isinstance(cuerpo, bytes) else cuerpo.encode()
        self.send_response(codigo)
        self.send_header('Content-Type', tipo)
        self.send_header('Content-Length', str(len(b)))
        self.send_header('Cache-Control', cache or 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'same-origin')
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(b)

    def _json(self, obj, codigo=200):
        return self._enviar(json.dumps(obj, ensure_ascii=False), 'application/json', codigo)

    # --- seguridad ---
    def _token(self):
        """Token del webhook que trae la petición: cabecera X-Api-Key o parámetro ?token=."""
        return self.headers.get('X-Api-Key') or parse_qs(urlparse(self.path).query).get('token', [''])[0]

    def _autorizado(self, ruta):
        """Contraseña (HTTP Basic) si hay DUBERR_PASSWORD. /salud y los webhooks (que llevan su token) no la piden."""
        if not PASSWORD or ruta == '/salud' or ruta in WEBHOOKS:
            return True
        cab = self.headers.get('Authorization') or ''
        if cab.lower().startswith('basic '):
            try:
                _, _, pw = base64.b64decode(cab[6:].strip()).decode('utf-8', 'replace').partition(':')
                if hmac.compare_digest(pw.encode(), PASSWORD.encode()):
                    return True
            except ValueError:
                pass
        time.sleep(0.5)                                   # frena la fuerza bruta
        self._enviar('Hace falta contraseña', 'text/plain; charset=utf-8', 401, extra=[('WWW-Authenticate', 'Basic realm="duberr", charset="UTF-8"')])
        return False

    def _origen_valido(self):
        """POST desde el navegador: Origin (o Referer) debe ser esta misma web. Sin ninguno de los dos, solo con token válido."""
        o = self.headers.get('Origin') or self.headers.get('Referer')
        if not o:
            return C.token_valido(self._token())
        host = (urlparse(o).netloc or '').lower()
        return bool(host) and (host == (self.headers.get('Host') or '').lower() or host in ORIGENES_EXTRA)

    def _webhook(self, tipo):
        if not C.token_valido(self._token()):
            return self._json({'error': 'token no válido'}, 401)
        try:
            largo = int(self.headers.get('Content-Length') or 0)
            if largo > MAX_CUERPO:
                return self._json({'error': 'cuerpo demasiado grande'}, 413)
            datos = json.loads(self.rfile.read(largo) or b'{}')
            if not isinstance(datos, dict):
                raise ValueError('no es un objeto')
        except ValueError:
            return self._json({'error': 'JSON no válido'}, 400)
        return self._json(webhook_arr(tipo, datos))

    def _archivo(self, ruta, tipo):
        tam = os.path.getsize(ruta)
        a, b, codigo = 0, tam - 1, 200
        m = re.match(r'bytes=(\d*)-(\d*)', self.headers.get('Range') or '')
        if m and (m[1] or m[2]):
            if m[1]:
                a, b = int(m[1]), int(m[2]) if m[2] else tam - 1
            else:
                a = max(0, tam - int(m[2]))
            b, codigo = min(b, tam - 1), 206
        self.send_response(codigo)
        self.send_header('Content-Type', tipo)
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Length', str(b - a + 1))
        self.send_header('Cache-Control', 'max-age=3600')
        if codigo == 206:
            self.send_header('Content-Range', f'bytes {a}-{b}/{tam}')
        self.end_headers()
        try:
            with open(ruta, 'rb') as fh:
                fh.seek(a)
                resto = b - a + 1
                while resto > 0:
                    trozo = fh.read(min(1 << 20, resto))
                    if not trozo:
                        break
                    self.wfile.write(trozo)
                    resto -= len(trozo)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        u = urlparse(self.path)
        ruta, q = u.path, parse_qs(u.query)
        if not self._autorizado(ruta):
            return
        _ctx.host = self.headers.get('Host') or ''
        try:
            if ruta == '/salud':
                return self._enviar('ok', 'text/plain')
            if ruta in WEBHOOKS:
                return self._json({'error': 'usa POST'}, 405)
            if ruta == '/favicon.svg':
                return self._enviar(FAVICON, 'image/svg+xml', cache='max-age=86400')
            if ruta == '/api/vivo':
                return self._enviar(json.dumps({**C.vivo, 'cola': len(C.acciones_pendientes())}, ensure_ascii=False), 'application/json')
            if ruta == '/api/estado':
                return self._enviar(json.dumps({'version': C.VERSION, 'trabajando': C.vivo['trabajando'], 'paso': C.vivo['paso'],
                                                'ultima_pasada': C.vivo['ultima_pasada'], 'cola': len(C.acciones_pendientes()), 'items': {
                    k: {x: v.get(x) for x in ('titulo', 'fase', 'resumen')} for k, v in C.cfg()['items'].items()}},
                    ensure_ascii=False), 'application/json')
            if ruta.startswith('/img/'):
                _, _, tipo, id_, clase = ruta.split('/')
                if tipo not in ('sonarr', 'radarr') or clase not in ('poster', 'fanart'):
                    return self._enviar('no', 'text/plain', 404)
                datos = C.caratula(tipo, int(id_), clase)
                if not datos:
                    return self._enviar('', 'text/plain', 404)
                return self._enviar(datos, 'image/jpeg', cache='max-age=86400')
            if ruta == '/previa':
                try:
                    t = float(q.get('t', ['0'])[0])
                except ValueError:
                    t = 0.0
                modo = q.get('a', ['es'])[0]
                ruta_mp4 = generar_previa(q.get('k', [''])[0], q.get('c', [''])[0], t, modo if modo in ('es', 'orig', 'ambos') else 'es')
                return self._archivo(ruta_mp4, 'video/mp4')
            if ruta == '/item':
                return self._enviar(vista_item(q.get('k', [''])[0]))
            if ruta == '/actividad':
                return self._enviar(vista_actividad(q.get('f', ['todo'])[0]))
            vista = {'/': vista_biblioteca, '/anadir': vista_anadir, '/ajustes': vista_ajustes,
                     '/resultados': lambda: vista_actividad()}.get(ruta)
            if not vista:
                return self._enviar(pagina('No existe', '<div class="panel vacio"><h2>Esta página no existe</h2><p><a class="btn" href="/">Volver</a></p></div>'), codigo=404)
            self._enviar(vista())
        except Exception as ex:
            traceback.print_exc()
            self._enviar(pagina('Error', f'<div class="panel vacio"><h2>Algo ha fallado</h2><p class="mono">{esc(C.sin_claves(ex))}</p></div>'), codigo=500)

    def do_HEAD(self):
        if urlparse(self.path).path == '/salud':
            return self._enviar('ok', 'text/plain')
        self._enviar('', 'text/plain', 405)

    def do_POST(self):
        ruta = urlparse(self.path).path
        if ruta in WEBHOOKS:
            return self._webhook(WEBHOOKS[ruta])
        if not self._autorizado(ruta):
            return
        if not self._origen_valido():
            return self._enviar('Petición rechazada: el origen no coincide con esta web.', 'text/plain; charset=utf-8', 403)
        try:
            largo = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            largo = -1
        if not 0 <= largo <= MAX_CUERPO:
            return self._enviar('Cuerpo no válido', 'text/plain; charset=utf-8', 413, extra=[('Connection', 'close')])
        campos = parse_qs(self.rfile.read(largo).decode('utf-8', 'replace'), keep_blank_values=True)
        destino = '/'
        try:
            if ruta == '/accion':
                destino = accion_web(campos)
            elif ruta == '/anadir':
                destino = anadir_web(campos)
            elif ruta == '/ajustes':
                destino = ajustes_web(campos)
            elif ruta == '/episodio':
                destino = episodio_web(campos)
        except Exception as ex:
            traceback.print_exc()
            return self._enviar(pagina('Error', f'<div class="panel vacio"><h2>Algo ha fallado</h2><p class="mono">{esc(C.sin_claves(ex))}</p></div>'), codigo=500)
        self.send_response(303)
        self.send_header('Location', destino)
        self.end_headers()


def webhook_arr(tipo, datos):
    """Evento de Sonarr/Radarr (Ajustes → Conectar → Webhook). Test responde sin más; los eventos de importación, renombrado
    o alta de un elemento que está en duberr despiertan al trabajador; el resto se ignora (siempre 200)."""
    ev = str(datos.get('eventType') or '')
    if ev.lower() == 'test':
        return {'ok': True, 'evento': ev}
    obj = datos.get('series' if tipo == 'sonarr' else 'movie') or {}
    id_ = obj.get('id') if isinstance(obj, dict) else None
    k = f'{tipo}:{id_}'
    if ev.lower() in EVENTOS_DESPERTAR and id_ is not None and k in C.cfg()['items']:
        C.despertar.set()
        C.despertar_en(ESPERA_IMPORTADO)
        C.log('webhook', tipo, ev, obj.get('title', k), '-> revisión inmediata')
        return {'ok': True, 'evento': ev, 'despertado': True}
    return {'ok': True, 'evento': ev, 'ignorado': True}


class Servidor(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, dir_, handler):
        if ':' in dir_[0]:
            self.address_family = socket.AF_INET6
        super().__init__(dir_, handler)


def servir():
    C.token_webhook()                       # lo crea la primera vez para que salga en Ajustes
    if not PASSWORD and BIND not in ('127.0.0.1', 'localhost', '::1'):
        C.log('AVISO: la web no tiene contraseña (DUBERR_PASSWORD) y escucha en', BIND, '· no la expongas a internet (ver SECURITY.md)')
    Servidor((BIND, PUERTO), Web).serve_forever()
