"""duberr · comprobación de salud para Docker (HEALTHCHECK): pide /salud a la web local. Sale con 0 si responde 200."""
import os, sys, urllib.request

puerto = os.environ.get('DUBERR_PORT') or os.environ.get('PUERTO') or '3005'
bind = os.environ.get('DUBERR_BIND') or '0.0.0.0'
host = '127.0.0.1' if bind in ('0.0.0.0', '::', '') else bind
try:
    with urllib.request.urlopen(f'http://{host}:{puerto}/salud', timeout=4) as r:
        sys.exit(0 if r.status == 200 else 1)
except Exception:
    sys.exit(1)
