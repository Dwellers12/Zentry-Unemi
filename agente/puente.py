"""
Puente HTTP entre el simulador (navegador) y el agente.

    GET  /                          el simulador con el conector de Zentry inyectado
    GET  /zentry-conector.js        el conector
    GET  /api/config                resolución e intervalo de captura (lo lee el conector)
    POST /api/cuadro                el conector envía: t, estado (JSON) y un JPEG por cámara
    GET  /api/estado[?cuadro=ID]    estado del simulador (verdad de terreno) de ese cuadro
    GET  /api/camaras               cámaras virtuales y sus plazas (coordenadas relativas)
    GET  /api/salud                 ¿está conectado el simulador?
    GET  /camaras/CAM_01/shot.jpg   última imagen de la cámara (como la app IP Webcam)
    GET  /camaras/CAM_01/video      vídeo MJPEG (se puede abrir en el navegador, VLC u OBS)

Solo escucha en 127.0.0.1 y solo acepta peticiones con Host local.
"""
import json
import logging
import re
import threading
import time
from collections import OrderedDict
from email.parser import BytesParser
from email.policy import default as politica_email
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import simulador

log = logging.getLogger('zentry.puente')

CONECTOR = Path(__file__).with_name('conector_simulador.js')
MAX_CUERPO = 40 * 1024 * 1024
HISTORIAL = 8          # estados guardados para el modo "simulador" del agente
SIN_SIMULADOR_S = 15   # sin cuadros en este tiempo = simulador desconectado
CAMARA = re.compile(r'^/camaras/([A-Za-z0-9_-]{1,30})/(shot\.jpg|video)$')


class EstadoPuente:
    """Último cuadro recibido del simulador (compartido entre los hilos del servidor)."""

    def __init__(self, ancho=1920, alto=1080, intervalo_s=2.0, calidad_jpeg=0.9):
        self.config = {'ancho': ancho, 'alto': alto, 'intervalo_ms': int(intervalo_s * 1000),
                       'calidad_jpeg': calidad_jpeg}
        self.condicion = threading.Condition()
        self.cuadro = None                   # {'id', 't_ms', 'jpeg': {codigo: bytes}}
        self.estados = OrderedDict()         # id de cuadro -> estado del simulador
        self.recibido_en = 0.0
        self.zentry = {'estado': 'esperando', 'detalle': 'esperando a Zentry…'}

    def guardar(self, t_ms, estado, imagenes):
        with self.condicion:
            ident = str(t_ms)
            self.cuadro = {'id': ident, 't_ms': t_ms, 'jpeg': imagenes}
            self.estados[ident] = estado
            while len(self.estados) > HISTORIAL:
                self.estados.popitem(last=False)
            self.recibido_en = time.monotonic()
            self.condicion.notify_all()

    def estado(self, ident=None):
        with self.condicion:
            if ident and ident in self.estados:
                return self.estados[ident]
            return next(reversed(self.estados.values())) if self.estados else None

    def conectado(self):
        return self.cuadro is not None and time.monotonic() - self.recibido_en < SIN_SIMULADOR_S

    def camaras(self):
        estado = self.estado()
        return simulador.camaras(estado) if estado else []


def _partes_multipart(tipo, cuerpo):
    """Campos de un multipart/form-data: {nombre: bytes}. (El módulo cgi ya no existe en Python 3.13+.)"""
    mensaje = BytesParser(policy=politica_email).parsebytes(
        f'Content-Type: {tipo}\r\nMIME-Version: 1.0\r\n\r\n'.encode() + cuerpo)
    if not mensaje.is_multipart():
        raise ValueError('Se esperaba multipart/form-data.')
    campos = {}
    for parte in mensaje.iter_parts():
        nombre = parte.get_param('name', header='content-disposition')
        if nombre:
            campos[nombre] = parte.get_payload(decode=True) or b''
    return campos


def crear_manejador(puente, simulador_html, puerto):
    hosts_validos = {f'127.0.0.1:{puerto}', f'localhost:{puerto}'}

    class Manejador(BaseHTTPRequestHandler):
        server_version = 'ZentryPuente/1.0'
        protocol_version = 'HTTP/1.1'

        def log_message(self, formato, *args):
            log.debug('%s %s', self.address_string(), formato % args)

        # -------------------------------------------------------------- utilidades
        def _responder(self, codigo, cuerpo=b'', tipo='application/json', cabeceras=None):
            if isinstance(cuerpo, (dict, list)):
                cuerpo = json.dumps(cuerpo, ensure_ascii=False).encode()
            elif isinstance(cuerpo, str):
                cuerpo = cuerpo.encode()
            self.send_response(codigo)
            self.send_header('Content-Type', tipo)
            self.send_header('Content-Length', str(len(cuerpo)))
            self.send_header('Cache-Control', 'no-store')
            for clave, valor in (cabeceras or {}).items():
                self.send_header(clave, valor)
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(cuerpo)

        def _error(self, codigo, mensaje):
            self._responder(codigo, {'detail': mensaje})

        def _host_local(self):
            # Evita que una página de internet use el puente con DNS rebinding.
            if self.headers.get('Host') not in hosts_validos:
                self._error(403, 'Host no permitido.')
                return False
            return True

        # -------------------------------------------------------------- GET
        def do_GET(self):  # noqa: N802 (nombre impuesto por http.server)
            if not self._host_local():
                return
            url = urlsplit(self.path)
            ruta, consulta = url.path, parse_qs(url.query)
            if ruta in ('/', '/index.html'):
                return self._simulador()
            if ruta == '/zentry-conector.js':
                return self._responder(200, CONECTOR.read_bytes(), 'text/javascript; charset=utf-8')
            if ruta == '/api/config':
                return self._responder(200, puente.config)
            if ruta == '/api/salud':
                return self._responder(200, {'simulador_conectado': puente.conectado(),
                                             'cuadro': puente.cuadro['id'] if puente.cuadro else None,
                                             'zentry': puente.zentry})
            if ruta == '/api/camaras':
                return self._responder(200, puente.camaras())
            if ruta == '/api/estado':
                estado = puente.estado((consulta.get('cuadro') or [None])[0])
                return self._responder(200, estado) if estado else self._error(503, 'El simulador aún no se conecta.')
            coincide = CAMARA.match(ruta)
            if coincide:
                codigo, recurso = coincide.groups()
                return self._foto(codigo) if recurso == 'shot.jpg' else self._video(codigo)
            self._error(404, 'No existe.')

        do_HEAD = do_GET

        def _simulador(self):
            try:
                html = simulador_html.read_text(encoding='utf-8')
            except OSError as error:
                return self._responder(500, f'No se pudo leer el simulador: {error}', 'text/plain; charset=utf-8')
            etiqueta = '<script src="/zentry-conector.js" defer></script>\n'
            html = html.replace('</body>', etiqueta + '</body>', 1) if '</body>' in html else html + etiqueta
            self._responder(200, html, 'text/html; charset=utf-8')

        def _foto(self, codigo):
            cuadro = puente.cuadro
            if not puente.conectado() or not cuadro or codigo not in cuadro['jpeg']:
                return self._error(503, f'{codigo}: sin imagen. ¿Está abierto el simulador en http://127.0.0.1:{puerto}/ ?')
            self._responder(200, cuadro['jpeg'][codigo], 'image/jpeg', {
                'X-Zentry-Cuadro': cuadro['id'], 'X-Zentry-Capturada': str(cuadro['t_ms']),
            })

        def _video(self, codigo):
            limite = 'zentrycuadro'
            self.send_response(200)
            self.send_header('Content-Type', f'multipart/x-mixed-replace; boundary={limite}')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Connection', 'close')
            self.end_headers()
            enviado = None
            try:
                while True:
                    with puente.condicion:
                        puente.condicion.wait_for(lambda: puente.cuadro and puente.cuadro['id'] != enviado, timeout=30)
                        cuadro = puente.cuadro
                    if not cuadro or cuadro['id'] == enviado or codigo not in cuadro['jpeg']:
                        continue
                    datos = cuadro['jpeg'][codigo]
                    self.wfile.write(f'--{limite}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(datos)}\r\n\r\n'.encode())
                    self.wfile.write(datos + b'\r\n')
                    self.wfile.flush()
                    enviado = cuadro['id']
            except (BrokenPipeError, ConnectionResetError):
                pass
            self.close_connection = True

        # -------------------------------------------------------------- POST
        def do_POST(self):  # noqa: N802
            try:
                largo = int(self.headers.get('Content-Length') or 0)
            except ValueError:
                largo = -1
            if not 0 < largo <= MAX_CUERPO:
                self.close_connection = True
                return self._error(413, 'Cuadro vacío o demasiado grande.')
            cuerpo = self.rfile.read(largo)  # se lee siempre, para responder sin cortar la conexión
            if not self._host_local():
                return
            origen = self.headers.get('Origin')
            if origen and urlsplit(origen).netloc not in hosts_validos:
                return self._error(403, 'Origen no permitido.')
            if urlsplit(self.path).path != '/api/cuadro':
                return self._error(404, 'No existe.')
            try:
                campos = _partes_multipart(self.headers.get('Content-Type', ''), cuerpo)
                t_ms = int(campos.pop('t').decode())
                estado = json.loads(campos.pop('estado'))
            except (KeyError, ValueError) as error:
                return self._error(400, f'Cuadro no válido: {error}')
            imagenes = {codigo: datos for codigo, datos in campos.items()
                        if re.fullmatch(r'[A-Za-z0-9_-]{1,30}', codigo) and datos[:3] == b'\xff\xd8\xff'}
            if not imagenes:
                return self._error(400, 'El cuadro no trae imágenes JPEG.')
            puente.guardar(t_ms, estado, imagenes)
            self._responder(200, {'cuadro': str(t_ms), 'camaras': sorted(imagenes), 'zentry': puente.zentry})

    return Manejador


class ServidorPuente(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def iniciar(puente, simulador_html, puerto=8765):
    """Arranca el servidor en un hilo y lo devuelve (servidor.shutdown() para detenerlo)."""
    servidor = ServidorPuente(('127.0.0.1', puerto), BaseHTTPRequestHandler)
    # Con puerto=0 el sistema elige uno libre: el manejador necesita el real para validar el Host.
    servidor.RequestHandlerClass = crear_manejador(puente, Path(simulador_html), servidor.server_address[1])
    hilo = threading.Thread(target=servidor.serve_forever, name='puente-simulador', daemon=True)
    hilo.start()
    return servidor
