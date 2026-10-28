"""
Reconocimiento: de un fotograma a la lista de detecciones que se envía a Zentry.

    [{'plaza': 'P1', 'ocupada': True, 'placa': 'ABC-1234', 'confianza': 91,
      'vehiculo_caja': [x1, y1, x2, y2], 'placa_caja': [x1, y1, x2, y2]}, ...]

Las cajas van en coordenadas relativas (0 a 1) para que no dependan de la
resolución de la cámara. Dos modos:
  - vision     YOLO + lector de placas sobre la imagen (lo que se usará con cámaras reales).
  - simulador  para cámaras virtuales usa la verdad de terreno del simulador
               (reconocedor perfecto, sin cargar la IA; para probar el flujo completo).
"""
import logging

import requests

from . import simulador
from .vision import Vision, verificar_ocupacion

log = logging.getLogger('zentry.agente')

MODOS = ('vision', 'simulador')


def _relativa(caja, ancho, alto):
    if not caja:
        return None
    x1, y1, x2, y2 = caja
    limitar = lambda v: round(min(1.0, max(0.0, v)), 5)  # noqa: E731
    return [limitar(x1 / ancho), limitar(y1 / alto), limitar(x2 / ancho), limitar(y2 / alto)]


def plazas_en_pixeles(camara, ancho, alto):
    return [
        {'id': p['nombre'], 'poligono': [[x * ancho, y * alto] for x, y in p['poligono']]}
        for p in camara.get('plazas') or [] if p.get('poligono')
    ]


class Reconocimiento:
    def __init__(self, config, modo='vision'):
        if modo not in MODOS:
            raise ValueError(f'Modo de reconocimiento desconocido: {modo}')
        self.modo = modo
        self.config = config
        self._vision = None
        self._sesion = requests.Session()

    @property
    def vision(self):
        if self._vision is None:
            self._vision = Vision(self.config)
        return self._vision

    def precargar(self):
        """Carga los modelos antes de la primera captura (la primera carga tarda varios segundos)."""
        if self.modo == 'vision':
            self.vision.modelo_vehiculos()
            self.vision.modelo_placa()
            if self.vision.lector_propio() is None or self.config.ocr != 'propio':
                self.vision.lector()   # EasyOCR (si hace falta para plazas se carga después)

    # ------------------------------------------------------------------ detecciones
    def detecciones(self, camara, fotograma):
        if self.modo == 'simulador' and camara.get('tipo') == 'virtual':
            return self._desde_simulador(camara, fotograma)
        return self._desde_imagen(camara, fotograma)

    def _desde_imagen(self, camara, fotograma):
        imagen = fotograma.imagen
        alto, ancho = imagen.shape[:2]
        plazas = plazas_en_pixeles(camara, ancho, alto)
        if not plazas:
            return []
        vehiculos = self.vision.detectar_vehiculos(imagen)
        salida = []
        for estado in verificar_ocupacion(vehiculos, plazas):
            if not estado['ocupada']:
                salida.append({'plaza': estado['id'], 'ocupada': False})
                continue
            vehiculo = estado['vehiculo']
            placa = self.vision.leer_placa_vehiculo(imagen, vehiculo, camara['codigo'])
            salida.append({
                'plaza': estado['id'], 'ocupada': True,
                'placa': placa['placa'] or '',
                'confianza': int(round(placa['placa_conf'] * 100)),
                'vehiculo_caja': _relativa(vehiculo['bbox'], ancho, alto),
                'placa_caja': _relativa(placa['placa_bbox'], ancho, alto),
            })
        return salida

    def _desde_simulador(self, camara, fotograma):
        puente = fotograma.extra.get('puente')
        try:
            respuesta = self._sesion.get(f'{puente}/api/estado', params={'cuadro': fotograma.cuadro}, timeout=5)
            respuesta.raise_for_status()
            estado = respuesta.json()
        except (requests.RequestException, ValueError) as error:
            log.warning('%s: no se pudo leer el estado del simulador (%s); se usa la visión.', camara['codigo'], error)
            return self._desde_imagen(camara, fotograma)
        nombres = {p['nombre'] for p in camara.get('plazas') or []}
        return [d for d in simulador.detecciones(estado, camara['codigo']) if d['plaza'] in nombres]

    # ------------------------------------------------------------------ plazas
    def sugerir_plazas(self, fotograma, primer_numero=1):
        """Plazas detectadas por las líneas amarillas del suelo (polígonos relativos)."""
        from .deteccion_plazas import detectar_plazas
        imagen = fotograma.imagen
        alto, ancho = imagen.shape[:2]
        vehiculos = self.vision.detectar_vehiculos(imagen)
        plazas = detectar_plazas(imagen, vehiculos, lector=self.vision.lector(), primer_numero=primer_numero)
        return [
            {'nombre': p['id'], 'poligono': [[round(x / ancho, 5), round(y / alto, 5)] for x, y in p['poligono']]}
            for p in plazas
        ]
