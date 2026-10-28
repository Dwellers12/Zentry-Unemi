"""
Visión por computador del agente (antes Zentry/modulo_1/vision.py, ahora sin Django).

  - detectar_vehiculos: YOLOv8 COCO (yolov8n.pt) → autos, motos, buses y camiones.
  - verificar_ocupacion: cada vehículo se asigna a la plaza donde cae su "huella".
  - leer_placa_vehiculo: YOLO propio (best.pt) busca la placa dentro del vehículo y
    la lee el lector de placas propio (placas_ocr.pt, ver lector_placas.py). En modo
    'auto', si no está seguro, EasyOCR la vuelve a leer y se combinan las dos lecturas.
    EasyOCR también lee los números pintados de las plazas (deteccion_plazas.py).

Los modelos se cargan una sola vez y de forma perezosa (la primera vez que se usan).
La normalización de la placa definitiva la hace el servidor (verificacion.servicios);
aquí solo se filtra lo que no tiene forma de placa.
"""
import logging
import re
import time
from collections import defaultdict

import cv2
import numpy as np

log = logging.getLogger('zentry.agente')

# Caracteres que puede tener una placa (el OCR no devolverá otros).
CARACTERES_PLACA = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-'

# Placa ecuatoriana: 3 letras + guion + 3-4 dígitos. Confusiones típicas del OCR
# según la posición (las 3 primeras son letras y el resto dígitos).
A_LETRA = {'0': 'O', '1': 'I', '2': 'Z', '4': 'A', '5': 'S', '6': 'G', '7': 'T', '8': 'B'}
A_DIGITO = {'O': '0', 'Q': '0', 'D': '0', 'U': '0', 'I': '1', 'L': '1', 'T': '7',
            'Z': '2', 'S': '5', 'B': '8', 'G': '6', 'A': '4'}

# Clases COCO que cuentan como vehículo.
CLASES_VEHICULO = {2: 'car', 3: 'motorcycle', 5: 'bus', 7: 'truck'}
CONFIANZA_VEHICULO = 0.35

# Franja inferior del vehículo (la que toca el suelo) y fracción mínima que debe caer en la plaza.
FRACCION_HUELLA = 0.25
SOLAPAMIENTO_MIN = 0.3

# Un vehículo estacionado no se mueve: si sigue en el mismo sitio se reutiliza la
# placa ya leída en vez de repetir el OCR (lo más lento).
CACHE_SEGUNDOS = 30
CACHE_FALLO_S = 9      # una placa que no se pudo leer se reintenta antes
CACHE_IOU_MIN = 0.85

# Confianza del OCR (0-1) a partir de la cual no hace falta probar más versiones de la imagen
# (ni pedirle a EasyOCR una segunda opinión).
LECTURA_SEGURA = 0.85

# Lector propio: se lee la placa con tres márgenes distintos alrededor de la caja de YOLO y
# se vota (la caja nunca es exacta). EasyOCR usa el margen de siempre.
MARGENES_PROPIO = (0.08, 0.02, 0.16)
MARGEN_EASYOCR = 0.08
# Si los dos lectores no coinciden, la lectura elegida pierde confianza.
CASTIGO_DESACUERDO = 0.75
MODOS_OCR = ('propio', 'auto', 'easyocr')


class Vision:
    def __init__(self, config):
        self.config = config
        self._modelo_placa = None
        self._modelo_vehiculos = None
        self._lector = None
        self._propio = None              # LectorPlacas, o False si no hay placas_ocr.pt
        self._cache = defaultdict(list)  # cámara -> [{'bbox', 'placa', 'placa_bbox', 'placa_conf', 'vence'}]

    # ------------------------------------------------------------------ modelos
    def _gpu(self):
        if not self.config.usar_gpu:
            return False
        import torch
        return torch.cuda.is_available()

    def _dispositivo(self):
        return 0 if self._gpu() else 'cpu'

    def modelo_vehiculos(self):
        if self._modelo_vehiculos is None:
            from ultralytics import YOLO
            log.info('Cargando YOLO de vehículos (%s)...', self.config.modelo_vehiculos.name)
            self._modelo_vehiculos = YOLO(str(self.config.modelo_vehiculos))
        return self._modelo_vehiculos

    def modelo_placa(self):
        if self._modelo_placa is None:
            from ultralytics import YOLO
            log.info('Cargando YOLO de placas (%s)...', self.config.modelo_placas.name)
            self._modelo_placa = YOLO(str(self.config.modelo_placas))
        return self._modelo_placa

    def lector(self):
        if self._lector is None:
            import easyocr
            log.info('Iniciando EasyOCR (%s)...', 'GPU' if self._gpu() else 'CPU')
            self._lector = easyocr.Reader(['en'], gpu=self._gpu(), verbose=False)
        return self._lector

    def lector_propio(self):
        """Lector de placas propio, o None si no se usa (modo easyocr o falta placas_ocr.pt)."""
        if self._propio is None:
            ruta = self.config.modelo_ocr
            if self.config.ocr == 'easyocr':
                self._propio = False
            elif not ruta.exists():
                log.warning('No se encontró %s: las placas se leen solo con EasyOCR.', ruta)
                self._propio = False
            else:
                from .lector_placas import LectorPlacas
                log.info('Cargando lector de placas (%s)...', ruta.name)
                self._propio = LectorPlacas(ruta, 'cuda' if self._gpu() else 'cpu')
        return self._propio or None

    # ------------------------------------------------------------------ vehículos
    def detectar_vehiculos(self, imagen):
        """[{'clase': 'car', 'bbox': [x1, y1, x2, y2], 'conf': 0.87}, ...] en píxeles."""
        resultados = self.modelo_vehiculos()(imagen, device=self._dispositivo(), verbose=False,
                                             conf=CONFIANZA_VEHICULO)
        vehiculos = []
        for r in resultados:
            for caja in r.boxes:
                clase = int(caja.cls[0])
                if clase in CLASES_VEHICULO:
                    x1, y1, x2, y2 = map(int, caja.xyxy[0])
                    vehiculos.append({'clase': CLASES_VEHICULO[clase], 'bbox': [x1, y1, x2, y2],
                                      'conf': round(float(caja.conf[0]), 2)})
        return vehiculos

    # ------------------------------------------------------------------ placas
    def leer_placa_vehiculo(self, imagen, vehiculo, camara=''):
        """
        Busca y lee la placa dentro del recorte de un vehículo (en una imagen 4K la placa
        ocupa muy pocos píxeles si se busca en toda la imagen).
        Devuelve {'placa', 'placa_bbox', 'placa_conf'} (placa None si no se pudo leer).
        """
        ahora = time.time()
        cache = self._cache[camara]
        cache[:] = [c for c in cache if ahora < c['vence']]
        for c in cache:
            if _iou(c['bbox'], vehiculo['bbox']) >= CACHE_IOU_MIN:
                return {k: c[k] for k in ('placa', 'placa_bbox', 'placa_conf')}

        resultado = {'placa': None, 'placa_bbox': None, 'placa_conf': 0.0}
        caja = self.buscar_placa(imagen, vehiculo)
        if caja:
            placa, conf = self.leer_caja(imagen, caja)
            resultado = {'placa': placa, 'placa_bbox': caja_con_margen(imagen, caja, MARGEN_EASYOCR),
                         'placa_conf': conf}
        vence = ahora + (CACHE_SEGUNDOS if resultado['placa'] else CACHE_FALLO_S)
        cache.append({'bbox': vehiculo['bbox'], 'vence': vence, **resultado})
        return resultado

    def buscar_placa(self, imagen, vehiculo):
        """Caja [x1, y1, x2, y2] de la placa (sin margen, en píxeles de la imagen) o None."""
        x1, y1, x2, y2 = vehiculo['bbox']
        x1, y1 = max(0, x1), max(0, y1)
        recorte = imagen[y1:y2, x1:x2]
        if not recorte.size:
            return None
        cajas = self.modelo_placa()(recorte, device=self._dispositivo(), verbose=False)[0].boxes
        if not len(cajas):
            return None
        px1, py1, px2, py2 = map(float, cajas[int(cajas.conf.argmax())].xyxy[0])
        return [x1 + px1, y1 + py1, x1 + px2, y1 + py2]

    def leer_caja(self, imagen, caja, modo=None):
        """
        Lee la placa que está en `caja`. Devuelve (placa, confianza 0-1).
          propio:  solo el lector propio (por defecto; ~3-20 ms por placa en CPU).
          auto:    el lector propio; si no está seguro, también EasyOCR, y se combinan.
          easyocr: solo EasyOCR (lo que se usaba antes; segundos por placa en CPU).
        """
        modo = modo or self.config.ocr
        propio = self.lector_propio() if modo in ('auto', 'propio') else None
        placa, conf = self._leer_propio(propio, imagen, caja) if propio else (None, 0.0)
        if propio and (modo == 'propio' or conf >= LECTURA_SEGURA):
            return placa, conf
        segunda = self._leer_texto(recortar(imagen, caja_con_margen(imagen, caja, MARGEN_EASYOCR)))
        return combinar((placa, conf), segunda) if propio else segunda

    def _leer_propio(self, lector, imagen, caja):
        """Lee con varios márgenes y vota; la confianza baja si las lecturas no coinciden."""
        recortes = [recortar(imagen, caja_con_margen(imagen, caja, m)) for m in MARGENES_PROPIO]
        votos = defaultdict(float)
        for texto, conf in lector.leer([r for r in recortes if r.size]):
            placa = normalizar_placa(texto)
            if placa:
                votos[placa] += conf
        if not votos:
            return None, 0.0
        placa = max(votos, key=votos.get)
        return placa, round(min(1.0, votos[placa] / len(recortes)), 2)

    def _leer_texto(self, recorte):
        """Lee una placa ya recortada probando gris, CLAHE y binarizada. Devuelve (placa, confianza 0-1)."""
        if not recorte.size:
            return None, 0.0
        # El OCR funciona mejor con placas de ~240 px de alto.
        escala = min(4.0, max(1.0, 240 / max(recorte.shape[0], 1)))
        grande = cv2.resize(recorte, None, fx=escala, fy=escala, interpolation=cv2.INTER_CUBIC)
        gris = cv2.cvtColor(grande, cv2.COLOR_BGR2GRAY)
        variantes = [
            gris,
            cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gris),
            cv2.threshold(cv2.GaussianBlur(gris, (5, 5), 0), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1],
        ]
        votos = defaultdict(float)
        usadas = 0
        for variante in variantes:
            usadas += 1
            for _, texto, conf in self.lector().readtext(variante, allowlist=CARACTERES_PLACA,
                                                         detail=1, paragraph=False):
                placa = normalizar_placa(texto)
                if placa:
                    votos[placa] += conf
            # Si la lectura ya es clara no se prueban las demás versiones (el OCR es lo más lento en CPU).
            if votos and max(votos.values()) / usadas >= LECTURA_SEGURA:
                break
        if not votos:
            return None, 0.0
        placa = max(votos, key=votos.get)
        return placa, round(min(1.0, votos[placa] / usadas), 2)


# ---------------------------------------------------------------------- funciones puras

def caja_con_margen(imagen, caja, margen):
    """Agranda la caja un `margen` (fracción de su ancho/alto) por lado, sin salirse de la imagen. Enteros."""
    x1, y1, x2, y2 = caja
    mx, my = (x2 - x1) * margen, (y2 - y1) * margen
    alto, ancho = imagen.shape[:2]
    return [max(0, int(x1 - mx)), max(0, int(y1 - my)), min(ancho, int(round(x2 + mx))), min(alto, int(round(y2 + my)))]


def recortar(imagen, caja):
    x1, y1, x2, y2 = caja
    return imagen[y1:y2, x1:x2]


def combinar(primera, segunda):
    """
    Junta la lectura del lector propio con la de EasyOCR (cada una: (placa, confianza)).
    Si coinciden, la confianza sube (dos lectores distintos dijeron lo mismo). Si no
    coinciden, o si solo EasyOCR leyó algo, gana la más segura pero con menos confianza:
    en las pruebas EasyOCR solo se equivocaba con 70-75 % de confianza y el lector propio no.
    """
    (p1, c1), (p2, c2) = primera, segunda
    if p1 and p1 == p2:
        return p1, round(1 - (1 - c1) * (1 - c2), 2)
    if not p2:
        return p1, c1
    if not p1:
        return p2, round(c2 * CASTIGO_DESACUERDO, 2)
    placa, conf = (p1, c1) if c1 >= c2 else (p2, c2)
    return placa, round(conf * CASTIGO_DESACUERDO, 2)


def normalizar_placa(texto):
    """
    Convierte un texto del OCR en 'ABC-1234', o None si no tiene forma de placa
    (descarta 'ECUADOR' y el ruido, y corrige confusiones letra/dígito según la posición).
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


def _iou(a, b):
    """Intersección sobre unión de dos cajas [x1, y1, x2, y2]."""
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _solapamiento(bbox, poligono):
    """Fracción de la huella del vehículo (su franja inferior) que cae dentro del polígono de una plaza."""
    x1, y1, x2, y2 = bbox
    y_huella = y2 - (y2 - y1) * FRACCION_HUELLA
    huella = np.array([[x1, y_huella], [x2, y_huella], [x2, y2], [x1, y2]], dtype=np.float32)
    plaza = cv2.convexHull(np.array(poligono, dtype=np.float32))
    area, _ = cv2.intersectConvexConvex(huella, plaza)
    return area / max(cv2.contourArea(huella), 1.0)


def verificar_ocupacion(vehiculos, plazas):
    """
    `plazas`: [{'id': 'P1', 'poligono': [[x, y], ...] en píxeles}].
    Cada vehículo va a la plaza con más solapamiento (≥ SOLAPAMIENTO_MIN); si dos caen en
    la misma plaza se queda el que más solapa.
    Devuelve [{'id': 'P1', 'ocupada': bool, 'vehiculo': {...} | None}, ...].
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
        {'id': p['id'], 'ocupada': p['id'] in asignados, 'vehiculo': asignados.get(p['id'], (0, None))[1]}
        for p in plazas
    ]
