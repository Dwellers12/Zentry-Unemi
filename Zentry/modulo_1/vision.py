"""
Módulo de Visión por Computador de Zentry (Módulo 2).

Encapsula la carga de los modelos de IA (YOLOv8 + EasyOCR) y las funciones
que procesan un fotograma para:
  - Detectar vehículos y determinar la ocupación de las plazas.
  - Detectar y leer la placa de cada vehículo estacionado.

Los modelos se cargan de forma perezosa (lazy) una sola vez en memoria
para no penalizar el rendimiento en cada petición.
"""
import re
import time
from collections import defaultdict

import cv2
import easyocr
import numpy as np
import torch
from django.conf import settings
from ultralytics import YOLO

# Los modelos se encuentran en la raíz del proyecto (un nivel por encima de
# la carpeta del proyecto Django 'Zentry').
MODEL_PATH = settings.BASE_DIR.parent / 'best.pt'
MODEL_VEHICULOS_PATH = settings.BASE_DIR.parent / 'yolov8n.pt'

# Caracteres que puede tener una placa (el OCR no devolverá otros).
CARACTERES_PLACA = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-'

# Placa ecuatoriana: 3 letras + guion + 3-4 dígitos. Ej.: "PBD-5650", "ABC-123".
# Confusiones típicas del OCR según la posición (las 3 primeras son letras y
# el resto dígitos), p. ej. "2QJ-6606" -> "ZQJ-6606".
A_LETRA = {'0': 'O', '1': 'I', '2': 'Z', '4': 'A', '5': 'S', '6': 'G', '7': 'T', '8': 'B'}
A_DIGITO = {'O': '0', 'Q': '0', 'D': '0', 'U': '0', 'I': '1', 'L': '1', 'T': '7',
            'Z': '2', 'S': '5', 'B': '8', 'G': '6', 'A': '4'}

# Clases COCO que consideramos "vehículos" para determinar ocupación.
CLASES_VEHICULO = {2: 'car', 3: 'motorcycle', 5: 'bus', 7: 'truck'}

# Parte inferior del vehículo (donde toca el suelo) que se compara con las
# plazas, y fracción mínima de esa parte que debe caer dentro de una plaza.
FRACCION_HUELLA = 0.25
SOLAPAMIENTO_MIN = 0.3

# Los vehículos estacionados no se mueven: si un vehículo sigue en el mismo
# sitio, se reutiliza la placa leída en vez de repetir el OCR (lo más lento).
CACHE_SEGUNDOS = 30
CACHE_IOU_MIN = 0.85


def _usar_gpu():
    """Devuelve True si hay GPU disponible (CUDA/ROCm) y está habilitada."""
    habilitada = getattr(settings, 'USAR_GPU', True)
    return habilitada and torch.cuda.is_available()


def _device():
    """Device para YOLO: 0 (GPU) si está disponible, 'cpu' en caso contrario."""
    return 0 if _usar_gpu() else 'cpu'

# Referencias globales a los modelos (se inicializan una única vez).
_modelo_placa = None
_modelo_vehiculos = None
_lector = None

# fuente -> [{'bbox', 'placa', 'placa_bbox', 'placa_conf', 't'}, ...]
_cache_placas = defaultdict(list)


def cargar_modelos():
    """Carga (una sola vez) el modelo YOLO de placas y el lector EasyOCR."""
    global _modelo_placa, _lector

    if _modelo_placa is None:
        print("Cargando modelo YOLO personalizado (best.pt)...")
        _modelo_placa = YOLO(str(MODEL_PATH))

    if _lector is None:
        print("Iniciando lector óptico EasyOCR...")
        _lector = easyocr.Reader(['en'], gpu=_usar_gpu())

    return _modelo_placa, _lector


def cargar_modelo_vehiculos():
    """Carga (una sola vez) el modelo YOLO COCO para detectar vehículos."""
    global _modelo_vehiculos
    if _modelo_vehiculos is None:
        print("Cargando modelo YOLO COCO (yolov8n.pt)...")
        _modelo_vehiculos = YOLO(str(MODEL_VEHICULOS_PATH))
    return _modelo_vehiculos


def _normalizar_placa(texto):
    """
    Convierte un texto del OCR en una placa 'ABC-1234', o None si no tiene
    forma de placa. Descarta 'ECUADOR' y el ruido, y corrige confusiones
    letra/dígito según la posición.
    """
    texto = texto.upper()
    if '-' in texto:
        izq, der = texto.split('-', 1)
        izq = re.sub(r'[^A-Z0-9]', '', izq)[-3:]
        der = re.sub(r'[^A-Z0-9]', '', der)[:4]
    else:
        limpio = re.sub(r'[^A-Z0-9]', '', texto)
        if len(limpio) not in (6, 7):
            return None
        izq, der = limpio[:3], limpio[3:]

    if len(izq) != 3 or len(der) < 3:
        return None
    # Con demasiadas correcciones la lectura no es fiable.
    if sum(c.isalpha() for c in izq) < 2 or sum(c.isdigit() for c in der) < len(der) - 1:
        return None

    letras = ''.join(A_LETRA.get(c, c) for c in izq)
    digitos = ''.join(A_DIGITO.get(c, c) for c in der)
    if not (letras.isalpha() and digitos.isdigit()):
        return None
    return f'{letras}-{digitos}'


def _leer_texto_placa(recorte):
    """
    Lee el número de una placa ya recortada.

    Se prueban varias versiones de la imagen (gris, contraste CLAHE y
    binarizada) y gana la lectura con más confianza acumulada. Devuelve
    (placa, confianza) o (None, 0.0).
    """
    _, lector = cargar_modelos()

    # El OCR funciona mejor con placas de ~240 px de alto.
    escala = min(4.0, max(1.0, 240 / max(recorte.shape[0], 1)))
    grande = cv2.resize(recorte, None, fx=escala, fy=escala, interpolation=cv2.INTER_CUBIC)
    gris = cv2.cvtColor(grande, cv2.COLOR_BGR2GRAY)
    variantes = [
        gris,
        cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gris),
        cv2.threshold(cv2.GaussianBlur(gris, (5, 5), 0), 0, 255,
                      cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1],
    ]

    votos = defaultdict(float)
    for imagen in variantes:
        for _, texto, conf in lector.readtext(imagen, allowlist=CARACTERES_PLACA,
                                              detail=1, paragraph=False):
            placa = _normalizar_placa(texto)
            if placa:
                votos[placa] += conf

    if not votos:
        return None, 0.0
    placa = max(votos, key=votos.get)
    return placa, round(votos[placa] / len(variantes), 2)


def _iou(a, b):
    """Intersección sobre unión de dos cajas [x1, y1, x2, y2]."""
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def leer_placa_vehiculo(frame, vehiculo, fuente=''):
    """
    Busca y lee la placa dentro del recorte de un vehículo.

    En una imagen grande (4K) la placa ocupa muy pocos píxeles al reducirla
    para YOLO, así que se busca dentro de cada vehículo en vez de en toda la
    imagen. Devuelve {'placa', 'placa_bbox', 'placa_conf'}.
    """
    ahora = time.time()
    cache = _cache_placas[fuente]
    cache[:] = [c for c in cache if ahora - c['t'] < CACHE_SEGUNDOS]
    for c in cache:
        if _iou(c['bbox'], vehiculo['bbox']) >= CACHE_IOU_MIN:
            return {k: c[k] for k in ('placa', 'placa_bbox', 'placa_conf')}

    resultado = {'placa': None, 'placa_bbox': None, 'placa_conf': 0.0}
    modelo, _ = cargar_modelos()
    x1, y1, x2, y2 = vehiculo['bbox']
    recorte_vehiculo = frame[y1:y2, x1:x2]

    if recorte_vehiculo.size:
        cajas = modelo(recorte_vehiculo, device=_device(), verbose=False)[0].boxes
        if len(cajas):
            mejor = cajas[int(cajas.conf.argmax())]
            px1, py1, px2, py2 = map(int, mejor.xyxy[0])
            # Pequeño margen para no cortar los bordes de los caracteres.
            mx, my = int((px2 - px1) * 0.08), int((py2 - py1) * 0.08)
            px1, py1 = max(0, px1 - mx), max(0, py1 - my)
            px2 = min(recorte_vehiculo.shape[1], px2 + mx)
            py2 = min(recorte_vehiculo.shape[0], py2 + my)

            placa, conf = _leer_texto_placa(recorte_vehiculo[py1:py2, px1:px2])
            resultado = {
                'placa': placa,
                'placa_bbox': [x1 + px1, y1 + py1, x1 + px2, y1 + py2],
                'placa_conf': conf,
            }

    cache.append({'bbox': vehiculo['bbox'], 't': ahora, **resultado})
    return resultado


def detectar_vehiculos(frame):
    """
    Detecta vehículos (car, truck, bus, motorcycle) en el fotograma.

    Devuelve una lista de diccionarios:
        [{'clase': 'car', 'bbox': [x1, y1, x2, y2], 'conf': 0.87}, ...]
    """
    modelo = cargar_modelo_vehiculos()

    resultados = modelo(frame, device=_device(), verbose=False)
    vehiculos = []

    for r in resultados:
        for caja in r.boxes:
            clase = int(caja.cls[0])
            if clase in CLASES_VEHICULO:
                x1, y1, x2, y2 = map(int, caja.xyxy[0])
                conf = float(caja.conf[0])
                vehiculos.append({
                    'clase': CLASES_VEHICULO[clase],
                    'bbox': [x1, y1, x2, y2],
                    'conf': round(conf, 2),
                })

    return vehiculos


def _solapamiento(bbox, poligono):
    """
    Fracción de la "huella" del vehículo (su franja inferior, la que toca el
    suelo) que cae dentro del polígono de una plaza.
    """
    x1, y1, x2, y2 = bbox
    y_huella = y2 - (y2 - y1) * FRACCION_HUELLA
    huella = np.array([[x1, y_huella], [x2, y_huella], [x2, y2], [x1, y2]], dtype=np.float32)
    plaza = cv2.convexHull(np.array(poligono, dtype=np.float32))
    area, _ = cv2.intersectConvexConvex(huella, plaza)
    return area / max(cv2.contourArea(huella), 1.0)


def verificar_ocupacion(vehiculos, plazas):
    """
    Determina el estado de ocupación de cada plaza.

    Cada vehículo se asigna a la plaza que contiene la mayor parte de su
    huella (si supera SOLAPAMIENTO_MIN). Si dos vehículos caen en la misma
    plaza, se queda el que más solapa.

    Devuelve una lista de diccionarios:
        [{'id': 'P1', 'ocupada': True, 'vehiculo': {...}}, ...]
    """
    asignados = {}  # id_plaza -> (solapamiento, vehículo)
    for v in vehiculos:
        mejor_id, mejor = None, SOLAPAMIENTO_MIN
        for plaza in plazas:
            s = _solapamiento(v['bbox'], plaza['poligono'])
            if s >= mejor:
                mejor_id, mejor = plaza['id'], s
        if mejor_id and mejor > asignados.get(mejor_id, (0, None))[0]:
            asignados[mejor_id] = (mejor, v)

    return [
        {
            'id': plaza['id'],
            'ocupada': plaza['id'] in asignados,
            'vehiculo': asignados.get(plaza['id'], (0, None))[1],
        }
        for plaza in plazas
    ]
