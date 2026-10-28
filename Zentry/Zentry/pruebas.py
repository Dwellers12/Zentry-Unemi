"""Datos y utilidades compartidas por las pruebas de las apps (python manage.py test)."""
import io
import shutil
import tempfile
from datetime import timedelta
from pathlib import Path

from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image

from alertas import puertos
from cuentas import roles
from cuentas.servicios import crear_usuario, token_del_agente
from registro.models import Camara, Plaza, Residente, Vehiculo

CLAVE = 'ClaveDePrueba2026'


def poligono(i):
    """Plaza rectangular número i (relativa) en una fila de la imagen."""
    x = 0.05 + i * 0.15
    return [[x, 0.5], [x + 0.14, 0.5], [x + 0.14, 0.9], [x, 0.9]]


def jpeg(ancho=320, alto=180, color=(90, 90, 90)):
    salida = io.BytesIO()
    Image.new('RGB', (ancho, alto), color).save(salida, 'JPEG')
    return salida.getvalue()


def det(plaza, placa='', confianza=90, ocupada=True):
    """Detección como la envía el agente."""
    if not ocupada:
        return {'plaza': plaza, 'ocupada': False}
    return {'plaza': plaza, 'ocupada': True, 'placa': placa, 'confianza': confianza,
            'vehiculo_caja': [0.1, 0.3, 0.3, 0.9], 'placa_caja': [0.15, 0.7, 0.2, 0.75]}


class PruebaZentry(TestCase):
    """
    Escenario mínimo: una cámara con P1, P2 y P3; María tiene GBP-3579 en P1.
    Los archivos (imágenes, evidencia) van a una carpeta temporal.
    """

    @classmethod
    def setUpClass(cls):
        cls._media = tempfile.mkdtemp(prefix='zentry-pruebas-')
        cls._ajustes = override_settings(MEDIA_ROOT=Path(cls._media))
        cls._ajustes.enable()
        puertos.reiniciar()  # el adaptador de evidencia toma la carpeta nueva
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._ajustes.disable()
        puertos.reiniciar()
        shutil.rmtree(cls._media, ignore_errors=True)

    @classmethod
    def setUpTestData(cls):
        cls.camara = Camara.objects.create(codigo='CAM_01', nombre='Cámara 1 · Fila A', tipo=Camara.VIRTUAL)
        cls.p1 = Plaza.objects.create(nombre='P1', camara=cls.camara, poligono=poligono(0))
        cls.p2 = Plaza.objects.create(nombre='P2', camara=cls.camara, poligono=poligono(1))
        cls.p3 = Plaza.objects.create(nombre='P3', camara=cls.camara, poligono=poligono(2))
        cls.maria = Residente.objects.create(nombre='María Torres', cedula='0912345678', departamento='3A',
                                             telefono='0991234567')
        cls.dmax = Vehiculo.objects.create(placa='GBP-3579', marca_modelo='Isuzu D-Max', color='Blanco',
                                           residente=cls.maria, plaza=cls.p1)
        cls.admin = crear_usuario('admin_prueba', CLAVE, roles.ADMINISTRADOR, first_name='Ana')
        cls.guardia = crear_usuario('guardia_prueba', CLAVE, roles.SEGURIDAD, first_name='Luis')
        cls.token_agente = token_del_agente(guardar_archivo=False)

    def setUp(self):
        self.instante = timezone.now() - timedelta(minutes=10)

    def capturar(self, *detecciones, imagen=None):
        """Envía una captura al servicio de verificación (cada una 3 s después de la anterior)."""
        from verificacion.servicios import procesar_captura
        self.instante += timedelta(seconds=3)
        return procesar_captura(self.camara, list(detecciones), capturada_en=self.instante, imagen=imagen)

    def capturar_varias(self, veces, *detecciones):
        cambios = []
        for _ in range(veces):
            cambios += self.capturar(*detecciones)['cambios']
        return cambios
