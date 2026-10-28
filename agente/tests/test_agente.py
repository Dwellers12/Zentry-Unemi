"""
Pruebas del agente y del puente del simulador (no necesitan Django ni los modelos de IA).

    python -m unittest discover -s agente/tests -t .
"""
import json
import math
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import numpy as np
import requests

from agente import cola as modulo_cola
from agente import puente, simulador
from agente.cliente import SinConexion
from agente.config import Config
from agente.fuentes import ImagenEstatica
from agente.principal import Agente
from agente.lector_placas import ALFABETO, decodificar
from agente.vision import Vision, caja_con_margen, combinar, normalizar_placa, verificar_ocupacion

ANCHO, ALTO = 1920, 1080


def carpeta_temporal(prueba, prefijo):
    carpeta = Path(tempfile.mkdtemp(prefix=prefijo))
    prueba.addCleanup(shutil.rmtree, carpeta, ignore_errors=True)
    return carpeta


# ---------------------------------------------------------------------- utilidades

def _perspectiva(fov_grados=60, relacion=16 / 9, cerca=0.1, lejos=100):
    """Matriz de proyección como la de three.js, en orden column-major."""
    f = 1 / math.tan(math.radians(fov_grados) / 2)
    filas = [[f / relacion, 0, 0, 0], [0, f, 0, 0],
             [0, 0, (lejos + cerca) / (cerca - lejos), 2 * lejos * cerca / (cerca - lejos)], [0, 0, -1, 0]]
    return [filas[fila][col] for col in range(4) for fila in range(4)]


IDENTIDAD = [1.0 if fila == col else 0.0 for col in range(4) for fila in range(4)]


def estado_simulador(con_vehiculo=True, placa_visible=True):
    """Estado mínimo con la forma de window.parqueadero.estado(): una cámara que mira hacia −Z."""
    camara = {'id': 'CAM_01', 'nombre': 'CAM 01', 'plazasObjetivo': ['P1', 'P2'],
              'matriz_mundo_a_camara': IDENTIDAD, 'matriz_proyeccion': _perspectiva()}
    plazas = [
        {'id': 'P1', 'proyecciones': {'CAM_01': {'poligono_px': [[400, 600], [900, 600], [900, 1000], [400, 1000]]}}},
        {'id': 'P2', 'proyecciones': {'CAM_01': {'poligono_px': [[960, 600], [1500, 600], [1500, 1000], [960, 1000]]}}},
    ]
    vehiculos = []
    if con_vehiculo:
        vehiculos.append({
            'placa': 'GBP-3579', 'plazaReal': 'P1', 'plazasOcupadas': ['P1'],
            'dimensiones_m': {'alto': 1.5}, 'huella': [[-1, -11], [1, -11], [1, -9], [-1, -9]],
            'placas': [{'texto': 'GBP-3579', 'camaras': {'CAM_01': {
                'en_imagen': 'completa', 'de_frente_a_camara': True,
                'fraccion_sin_oclusion': 1.0 if placa_visible else 0.3, 'ancho_px': 60,
                'poligono_px': [[600, 900], [700, 900], [700, 930], [600, 930]]}}}],
        })
    return {'resolucionReferencia': {'ancho': ANCHO, 'alto': ALTO}, 'camaras': [camara],
            'plazas': plazas, 'vehiculos': vehiculos}


# ---------------------------------------------------------------------- visión

class VisionTest(unittest.TestCase):
    def test_normalizar_placa(self):
        self.assertEqual(normalizar_placa('2QJ-6606'), 'ZQJ-6606')
        self.assertEqual(normalizar_placa('ECUADOR'), None)
        self.assertEqual(normalizar_placa('P11'), None)       # un cartel de plaza no es una placa
        self.assertEqual(normalizar_placa('GBP3579'), 'GBP-3579')

    def test_ocupacion_por_huella(self):
        plazas = [{'id': 'P1', 'poligono': [[0, 500], [500, 500], [500, 1000], [0, 1000]]},
                  {'id': 'P2', 'poligono': [[500, 500], [1000, 500], [1000, 1000], [500, 1000]]}]
        # El techo del vehículo invade P2, pero las ruedas están en P1.
        vehiculo = {'bbox': [100, 300, 700, 950]}
        resultado = {r['id']: r['ocupada'] for r in verificar_ocupacion([vehiculo], plazas)}
        self.assertEqual(resultado, {'P1': True, 'P2': False})


class LectorPlacasTest(unittest.TestCase):
    def test_decodificacion_ctc(self):
        # Columnas: A, A, vacío, A, B, B → "AAB" (las repeticiones seguidas cuentan una vez).
        clases = len(ALFABETO) + 1
        indice = {c: ALFABETO.index(c) + 1 for c in 'AB'}
        secuencia = [indice['A'], indice['A'], 0, indice['A'], indice['B'], indice['B']]
        probs = np.full((len(secuencia), clases), 0.01)
        for t, k in enumerate(secuencia):
            probs[t, k] = 0.9
        probs[4, indice['B']] = 0.6            # la B sale en dos columnas: cuenta la mejor (0.9)
        texto, conf = decodificar(probs)
        self.assertEqual(texto, 'AAB')
        self.assertAlmostEqual(conf, 0.9)
        probs[3, indice['A']] = 0.5            # la confianza es la del carácter más dudoso
        self.assertAlmostEqual(decodificar(probs)[1], 0.5)

    def test_combinar_lecturas(self):
        self.assertEqual(combinar(('GBP-3579', 0.8), ('GBP-3579', 0.6)), ('GBP-3579', 0.92))
        self.assertEqual(combinar(('GBP-3579', 0.8), (None, 0.0)), ('GBP-3579', 0.8))
        # Solo EasyOCR leyó algo: no basta con 90 % (se equivocaba con esas confianzas).
        self.assertEqual(combinar((None, 0.0), ('GBP-3579', 0.9)), ('GBP-3579', 0.68))
        # Si no coinciden, gana la más segura pero baja de confianza (queda "no concluyente").
        self.assertEqual(combinar(('GBP-3579', 0.8), ('GBR-3579', 0.6)), ('GBP-3579', 0.6))

    def test_caja_con_margen_no_sale_de_la_imagen(self):
        imagen = np.zeros((100, 200, 3), np.uint8)
        self.assertEqual(caja_con_margen(imagen, [10, 10, 110, 60], 0.1), [0, 5, 120, 65])
        self.assertEqual(caja_con_margen(imagen, [150, 80, 199, 99], 0.5), [125, 70, 200, 100])

    def _vision(self, modo, lecturas_propio, lectura_easyocr):
        config = Config()
        config.ocr = modo
        vision = Vision(config)
        lector = mock.Mock()
        lector.leer.return_value = lecturas_propio
        vision._propio = lector
        vision._leer_texto = mock.Mock(return_value=lectura_easyocr)
        return vision

    def test_lector_propio_vota_entre_margenes(self):
        imagen = np.zeros((100, 200, 3), np.uint8)
        vision = self._vision('propio', [('GBP-3579', 0.9), ('GBP3579', 0.9), ('GBR-3579', 0.5)], None)
        self.assertEqual(vision.leer_caja(imagen, [50, 40, 150, 70]), ('GBP-3579', 0.6))
        vision._leer_texto.assert_not_called()

    def test_auto_solo_pregunta_a_easyocr_si_duda(self):
        imagen = np.zeros((100, 200, 3), np.uint8)
        segura = self._vision('auto', [('GBP-3579', 0.95)] * 3, ('GBP-3579', 0.5))
        self.assertEqual(segura.leer_caja(imagen, [50, 40, 150, 70]), ('GBP-3579', 0.95))
        segura._leer_texto.assert_not_called()
        dudosa = self._vision('auto', [('GBP-3579', 0.6)] * 3, ('GBP-3579', 0.5))
        self.assertEqual(dudosa.leer_caja(imagen, [50, 40, 150, 70]), ('GBP-3579', 0.8))

    def test_sin_modelo_propio_usa_easyocr(self):
        config = Config()
        config.ocr = 'auto'
        config.modelo_ocr = Path(tempfile.gettempdir()) / 'no-existe-placas_ocr.pt'
        vision = Vision(config)
        vision._leer_texto = mock.Mock(return_value=('GBP-3579', 0.9))
        with self.assertLogs('zentry.agente', 'WARNING'):
            self.assertIsNone(vision.lector_propio())
        self.assertEqual(vision.leer_caja(np.zeros((100, 200, 3), np.uint8), [50, 40, 150, 70]), ('GBP-3579', 0.9))


# ---------------------------------------------------------------------- simulador

class SimuladorTest(unittest.TestCase):
    def test_camaras_con_plazas_relativas(self):
        camaras = simulador.camaras(estado_simulador())
        self.assertEqual(camaras[0]['codigo'], 'CAM_01')
        p1 = camaras[0]['plazas_sugeridas'][0]
        self.assertEqual(p1['nombre'], 'P1')
        self.assertEqual(p1['poligono'][0], [round(400 / ANCHO, 5), round(600 / ALTO, 5)])

    def test_proyeccion_al_centro_y_detras(self):
        camara = estado_simulador()['camaras'][0]
        x, y = simulador.proyectar(camara, (0, 0, -10), ANCHO, ALTO)
        self.assertAlmostEqual(x, ANCHO / 2)
        self.assertAlmostEqual(y, ALTO / 2)
        self.assertIsNone(simulador.proyectar(camara, (0, 0, 10), ANCHO, ALTO))

    def test_detecciones_ideales(self):
        detecciones = {d['plaza']: d for d in simulador.detecciones(estado_simulador(), 'CAM_01')}
        self.assertFalse(detecciones['P2']['ocupada'])
        p1 = detecciones['P1']
        self.assertEqual(p1['placa'], 'GBP-3579')
        self.assertGreaterEqual(p1['confianza'], 75)
        x1, y1, x2, y2 = p1['vehiculo_caja']
        self.assertTrue(0 <= x1 < 0.5 < x2 <= 1 and 0 <= y1 < y2 <= 1)

    def test_placa_tapada_no_se_lee(self):
        p1 = simulador.detecciones(estado_simulador(placa_visible=False), 'CAM_01')[0]
        self.assertTrue(p1['ocupada'])
        self.assertEqual((p1['placa'], p1['confianza']), ('', 0))


# ---------------------------------------------------------------------- cola local

def _captura(instante_ms, camara='CAM_01'):
    return {'camara': camara, 'captura': f'id-{instante_ms}', 'instante_ms': instante_ms,
            'capturada_en': datetime.fromtimestamp(instante_ms / 1000, timezone.utc).isoformat(),
            'detecciones': [{'plaza': 'P1', 'ocupada': False}], 'jpeg': b'\xff\xd8\xff' + b'0' * 10}


class ColaTest(unittest.TestCase):
    def setUp(self):
        self.carpeta = carpeta_temporal(self, 'zentry-cola-')
        self.cola = modulo_cola.Cola(self.carpeta)

    def test_reenvia_en_orden_cronologico(self):
        for instante in (3000, 1000, 2000):
            self.cola.guardar(_captura(instante))
        enviadas = []
        self.assertEqual(self.cola.vaciar(lambda c: enviadas.append(c['instante_ms'])), 3)
        self.assertEqual(enviadas, [1000, 2000, 3000])
        self.assertEqual(len(self.cola), 0)

    def test_se_detiene_si_se_corta_la_conexion(self):
        for instante in (1000, 2000):
            self.cola.guardar(_captura(instante))

        def enviar(captura):
            if captura['instante_ms'] == 2000:
                raise SinConexion('sin red')

        with self.assertRaises(SinConexion):
            self.cola.vaciar(enviar)
        self.assertEqual(len(self.cola), 1)   # la que no se pudo enviar sigue ahí

    def test_limita_el_espacio_en_disco(self):
        with mock.patch.object(modulo_cola, 'MAX_PENDIENTES', 5), mock.patch.object(modulo_cola, 'MAX_CON_IMAGEN', 2):
            for instante in range(1000, 9000, 1000):
                self.cola.guardar(_captura(instante))
        self.assertEqual(len(self.cola), 5)
        self.assertEqual(len(list(self.carpeta.glob('*.jpg'))), 2)


class AgenteSinConexionTest(unittest.TestCase):
    def test_guarda_en_la_cola_y_reenvia_al_volver(self):
        carpeta = carpeta_temporal(self, 'zentry-agente-')
        agente = Agente(Config(servidor='http://127.0.0.1:9', carpeta_cola=carpeta / 'cola'))
        recibidas = []
        servidor_caido = True

        def enviar_captura(captura):
            if servidor_caido:
                raise SinConexion('sin red')
            recibidas.append(captura['instante_ms'])
            return {'cambios': []}

        agente.cliente.enviar_captura = enviar_captura
        agente.enviar(_captura(1000))
        agente.enviar(_captura(2000))
        self.assertEqual(len(agente.cola), 2)
        servidor_caido = False
        agente.enviar(_captura(3000))
        self.assertEqual(recibidas, [1000, 2000, 3000])
        self.assertEqual(len(agente.cola), 0)


class FuentesTest(unittest.TestCase):
    def test_imagen_fuera_del_proyecto_no_se_acepta(self):
        with self.assertRaises(ValueError):
            ImagenEstatica({'codigo': 'CAM_X', 'url': '../../../etc/passwd'})


# ---------------------------------------------------------------------- puente HTTP

class PuenteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.carpeta = Path(tempfile.mkdtemp(prefix='zentry-puente-'))
        html = cls.carpeta / 'simulador.html'
        html.write_text('<html><body><p>simulador</p></body></html>', encoding='utf-8')
        cls.estado = puente.EstadoPuente(1280, 720, 1.0)
        cls.servidor = puente.iniciar(cls.estado, html, puerto=0)
        cls.base = f'http://127.0.0.1:{cls.servidor.server_address[1]}'

    @classmethod
    def tearDownClass(cls):
        cls.servidor.shutdown()
        cls.servidor.server_close()
        shutil.rmtree(cls.carpeta, ignore_errors=True)

    def test_sirve_el_simulador_con_el_conector(self):
        html = requests.get(self.base + '/', timeout=5).text
        self.assertIn('simulador', html)
        self.assertIn('<script src="/zentry-conector.js" defer></script>', html)
        self.assertEqual(requests.get(self.base + '/api/config', timeout=5).json()['ancho'], 1280)

    def test_recibe_cuadros_y_publica_las_camaras(self):
        self.assertEqual(requests.get(self.base + '/camaras/CAM_01/shot.jpg', timeout=5).status_code, 503)
        foto = b'\xff\xd8\xff\xe0' + b'1' * 100
        respuesta = requests.post(self.base + '/api/cuadro', timeout=5, files={
            't': (None, '1700000000000'),
            'estado': ('estado.json', json.dumps(estado_simulador()), 'application/json'),
            'CAM_01': ('CAM_01.jpg', foto, 'image/jpeg'),
        })
        self.assertEqual(respuesta.status_code, 200, respuesta.text)
        foto_publicada = requests.get(self.base + '/camaras/CAM_01/shot.jpg', timeout=5)
        self.assertEqual(foto_publicada.content, foto)
        self.assertEqual(foto_publicada.headers['X-Zentry-Cuadro'], '1700000000000')
        camaras = requests.get(self.base + '/api/camaras', timeout=5).json()
        self.assertEqual(camaras[0]['codigo'], 'CAM_01')
        estado = requests.get(self.base + '/api/estado', params={'cuadro': '1700000000000'}, timeout=5).json()
        self.assertEqual(estado['vehiculos'][0]['placa'], 'GBP-3579')

    def test_rechaza_otros_hosts_y_origenes(self):
        self.assertEqual(requests.get(self.base + '/api/salud', headers={'Host': 'evil.example'}, timeout=5).status_code, 403)
        respuesta = requests.post(self.base + '/api/cuadro', headers={'Origin': 'https://evil.example'},
                                  files={'t': (None, '1')}, timeout=5)
        self.assertEqual(respuesta.status_code, 403)

    def test_solo_acepta_jpeg(self):
        respuesta = requests.post(self.base + '/api/cuadro', timeout=5, files={
            't': (None, '1700000000001'), 'estado': ('estado.json', '{}', 'application/json'),
            'CAM_01': ('x.jpg', b'<script>', 'image/jpeg')})
        self.assertEqual(respuesta.status_code, 400)


if __name__ == '__main__':
    unittest.main()
