"""
Conecta las cámaras virtuales del simulador con Zentry.

    python camaras_virtuales.py

  1. Sirve el simulador (simulador/parqueadero-cctv.html, dentro del proyecto) en
     http://127.0.0.1:8765/ con un conector que envía las imágenes de CAM 01 y
     CAM 02, y lo abre en el navegador. El archivo del simulador no se modifica.
  2. Registra las dos cámaras en Zentry como «cámaras virtuales» con sus plazas
     (los polígonos salen del propio simulador). Si mueves una cámara en el
     simulador, las plazas se vuelven a sincronizar.
  3. Arranca el agente para esas cámaras: reconoce vehículos y placas y envía
     los resultados a Zentry.

Basta con tener abiertos Zentry (python Zentry/manage.py runserver) y este script:
se encuentran solos, sin importar cuál se abra primero.

Opciones útiles:
    --reconocimiento simulador   usa la verdad de terreno en vez de YOLO + lector de placas (rápido, sin IA)
    --solo-puente                no arranca el agente (para usar `python -m agente` aparte)
    --no-abrir                   no abre el navegador
    --resolucion 1920x1080       resolución de captura (por defecto 2560x1440: con menos, el OCR
                                 casi no puede leer las placas del simulador)
"""
import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from urllib.parse import urlsplit

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))

from agente import puente as modulo_puente  # noqa: E402
from agente.__main__ import configurar_registro, preparar_config  # noqa: E402
from agente.cliente import ClienteZentry, Rechazada, SinConexion  # noqa: E402

log = logging.getLogger('zentry.puente')

SIMULADOR_DEFECTO = RAIZ / 'simulador' / 'parqueadero-cctv.html'
# Ubicación anterior, al lado del proyecto: se sigue aceptando.
SIMULADOR_AL_LADO = RAIZ.parent / 'Entorno virtual' / 'parqueadero-cctv.html'
REANUNCIAR_S = 60


def buscar_simulador(ruta=None):
    candidatos = [ruta, os.environ.get('ZENTRY_SIMULADOR'), SIMULADOR_DEFECTO, SIMULADOR_AL_LADO]
    for candidato in candidatos:
        if candidato and Path(candidato).expanduser().is_file():
            return Path(candidato).expanduser().resolve()
    raise SystemExit(f'No encontré el simulador. Pásalo con --simulador RUTA (busqué en {SIMULADOR_DEFECTO}).')


def asegurar_token(config):
    """En local, si falta .agente_token lo crea con `manage.py crear_agente` (cuenta del agente + token)."""
    if config.leer_token():
        return
    manage = RAIZ / 'Zentry' / 'manage.py'
    if urlsplit(config.servidor).hostname not in ('127.0.0.1', 'localhost') or not manage.exists():
        raise SystemExit('Falta el token del agente: define ZENTRY_TOKEN o ejecuta `python manage.py crear_agente`.')
    log.info('Creando la cuenta del agente en la base de datos local...')
    subprocess.run([sys.executable, str(manage), 'crear_agente'], check=True, cwd=manage.parent,
                   stdout=subprocess.DEVNULL)


class Anunciador(threading.Thread):
    """Registra las cámaras virtuales en Zentry y las vuelve a anunciar si cambian sus plazas."""

    def __init__(self, puente, config, base_url, parar):
        super().__init__(name='anunciador', daemon=True)
        self.puente, self.cliente, self.base_url, self.parar = puente, ClienteZentry(config), base_url, parar
        self.anunciadas = {}   # código -> (firma, instante)
        self.servidor = config.servidor
        # Tamaño de las imágenes que recibe Zentry (el agente reduce las de más de ZENTRY_ANCHO_ENVIO px).
        ancho, alto = puente.config['ancho'], puente.config['alto']
        escala = min(1.0, config.ancho_envio / ancho)
        self.tamano = (round(ancho * escala), round(alto * escala))

    def _estado(self, estado, detalle):
        if self.puente.zentry != {'estado': estado, 'detalle': detalle}:
            (log.info if estado == 'conectado' else log.warning)('Zentry: %s', detalle)
        self.puente.zentry = {'estado': estado, 'detalle': detalle}

    def run(self):
        while not self.parar.wait(2):
            camaras = self.puente.camaras()
            if not camaras:
                continue
            try:
                for camara in camaras:
                    firma = json.dumps(camara, sort_keys=True)
                    anterior = self.anunciadas.get(camara['codigo'])
                    if anterior and anterior[0] == firma and time.monotonic() - anterior[1] < REANUNCIAR_S:
                        continue
                    # Las plazas van en coordenadas relativas (no dependen de la resolución).
                    respuesta = self.cliente.anunciar_camara(
                        camara['codigo'], camara['nombre'], 'virtual', f'{self.base_url}/camaras/{camara["codigo"]}',
                        *self.tamano, plazas_sugeridas=camara['plazas_sugeridas'],
                        sincronizar_plazas=True,
                    )
                    if not anterior or anterior[0] != firma:
                        log.info('%s registrada en Zentry (%s%s).', camara['codigo'],
                                 'nueva' if respuesta.get('creada') else 'actualizada',
                                 ', plazas sincronizadas' if respuesta.get('plazas_aplicadas') else '')
                    self.anunciadas[camara['codigo']] = (firma, time.monotonic())
                self._estado('conectado', f'registradas en Zentry ({self.servidor})')
            except SinConexion:
                self.anunciadas.clear()
                self._estado('esperando', f'Zentry no responde en {self.servidor}; reintentando…')
            except Rechazada as error:
                self.anunciadas.clear()
                self._estado('error', f'Zentry rechazó las cámaras: {error.detalle}')


def argumentos():
    p = argparse.ArgumentParser(description='Conecta las cámaras virtuales del simulador con Zentry.')
    p.add_argument('--simulador', help=f'ruta de parqueadero-cctv.html (por defecto {SIMULADOR_DEFECTO})')
    p.add_argument('--puerto', type=int, default=8765, help='puerto del puente (por defecto 8765)')
    p.add_argument('--servidor', help='URL de Zentry (por defecto ZENTRY_URL o http://127.0.0.1:8000)')
    p.add_argument('--reconocimiento', choices=('vision', 'simulador'), default='vision',
                   help='vision = YOLO + lector de placas (por defecto); simulador = verdad de terreno, sin IA')
    p.add_argument('--ocr', choices=('propio', 'auto', 'easyocr'),
                   help='lector de placas: propio (por defecto), auto = propio y EasyOCR si duda, o easyocr')
    p.add_argument('--resolucion', default='2560x1440',
                   help='resolución de captura (por defecto 2560x1440; el agente envía a Zentry como máximo 1920 px)')
    p.add_argument('--intervalo', type=float, default=2.0, help='segundos entre cuadros del simulador')
    p.add_argument('--solo-puente', action='store_true', help='no arrancar el agente')
    p.add_argument('--no-abrir', action='store_true', help='no abrir el navegador')
    p.add_argument('--gpu', action='store_true', help='usar la GPU para la IA si está disponible')
    p.add_argument('--hilos', type=int, default=0, help='hilos de CPU para la IA (0 = todos)')
    p.add_argument('-v', '--detallado', action='store_true')
    datos = p.parse_args()
    try:
        datos.ancho, datos.alto = (int(n) for n in datos.resolucion.lower().split('x'))
    except ValueError:
        p.error('--resolucion debe tener la forma ANCHOxALTO, por ejemplo 1920x1080')
    return datos


def main():
    datos = argumentos()
    configurar_registro(datos.detallado)
    simulador_html = buscar_simulador(datos.simulador)
    config = preparar_config(datos)
    asegurar_token(config)

    puente = modulo_puente.EstadoPuente(datos.ancho, datos.alto, datos.intervalo)
    try:
        servidor = modulo_puente.iniciar(puente, simulador_html, datos.puerto)
    except OSError as error:
        raise SystemExit(f'No se pudo abrir el puerto {datos.puerto}: {error}. ¿Ya está abierto camaras_virtuales.py?')
    base_url = f'http://127.0.0.1:{datos.puerto}'
    log.info('Simulador: %s', simulador_html)
    log.info('Abre el simulador en %s/ (no el archivo .html directamente).', base_url)
    log.info('Cámaras: %s/camaras/CAM_01/video · %s/camaras/CAM_02/video', base_url, base_url)

    parar = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: parar.set())
    Anunciador(puente, config, base_url, parar).start()
    if not datos.no_abrir:
        webbrowser.open(f'{base_url}/')

    agente = None
    try:
        if datos.solo_puente:
            while not parar.wait(1):
                pass
        else:
            from agente.principal import Agente
            agente = Agente(config, modo=datos.reconocimiento, tipos={'virtual'}, sugerir_plazas=False)
            agente.parar = parar
            agente.ejecutar()
    except KeyboardInterrupt:
        pass
    finally:
        parar.set()
        servidor.shutdown()
        log.info('Puente detenido.')


if __name__ == '__main__':
    main()
