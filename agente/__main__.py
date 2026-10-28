"""
Agente local de Zentry.

    python -m agente                          # todas las cámaras activas, con YOLO + lector de placas
    python -m agente --tipos ip_webcam        # solo las cámaras IP Webcam
    python -m agente --reconocimiento simulador --tipos virtual
    python -m agente --una-vez -v             # un solo ciclo, con más detalle (pruebas)

Variables de entorno: ZENTRY_URL, ZENTRY_TOKEN, ZENTRY_USAR_GPU, ZENTRY_COLA, ZENTRY_OCR (ver agente/config.py).
"""
import argparse
import logging
import os
import signal

from .config import Config
from .reconocimiento import MODOS
from .vision import MODOS_OCR

TIPOS = ('ip_webcam', 'virtual', 'imagen')


def argumentos(args=None):
    p = argparse.ArgumentParser(prog='python -m agente', description='Agente local de captura de Zentry.',
                                add_help=False)
    p.add_argument('-h', '--ayuda', '--help', action='help', help='muestra esta ayuda')
    p.add_argument('--servidor', help='URL de Zentry (por defecto ZENTRY_URL o http://127.0.0.1:8000)')
    p.add_argument('--reconocimiento', choices=MODOS, default='vision',
                   help='vision = YOLO + lector de placas (por defecto); simulador = verdad de terreno de las cámaras virtuales')
    p.add_argument('--ocr', choices=MODOS_OCR,
                   help='lector de placas: propio (por defecto), auto = propio y EasyOCR si duda, o easyocr')
    p.add_argument('--tipos', help=f'solo estos tipos de cámara, separados por coma ({", ".join(TIPOS)})')
    p.add_argument('--gpu', action='store_true', help='usar la GPU si torch la detecta (igual que ZENTRY_USAR_GPU=1)')
    p.add_argument('--hilos', type=int, default=0, help='hilos de CPU para la IA (0 = todos)')
    p.add_argument('--sin-sugerir', action='store_true', help='no proponer plazas detectadas por las líneas del suelo')
    p.add_argument('--una-vez', action='store_true', help='un solo ciclo y salir')
    p.add_argument('-v', '--detallado', action='store_true', help='más mensajes')
    datos = p.parse_args(args)
    if datos.tipos:
        tipos = {t.strip() for t in datos.tipos.split(',') if t.strip()}
        if tipos - set(TIPOS):
            p.error(f'tipos desconocidos: {", ".join(sorted(tipos - set(TIPOS)))}')
        datos.tipos = tipos
    return datos


def configurar_registro(detallado=False):
    logging.basicConfig(level=logging.DEBUG if detallado else logging.INFO,
                        format='%(asctime)s %(levelname)-7s %(message)s', datefmt='%H:%M:%S')
    for ruidoso in ('urllib3', 'ultralytics', 'PIL', 'easyocr'):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)


def preparar_config(datos):
    config = Config()
    if datos.servidor:
        config.servidor = datos.servidor.rstrip('/')
    if datos.gpu:
        config.usar_gpu = True
    if getattr(datos, 'ocr', None):
        config.ocr = datos.ocr
    if config.ocr not in MODOS_OCR:
        raise SystemExit(f'ZENTRY_OCR debe ser uno de: {", ".join(MODOS_OCR)}')
    if datos.hilos:
        os.environ.setdefault('OMP_NUM_THREADS', str(datos.hilos))
        import torch
        torch.set_num_threads(datos.hilos)
    return config


def main(args=None):
    datos = argumentos(args)
    configurar_registro(datos.detallado)
    from .principal import Agente
    agente = Agente(preparar_config(datos), modo=datos.reconocimiento, tipos=datos.tipos,
                    sugerir_plazas=not datos.sin_sugerir)
    signal.signal(signal.SIGTERM, lambda *_: agente.parar.set())
    try:
        agente.ejecutar(una_vez=datos.una_vez)
    except KeyboardInterrupt:
        agente.parar.set()
    logging.getLogger('zentry.agente').info('Agente detenido.')


if __name__ == '__main__':
    main()
