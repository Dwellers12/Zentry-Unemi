"""Configuración del agente: variables de entorno con valores por defecto para desarrollo."""
import os
from dataclasses import dataclass, field
from pathlib import Path

# Raíz del repositorio (aquí está .agente_token) y carpeta de los modelos .pt.
RAIZ = Path(__file__).resolve().parent.parent
MODELOS = RAIZ / 'modelos'


def _bool(nombre, defecto=False):
    valor = os.environ.get(nombre)
    if valor is None:
        return defecto
    return valor.strip().lower() in ('1', 'true', 'si', 'sí', 'yes', 'on')


@dataclass
class Config:
    # Servidor Zentry (Django). En producción: la URL pública con HTTPS.
    servidor: str = field(default_factory=lambda: os.environ.get('ZENTRY_URL', 'http://127.0.0.1:8000').rstrip('/'))
    # Token del usuario "agente". Si no se da, se lee de .agente_token (lo crea `manage.py crear_agente`).
    token: str = field(default_factory=lambda: os.environ.get('ZENTRY_TOKEN', ''))
    archivo_token: Path = field(default_factory=lambda: Path(os.environ.get('ZENTRY_TOKEN_ARCHIVO', RAIZ / '.agente_token')))
    # Cola local para cuando no hay conexión con el servidor (RNF-05).
    carpeta_cola: Path = field(default_factory=lambda: Path(os.environ.get('ZENTRY_COLA', RAIZ / '.agente_cola')))
    # Modelos de IA y uso de GPU (por defecto CPU; ZENTRY_USAR_GPU=1 para CUDA/ROCm).
    modelo_placas: Path = field(default_factory=lambda: Path(os.environ.get('ZENTRY_MODELO_PLACAS', MODELOS / 'best.pt')))
    modelo_vehiculos: Path = field(default_factory=lambda: Path(os.environ.get('ZENTRY_MODELO_VEHICULOS', MODELOS / 'yolov8n.pt')))
    usar_gpu: bool = field(default_factory=lambda: _bool('ZENTRY_USAR_GPU'))
    # Lector de placas: 'propio' (placas_ocr.pt), 'auto' (propio + EasyOCR si duda) o 'easyocr'.
    ocr: str = field(default_factory=lambda: os.environ.get('ZENTRY_OCR', 'propio').strip().lower())
    modelo_ocr: Path = field(default_factory=lambda: Path(os.environ.get('ZENTRY_MODELO_OCR', MODELOS / 'placas_ocr.pt')))
    # Ancho máximo de la imagen que se envía como evidencia (la original puede ser 4K).
    ancho_envio: int = field(default_factory=lambda: int(os.environ.get('ZENTRY_ANCHO_ENVIO', 1920)))
    calidad_jpeg: int = 85
    # Tiempo de espera de cada petición HTTP (segundos).
    tiempo_espera: float = 10.0

    def leer_token(self):
        if self.token:
            return self.token.strip()
        try:
            return self.archivo_token.read_text(encoding='utf-8').strip()
        except OSError:
            return ''
