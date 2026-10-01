"""
Gestión de la configuración de plazas de estacionamiento.

Cada plaza se define como un polígono (lista de puntos [x, y]) sobre la
imagen de una cámara. Cada cámara (o imagen de ejemplo) tiene sus propias
plazas. La configuración se guarda en un archivo JSON para poder calibrarla
sin depender de la base de datos (que se conectará posteriormente a AWS RDS):

    {"camaras": {"cam_01.png": [{"id": "P1", "poligono": [[x, y], ...]}, ...]}}
"""
import json
import re

from django.conf import settings

PLAZAS_PATH = settings.BASE_DIR.parent / 'plazas.json'


def _leer():
    if PLAZAS_PATH.exists():
        with open(PLAZAS_PATH, encoding='utf-8') as f:
            return json.load(f)
    return {}


def cargar_plazas(fuente):
    """Carga las plazas de una cámara/imagen (o [] si no hay configuración)."""
    return _leer().get('camaras', {}).get(fuente, [])


def guardar_plazas(fuente, plazas):
    """Guarda las plazas de una cámara/imagen en plazas.json."""
    data = _leer()
    # Formato antiguo: una sola lista global de plazas, sin cámara.
    data.pop('plazas', None)
    data.setdefault('camaras', {})[fuente] = plazas
    with open(PLAZAS_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def siguiente_numero(excepto_fuente):
    """Primer número de plaza libre si se numera tras las demás cámaras."""
    numeros = [
        int(m.group(1))
        for fuente, plazas in _leer().get('camaras', {}).items()
        if fuente != excepto_fuente
        for p in plazas
        if (m := re.fullmatch(r'P(\d+)', str(p.get('id', ''))))
    ]
    return max(numeros, default=0) + 1


def validar_plazas(plazas):
    """Devuelve un mensaje de error si la lista de plazas no es válida, o None."""
    if not isinstance(plazas, list):
        return 'El campo "plazas" debe ser una lista.'
    ids = set()
    for p in plazas:
        if not isinstance(p, dict) or not str(p.get('id', '')).strip():
            return 'Cada plaza necesita un "id".'
        if p['id'] in ids:
            return f'El id "{p["id"]}" está repetido.'
        ids.add(p['id'])
        poligono = p.get('poligono')
        if (not isinstance(poligono, list) or len(poligono) < 3
                or not all(isinstance(pt, list) and len(pt) == 2
                           and all(isinstance(c, (int, float)) for c in pt)
                           for pt in poligono)):
            return f'La plaza "{p["id"]}" necesita un polígono de al menos 3 puntos [x, y].'
    return None
