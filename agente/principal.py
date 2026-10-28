"""
Bucle del agente:

  1. Pide a Zentry la configuración (cámaras activas, plazas, intervalo). Si el
     servidor no responde usa la última configuración guardada en disco.
  2. Cada `intervalo_s` lee un fotograma de cada cámara, lo reconoce y envía
     detecciones + imagen. Si no hay conexión, la captura va a la cola local y se
     reenvía (en orden) cuando el servidor vuelve.
  3. Si la respuesta trae otra `version` de la configuración (alguien editó las
     plazas o agregó una cámara), la vuelve a pedir.
"""
import json
import logging
import re
import threading
import time
import uuid
from datetime import timezone

import cv2

from .cliente import ClienteZentry, Rechazada, SinConexion
from .cola import Cola
from .fuentes import SinSenal, crear_fuente
from .reconocimiento import Reconocimiento

log = logging.getLogger('zentry.agente')

RECARGAR_CONFIG_S = 60
RECARGAR_SIN_CAMARAS_S = 5   # sin cámaras que vigilar se pregunta más seguido (p. ej. recién abierto el simulador)


class Agente:
    def __init__(self, config, modo='vision', tipos=None, sugerir_plazas=True):
        self.config = config
        self.cliente = ClienteZentry(config)
        self.cola = Cola(config.carpeta_cola)
        self.reconocimiento = Reconocimiento(config, modo)
        self.tipos = set(tipos) if tipos else None   # p. ej. {'virtual'} para el puente del simulador
        self.sugerir = sugerir_plazas
        self.parar = threading.Event()

        self.configuracion = None
        self.version = None
        self.cargada_en = 0.0
        self.fuentes = {}         # código -> (firma, Fuente)
        self.ultimo_cuadro = {}   # código -> cuadro ya procesado
        self.sin_senal = {}       # código -> último mensaje mostrado
        self.sugeridas = set()    # cámaras a las que ya se les propusieron plazas en esta ejecución
        self.conectado = None

    # ------------------------------------------------------------------ configuración
    @property
    def _archivo_config(self):
        return self.config.carpeta_cola.parent / f'{self.config.carpeta_cola.name}_configuracion.json'

    def cargar_configuracion(self, forzar=False):
        espera = RECARGAR_CONFIG_S if self._camaras() else RECARGAR_SIN_CAMARAS_S
        if not forzar and self.configuracion and time.monotonic() - self.cargada_en < espera:
            return
        try:
            datos = self.cliente.configuracion()
        except SinConexion as error:
            self._conexion(False, error)
            if self.configuracion is None and self._archivo_config.exists():
                self.configuracion = json.loads(self._archivo_config.read_text(encoding='utf-8'))
                log.info('Usando la última configuración guardada (%d cámaras).', len(self.configuracion['camaras']))
            return
        self._conexion(True)
        if datos.get('version') != self.version:
            nombres = ', '.join(c['codigo'] for c in self._camaras(datos)) or 'ninguna'
            log.info('Configuración %s · cámaras: %s · cada %s s', datos.get('version'), nombres, datos['intervalo_s'])
        self.configuracion, self.version, self.cargada_en = datos, datos.get('version'), time.monotonic()
        try:
            self._archivo_config.parent.mkdir(parents=True, exist_ok=True)
            self._archivo_config.write_text(json.dumps(datos), encoding='utf-8')
        except OSError:
            pass

    def _camaras(self, datos=None):
        datos = datos or self.configuracion or {}
        return [c for c in datos.get('camaras', []) if self.tipos is None or c.get('tipo') in self.tipos]

    def _conexion(self, ok, error=None):
        if ok and self.conectado is not True:
            log.info('Conectado con Zentry en %s', self.config.servidor)
        elif not ok and self.conectado is not False:
            log.warning('Sin conexión con Zentry (%s). Las capturas se guardan en la cola local.', error)
        self.conectado = ok

    # ------------------------------------------------------------------ fuentes
    def _fuente(self, camara):
        firma = (camara.get('tipo'), camara.get('url'))
        actual = self.fuentes.get(camara['codigo'])
        if actual and actual[0] == firma:
            return actual[1]
        if actual:
            actual[1].cerrar()
        fuente = crear_fuente(camara)
        self.fuentes[camara['codigo']] = (firma, fuente)
        return fuente

    def _aviso_senal(self, codigo, mensaje=None):
        if mensaje and self.sin_senal.get(codigo) != mensaje:
            log.warning('%s', mensaje)
        elif not mensaje and self.sin_senal.get(codigo):
            log.info('%s: señal recuperada.', codigo)
        self.sin_senal[codigo] = mensaje

    # ------------------------------------------------------------------ envío
    def _entregar(self, captura):
        """Envía una captura; si el servidor la rechaza (4xx) se descarta para no trabar la cola."""
        try:
            respuesta = self.cliente.enviar_captura(captura)
        except Rechazada as error:
            log.warning('%s: captura rechazada por el servidor: %s', captura['camara'], error.detalle)
            if error.estado in (401, 403):
                raise SinConexion('token no válido') from error
            return None
        for cambio in respuesta.get('cambios', []):
            log.info('%s %s: %s %s → %s', captura['camara'], cambio['plaza'], cambio['tipo'],
                     cambio.get('placa') or '', cambio['resultado'])
        if respuesta.get('version') and respuesta['version'] != self.version:
            self.cargada_en = 0  # la configuración cambió en el servidor: se vuelve a pedir
        return respuesta

    def enviar(self, captura):
        try:
            if len(self.cola):
                entregadas = self.cola.vaciar(self._entregar)
                log.info('Cola local vaciada: %d capturas reenviadas.', entregadas)
            self._entregar(captura)
            self._conexion(True)
        except SinConexion as error:
            self._conexion(False, error)
            self.cola.guardar(captura)

    def _jpeg(self, fotograma):
        imagen = fotograma.imagen
        alto, ancho = imagen.shape[:2]
        if fotograma.jpeg and ancho <= self.config.ancho_envio:
            return fotograma.jpeg
        if ancho > self.config.ancho_envio:
            escala = self.config.ancho_envio / ancho
            imagen = cv2.resize(imagen, (self.config.ancho_envio, round(alto * escala)), interpolation=cv2.INTER_AREA)
        ok, datos = cv2.imencode('.jpg', imagen, [cv2.IMWRITE_JPEG_QUALITY, self.config.calidad_jpeg])
        return datos.tobytes() if ok else None

    # ------------------------------------------------------------------ ciclo
    def procesar_camara(self, camara):
        codigo = camara['codigo']
        try:
            fuente = self._fuente(camara)
            fotograma = fuente.leer()
        except (SinSenal, ValueError) as error:
            self._aviso_senal(codigo, str(error))
            return False
        self._aviso_senal(codigo, None)
        if fotograma.cuadro and fotograma.cuadro == self.ultimo_cuadro.get(codigo):
            return False  # el simulador aún no envió un cuadro nuevo
        self.ultimo_cuadro[codigo] = fotograma.cuadro

        if self.sugerir and codigo not in self.sugeridas and camara.get('tipo') != 'virtual':
            self._proponer_plazas(camara, fotograma)

        inicio = time.monotonic()
        detecciones = self.reconocimiento.detecciones(camara, fotograma)
        log.debug('%s: %d detecciones en %.2f s', codigo, len(detecciones), time.monotonic() - inicio)
        capturada_en = fotograma.capturada_en.astimezone(timezone.utc)
        self.enviar({
            'camara': codigo,
            'captura': str(uuid.uuid4()),
            'capturada_en': capturada_en.isoformat(timespec='milliseconds'),
            'instante_ms': int(capturada_en.timestamp() * 1000),
            'detecciones': detecciones,
            'jpeg': self._jpeg(fotograma),
        })
        return True

    def _proponer_plazas(self, camara, fotograma):
        """Detecta las plazas por las líneas del suelo y las propone (botón «Detectar automáticamente»)."""
        self.sugeridas.add(camara['codigo'])
        try:
            otras = [p['nombre'] for c in self.configuracion['camaras'] if c['codigo'] != camara['codigo']
                     for p in c.get('plazas', [])]
            numeros = [int(m.group(1)) for n in otras if (m := re.fullmatch(r'P(\d+)', n))]
            sugeridas = self.reconocimiento.sugerir_plazas(fotograma, primer_numero=max(numeros, default=0) + 1)
            if not sugeridas:
                return
            alto, ancho = fotograma.imagen.shape[:2]
            self.cliente.anunciar_camara(camara['codigo'], camara['nombre'], camara['tipo'], camara.get('url', ''),
                                         ancho, alto, plazas_sugeridas=sugeridas)
            log.info('%s: %d plazas detectadas por las líneas del suelo (propuestas en el editor).',
                     camara['codigo'], len(sugeridas))
        except (SinConexion, Rechazada) as error:
            log.warning('%s: no se pudieron proponer las plazas (%s).', camara['codigo'], error)
        except Exception:  # la detección automática nunca debe tumbar al agente
            log.exception('%s: falló la detección automática de plazas.', camara['codigo'])

    def ciclo(self):
        self.cargar_configuracion()
        procesadas = 0
        for camara in self._camaras():
            if self.parar.is_set():
                break
            try:
                procesadas += self.procesar_camara(camara)
            except Exception:
                log.exception('%s: error inesperado al procesar la cámara.', camara['codigo'])
        return procesadas

    def ejecutar(self, una_vez=False):
        log.info('Agente Zentry · servidor %s · reconocimiento: %s', self.config.servidor, self.reconocimiento.modo)
        pendientes = len(self.cola)
        if pendientes:
            log.info('Hay %d capturas en la cola local esperando conexión.', pendientes)
        self.cargar_configuracion(forzar=True)
        self.reconocimiento.precargar()
        while not self.parar.is_set():
            inicio = time.monotonic()
            self.ciclo()
            if una_vez:
                break
            intervalo = float((self.configuracion or {}).get('intervalo_s') or 3)
            self.parar.wait(max(0.2, intervalo - (time.monotonic() - inicio)))
        for _, fuente in self.fuentes.values():
            fuente.cerrar()
