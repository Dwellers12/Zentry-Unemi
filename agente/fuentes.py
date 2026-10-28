"""
Fuentes de imagen (puerto "FuenteImagen" de la Tarea 5).

Cada tipo de cámara registrado en Zentry tiene su adaptador:
  - imagen     → ImagenEstatica: una foto fija del proyecto (pruebas sin cámara).
  - ip_webcam  → IPWebcam: la app IP Webcam de Android (http://IP:8080/shot.jpg).
  - virtual    → CamaraVirtual: el puente del simulador (camaras_virtuales.py).

Todas devuelven un Fotograma o lanzan SinSenal.
"""
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlsplit

import cv2
import numpy as np
import requests

from .config import RAIZ

# Un cuadro del simulador más viejo que esto significa que la pestaña se cerró o se colgó.
CUADRO_VIEJO_S = 30


class SinSenal(Exception):
    """La cámara no entregó imagen (apagada, sin red, pestaña del simulador cerrada...)."""


@dataclass
class Fotograma:
    imagen: np.ndarray                 # BGR, como la usa OpenCV
    capturada_en: datetime             # UTC
    cuadro: str = ''                   # identificador del cuadro (para no procesarlo dos veces)
    jpeg: bytes | None = None          # bytes originales si la fuente ya entrega JPEG
    extra: dict = field(default_factory=dict)


def _decodificar(datos):
    imagen = cv2.imdecode(np.frombuffer(datos, np.uint8), cv2.IMREAD_COLOR)
    if imagen is None:
        raise SinSenal('La cámara envió una imagen que no se puede leer.')
    return imagen


def _ahora():
    return datetime.now(timezone.utc)


class Fuente:
    def __init__(self, camara):
        self.codigo = camara['codigo']
        self.url = camara.get('url') or ''

    def leer(self):
        raise NotImplementedError

    def cerrar(self):
        pass


class ImagenEstatica(Fuente):
    """Foto fija. La ruta es relativa a la raíz del proyecto (no se aceptan rutas fuera de ella)."""

    def __init__(self, camara):
        super().__init__(camara)
        ruta = (RAIZ / self.url).resolve()
        if not ruta.is_relative_to(RAIZ):
            raise ValueError(f'{self.codigo}: la imagen debe estar dentro del proyecto ({self.url}).')
        self.ruta = ruta
        self._imagen = None

    def leer(self):
        if self._imagen is None:
            if not self.ruta.is_file():
                # Sin llamar a cv2.imread: OpenCV escribiría su propio aviso en cada ciclo.
                raise SinSenal(f'No se encontró la imagen {self.ruta}.')
            imagen = cv2.imread(str(self.ruta))
            if imagen is None:
                raise SinSenal(f'No se encontró la imagen {self.ruta}.')
            self._imagen = imagen
        return Fotograma(self._imagen, _ahora(), cuadro=str(time.time()))


class _HTTP(Fuente):
    def __init__(self, camara, tiempo_espera=5):
        super().__init__(camara)
        self.sesion = requests.Session()
        self.tiempo_espera = tiempo_espera

    def _bajar(self, url):
        try:
            respuesta = self.sesion.get(url, timeout=self.tiempo_espera)
        except requests.RequestException as error:
            raise SinSenal(f'{self.codigo}: sin conexión con la cámara ({error.__class__.__name__}).') from error
        if respuesta.status_code != 200:
            try:
                detalle = respuesta.json().get('detail')  # el puente del simulador explica el motivo
            except ValueError:
                detalle = None
            raise SinSenal(detalle or f'{self.codigo}: la cámara respondió HTTP {respuesta.status_code}.')
        return respuesta

    def cerrar(self):
        self.sesion.close()


class IPWebcam(_HTTP):
    """App IP Webcam: http://IP:8080 (se aceptan también .../video o .../shot.jpg)."""

    def __init__(self, camara):
        super().__init__(camara)
        partes = urlsplit(self.url if '://' in self.url else f'http://{self.url}')
        self.foto = f'{partes.scheme}://{partes.netloc}/shot.jpg'

    def leer(self):
        respuesta = self._bajar(self.foto)
        return Fotograma(_decodificar(respuesta.content), _ahora(), cuadro=str(time.time()), jpeg=respuesta.content)


class CamaraVirtual(_HTTP):
    """Cámara del simulador servida por el puente: http://127.0.0.1:8765/camaras/CAM_01."""

    def __init__(self, camara):
        super().__init__(camara)
        self.base = self.url.rstrip('/')

    def leer(self):
        respuesta = self._bajar(f'{self.base}/shot.jpg')
        instante_ms = int(respuesta.headers.get('X-Zentry-Capturada', '0') or 0)
        capturada_en = datetime.fromtimestamp(instante_ms / 1000, timezone.utc) if instante_ms else _ahora()
        if (_ahora() - capturada_en).total_seconds() > CUADRO_VIEJO_S:
            raise SinSenal(f'{self.codigo}: el simulador dejó de enviar imágenes (¿se cerró la pestaña?).')
        return Fotograma(
            _decodificar(respuesta.content), capturada_en,
            cuadro=respuesta.headers.get('X-Zentry-Cuadro', ''), jpeg=respuesta.content,
            extra={'puente': self.base.rsplit('/camaras/', 1)[0]},
        )


TIPOS = {'imagen': ImagenEstatica, 'ip_webcam': IPWebcam, 'virtual': CamaraVirtual}


def crear_fuente(camara):
    clase = TIPOS.get(camara.get('tipo'))
    if clase is None:
        raise ValueError(f'{camara["codigo"]}: tipo de cámara desconocido ({camara.get("tipo")}).')
    return clase(camara)
