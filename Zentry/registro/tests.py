"""Pruebas de M1 · Registro: permisos, plazas por cámara y cámaras que anuncia el agente."""
from django.urls import reverse

from alertas.models import Alerta
from Zentry.pruebas import CLAVE, PruebaZentry, det, poligono

from .models import Camara, Plaza, Vehiculo
from .servicios import ErrorPlazas, guardar_plazas_camara, registrar_camara


class PermisosTest(PruebaZentry):
    def test_solo_el_admin_gestiona_el_registro(self):
        self.client.login(username=self.guardia.username, password=CLAVE)
        for nombre in ('registro:residentes', 'registro:vehiculos'):
            self.assertEqual(self.client.get(reverse(nombre)).status_code, 302, nombre)
        self.client.login(username=self.admin.username, password=CLAVE)
        for nombre in ('registro:residentes', 'registro:vehiculos'):
            self.assertEqual(self.client.get(reverse(nombre)).status_code, 200, nombre)

    def test_crear_vehiculo_valida_la_placa(self):
        self.client.login(username=self.admin.username, password=CLAVE)
        datos = {'placa': 'ABC123', 'marca_modelo': 'Kia Rio', 'color': 'Rojo', 'residente': self.maria.pk,
                 'plaza': self.p1.pk}
        respuesta = self.client.post(reverse('registro:vehiculos'), datos)
        self.assertEqual(respuesta.status_code, 200)  # vuelve al formulario con el error
        self.assertFalse(Vehiculo.objects.filter(placa__startswith='ABC').exists())
        # Segundo vehículo de María en su misma plaza (HU-02).
        respuesta = self.client.post(reverse('registro:vehiculos'), {**datos, 'placa': 'abc-1234'})
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(Vehiculo.objects.get(placa='ABC-1234').plaza_id, self.p1.pk)


class PlazasTest(PruebaZentry):
    def test_el_editor_renombra_y_borra_plazas(self):
        plazas = [{'id': self.p1.pk, 'nombre': 'P1', 'poligono': poligono(0)},
                  {'id': self.p2.pk, 'nombre': 'P2B', 'poligono': poligono(1)}]
        guardar_plazas_camara(self.camara, plazas)
        self.assertEqual(sorted(self.camara.plazas.values_list('nombre', flat=True)), ['P1', 'P2B'])
        self.assertFalse(Plaza.objects.filter(nombre='P3').exists())

    def test_borrar_una_plaza_cierra_sus_alertas(self):
        self.capturar_varias(2, det('P3', 'XYZ-1234'))
        guardar_plazas_camara(self.camara, [{'id': self.p1.pk, 'nombre': 'P1', 'poligono': poligono(0)},
                                            {'id': self.p2.pk, 'nombre': 'P2', 'poligono': poligono(1)}])
        alerta = Alerta.objects.get()
        self.assertEqual(alerta.estado, Alerta.RESUELTA)
        self.assertIn('fue eliminada', alerta.resolucion)

    def test_nombres_repetidos_y_poligonos_invalidos(self):
        with self.assertRaises(ErrorPlazas):
            guardar_plazas_camara(self.camara, [{'nombre': 'P1', 'poligono': poligono(0)},
                                                {'nombre': 'p1', 'poligono': poligono(1)}])
        with self.assertRaises(ErrorPlazas):
            guardar_plazas_camara(self.camara, [{'nombre': 'P9', 'poligono': [[0, 0], [1, 1]]}])


class CamarasDelAgenteTest(PruebaZentry):
    sugeridas = [{'nombre': 'P1', 'poligono': [[0.1, 0.5], [0.3, 0.5], [0.3, 0.9], [0.1, 0.9]]},
                 {'nombre': 'P2', 'poligono': [[0.3, 0.5], [0.5, 0.5], [0.5, 0.9], [0.3, 0.9]]}]

    def test_no_pisa_plazas_editadas_a_mano(self):
        _, creada, aplicadas = registrar_camara('CAM_01', 'CAM 01', Camara.VIRTUAL, plazas_sugeridas=self.sugeridas,
                                                sincronizar_plazas=True)
        self.assertFalse(creada)
        self.assertFalse(aplicadas)  # las plazas de CAM_01 son "manuales"
        self.assertEqual(Camara.objects.get(codigo='CAM_01').plazas_sugeridas[0]['nombre'], 'P1')

    def test_una_imagen_que_pasa_a_camara_virtual_toma_las_plazas_del_simulador(self):
        Camara.objects.filter(codigo='CAM_01').update(tipo=Camara.IMAGEN)
        _, _, aplicadas = registrar_camara('CAM_01', 'CAM 01', Camara.VIRTUAL, plazas_sugeridas=self.sugeridas,
                                           sincronizar_plazas=True)
        self.assertTrue(aplicadas)
        p1 = Plaza.objects.get(nombre='P1')
        self.assertEqual(p1.pk, self.p1.pk)                 # se conserva la plaza (y su vehículo)
        self.assertEqual(p1.poligono, self.sugeridas[0]['poligono'])
        self.assertEqual(self.dmax.__class__.objects.get(pk=self.dmax.pk).plaza_id, self.p1.pk)

    def test_camara_nueva_sin_plazas_usa_las_sugeridas(self):
        sugeridas = [{'nombre': 'P20', 'poligono': self.sugeridas[0]['poligono']}]
        camara, creada, aplicadas = registrar_camara('CAM_05', 'Entrada', Camara.IP_WEBCAM, 'http://192.168.1.8:8080',
                                                     plazas_sugeridas=sugeridas)
        self.assertTrue(creada and aplicadas)
        self.assertEqual(list(camara.plazas.values_list('nombre', flat=True)), ['P20'])
