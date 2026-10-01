import base64
import json
from pathlib import Path

import cv2
import numpy as np
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

from . import deteccion_plazas
from . import plazas as plazas_mod
from . import vision

# Ancho máximo de la imagen anotada que se devuelve al navegador.
ANCHO_MAX_RESULTADO = 1600


def _modo_ejemplo():
    return getattr(settings, 'MODO_EJEMPLO', False)


def _fuente(nombre_imagen=None):
    """
    Identificador de la cámara con la que se trabaja: en modo ejemplo es el
    nombre de la imagen; en modo normal, la cámara en vivo.
    """
    if _modo_ejemplo():
        # Solo el nombre del archivo: evita rutas fuera de static/.
        return Path(nombre_imagen or getattr(settings, 'IMAGEN_EJEMPLO', '')).name
    return 'camara'


def _obtener_frame(nombre_imagen=None):
    """
    Obtiene un fotograma. En modo ejemplo carga una imagen estática (la indicada
    o la configurada por defecto); en modo normal captura desde la cámara.
    """
    if _modo_ejemplo():
        ruta = settings.BASE_DIR / 'static' / _fuente(nombre_imagen)
        frame = cv2.imread(str(ruta))
        return frame is not None, frame

    camara_url = getattr(settings, 'CAMARA_URL', 'http://192.168.1.8:8080/video')
    cap = cv2.VideoCapture(camara_url)
    ret, frame = cap.read()
    cap.release()
    return ret, frame


def _leer_json(request):
    try:
        return json.loads(request.body) if request.body else {}
    except (ValueError, TypeError):
        return {}


def _detectar_plazas(frame, vehiculos, fuente):
    """Detecta automáticamente las plazas de la imagen (sin guardarlas)."""
    _, lector = vision.cargar_modelos()
    return deteccion_plazas.detectar_plazas(
        frame, vehiculos, lector=lector,
        primer_numero=plazas_mod.siguiente_numero(fuente),
    )


def _anotar(frame, ocupacion, plazas):
    """Dibuja plazas, vehículos y placas sobre la imagen (evidencia)."""
    escala = frame.shape[1] / 1280  # grosor/tamaño de texto legibles en 4K
    grosor = max(2, int(2 * escala))
    fuente_tam = 0.6 * escala
    poligonos = {p['id']: p['poligono'] for p in plazas}

    for o in ocupacion:
        pts = np.array(poligonos[o['id']], dtype=np.int32).reshape(-1, 1, 2)
        color = (0, 0, 255) if o['ocupada'] else (0, 200, 0)
        cv2.polylines(frame, [pts], True, color, grosor)
        v = o['vehiculo']
        if v:
            x1, y1, x2, y2 = v['bbox']
            cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 128, 0), grosor)
            if v.get('placa_bbox'):
                px1, py1, px2, py2 = v['placa_bbox']
                cv2.rectangle(frame, (px1, py1), (px2, py2), (0, 255, 255), grosor)

        etiqueta = o['id'] + (f" {v['placa']}" if v and v.get('placa') else '')
        (ancho_texto, _), _ = cv2.getTextSize(etiqueta, cv2.FONT_HERSHEY_SIMPLEX, fuente_tam, grosor)
        xs = [p[0] for p in poligonos[o['id']]]
        x = int((min(xs) + max(xs)) / 2 - ancho_texto / 2)
        y = int(max(p[1] for p in poligonos[o['id']])) + int(30 * escala)
        cv2.putText(frame, etiqueta, (x, y), cv2.FONT_HERSHEY_SIMPLEX,
                    fuente_tam, (0, 0, 0), grosor + 3)
        cv2.putText(frame, etiqueta, (x, y), cv2.FONT_HERSHEY_SIMPLEX,
                    fuente_tam, color, grosor)

    if frame.shape[1] > ANCHO_MAX_RESULTADO:
        f = ANCHO_MAX_RESULTADO / frame.shape[1]
        frame = cv2.resize(frame, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    return frame


def dashboard(request):
    """Renderiza la página principal con la cámara o la imagen de ejemplo."""
    if _modo_ejemplo():
        camara_url = '/static/' + getattr(settings, 'IMAGEN_EJEMPLO', 'estacionamiento_ejemplo.jpg')
    else:
        camara_url = getattr(settings, 'CAMARA_URL', 'http://192.168.1.8:8080/video')

    context = {'camara_url': camara_url, 'modo_ejemplo': _modo_ejemplo()}
    return render(request, 'dashboard.html', context)


@csrf_exempt
def verificar_estado(request):
    """
    Endpoint que obtiene un fotograma, detecta vehículos y sus placas, y
    devuelve el estado de ocupación de cada plaza junto con la imagen anotada.

    Si la cámara aún no tiene plazas configuradas, se detectan automáticamente
    y se guardan.

    (La validación contra el registro de residentes/plazas se conectará
     posteriormente a AWS RDS.)
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Método no permitido. Usa POST.'}, status=405)

    nombre_imagen = _leer_json(request).get('imagen')
    fuente = _fuente(nombre_imagen)
    ret, frame = _obtener_frame(nombre_imagen)

    if not ret:
        return JsonResponse(
            {'error': 'No se pudo obtener la imagen (cámara o imagen de ejemplo).'},
            status=502,
        )

    vehiculos = vision.detectar_vehiculos(frame)

    plazas = plazas_mod.cargar_plazas(fuente)
    plazas_detectadas = False
    if not plazas:
        plazas = _detectar_plazas(frame, vehiculos, fuente)
        if plazas:
            plazas_mod.guardar_plazas(fuente, plazas)
            plazas_detectadas = True

    ocupacion = vision.verificar_ocupacion(vehiculos, plazas)
    for o in ocupacion:
        if o['vehiculo']:
            o['vehiculo'].update(vision.leer_placa_vehiculo(frame, o['vehiculo'], fuente))

    imagen = _anotar(frame, ocupacion, plazas)
    _, buffer = cv2.imencode('.jpg', imagen)

    return JsonResponse({
        'plazas': [
            {
                'id': o['id'],
                'ocupada': o['ocupada'],
                'placa': o['vehiculo']['placa'] if o['vehiculo'] else None,
                'placa_conf': o['vehiculo']['placa_conf'] if o['vehiculo'] else None,
                'vehiculo': o['vehiculo'],
            }
            for o in ocupacion
        ],
        'plazas_detectadas': plazas_detectadas,
        'imagen': base64.b64encode(buffer).decode('utf-8'),
    })


@csrf_exempt
def gestionar_plazas(request):
    """
    GET ?imagen=...: devuelve las plazas de esa cámara/imagen.
    POST {imagen, plazas}: guarda las plazas de esa cámara/imagen.
    """
    if request.method == 'GET':
        fuente = _fuente(request.GET.get('imagen'))
        return JsonResponse({'plazas': plazas_mod.cargar_plazas(fuente)})

    if request.method == 'POST':
        data = _leer_json(request)
        plazas = data.get('plazas', [])
        error = plazas_mod.validar_plazas(plazas)
        if error:
            return JsonResponse({'error': error}, status=400)
        plazas_mod.guardar_plazas(_fuente(data.get('imagen')), plazas)
        return JsonResponse({'ok': True, 'plazas': plazas})

    return JsonResponse({'error': 'Método no permitido'}, status=405)


@csrf_exempt
def detectar_plazas(request):
    """
    POST {imagen}: detecta automáticamente las plazas de la imagen y las
    devuelve SIN guardarlas (el modo edición decide si guardarlas).
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Método no permitido. Usa POST.'}, status=405)

    nombre_imagen = _leer_json(request).get('imagen')
    ret, frame = _obtener_frame(nombre_imagen)
    if not ret:
        return JsonResponse(
            {'error': 'No se pudo obtener la imagen (cámara o imagen de ejemplo).'},
            status=502,
        )

    plazas = _detectar_plazas(frame, vision.detectar_vehiculos(frame), _fuente(nombre_imagen))
    if not plazas:
        return JsonResponse(
            {'error': 'No se encontraron líneas de parqueo. Dibuja las plazas a mano.'},
            status=422,
        )
    return JsonResponse({'plazas': plazas})


def listar_imagenes(request):
    """Devuelve la lista de imágenes disponibles en la carpeta static/."""
    carpeta = settings.BASE_DIR / 'static'
    extensiones = ('.jpg', '.jpeg', '.png')
    imagenes = sorted(
        f.name for f in carpeta.iterdir()
        if f.is_file() and f.name.lower().endswith(extensiones)
    )
    return JsonResponse({'imagenes': imagenes})
