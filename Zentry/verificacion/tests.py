"""Pruebas de M2 · Verificación: lectura de placas, confirmación de cambios y API del agente."""
import json
from datetime import timedelta

from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework.authtoken.models import Token
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from alertas.models import Alerta
from registro.models import Camara, Plaza, Vehiculo
from Zentry.pruebas import PruebaZentry, det, jpeg

from .models import EstadoPlaza, Lectura, Resultado
from .servicios import configuracion_agente, normalizar_placa, placa_escrita


class PlacasTest(PruebaZentry):
    def test_normaliza_lecturas_del_ocr(self):
        self.assertEqual(normalizar_placa('gbp 3579'), 'GBP-3579')
        self.assertEqual(normalizar_placa('6BP-3S79'), 'GBP-3579')   # 6→G en letras, S→5 en dígitos
        self.assertEqual(normalizar_placa('PBA-721'), 'PBA-721')     # placa antigua de 3 dígitos
        self.assertEqual(normalizar_placa('ECUADOR'), '')
        self.assertEqual(normalizar_placa(''), '')

    def test_placa_escrita_no_adivina(self):
        self.assertEqual(placa_escrita('abc 1234'), 'ABC-1234')
        self.assertEqual(placa_escrita('0BC-1234'), '')  # una persona no confunde 0 con O


class ConfirmacionTest(PruebaZentry):
    def estado(self, plaza):
        return EstadoPlaza.objects.get(plaza=plaza)

    def test_una_lectura_suelta_no_cambia_la_plaza(self):
        self.capturar(det('P2', 'XYZ-1234'))
        self.capturar(det('P2', ocupada=False))
        self.capturar(det('P2', ocupada=False))
        self.assertFalse(Lectura.objects.exists())
        self.assertFalse(self.estado(self.p2).ocupada)

    def test_llegada_autorizada_es_coincidencia(self):
        cambios = self.capturar_varias(2, det('P1', 'GBP-3579'))
        self.assertEqual(cambios, [{'plaza': 'P1', 'tipo': 'llegada', 'placa': 'GBP-3579', 'resultado': 'coincidencia'}])
        self.assertFalse(Alerta.objects.exists())

    def test_vehiculo_en_plaza_ajena_es_discrepancia(self):
        self.capturar_varias(2, det('P2', 'GBP-3579'))
        estado = self.estado(self.p2)
        self.assertEqual(estado.resultado, Resultado.DISCREPANCIA)
        alerta = Alerta.objects.get()
        self.assertEqual((alerta.plaza_nombre, alerta.placa, alerta.estado), ('P2', 'GBP-3579', Alerta.PENDIENTE))

    def test_placa_desconocida_es_no_registrada(self):
        self.capturar_varias(2, det('P3', 'XYZ-1234'))
        self.assertEqual(self.estado(self.p3).resultado, Resultado.NO_REGISTRADA)
        self.assertEqual(Alerta.objects.get().resultado, Alerta.NO_REGISTRADA)

    def test_confianza_baja_es_no_concluyente_tras_esperar_al_ocr(self):
        self.capturar_varias(3, det('P3', 'XYZ-1234', confianza=40))
        self.assertFalse(Alerta.objects.exists())   # al llegar se le da más tiempo al OCR
        self.capturar(det('P3', 'XYZ-1234', confianza=40))
        estado = self.estado(self.p3)
        self.assertEqual((estado.resultado, estado.placa), (Resultado.NO_CONCLUYENTE, ''))
        self.assertEqual(Alerta.objects.get().resultado, Alerta.NO_CONCLUYENTE)

    def test_salida_resuelve_la_alerta_sola(self):
        self.capturar_varias(2, det('P2', 'XYZ-1234'))
        cambios = self.capturar_varias(2, det('P2', ocupada=False))
        self.assertEqual(cambios[0]['tipo'], 'salida')
        alerta = Alerta.objects.get()
        self.assertEqual(alerta.estado, Alerta.RESUELTA)
        self.assertTrue(alerta.resolucion_automatica)
        self.assertIn('salió de la plaza P2', alerta.resolucion)

    def test_registrar_el_vehiculo_resuelve_la_alerta(self):
        self.capturar_varias(2, det('P2', 'XYZ-1234'))
        Vehiculo.objects.create(placa='XYZ-1234', marca_modelo='Kia Rio', color='Rojo', residente=self.maria,
                                plaza=self.p2)
        self.assertEqual(self.estado(self.p2).resultado, Resultado.COINCIDENCIA)
        self.assertEqual(Alerta.objects.get().estado, Alerta.RESUELTA)

    def test_captura_repetida_o_vieja_se_descarta(self):
        self.capturar(det('P1', 'GBP-3579'))
        self.instante -= timedelta(seconds=3)  # misma hora que la anterior
        respuesta = self.capturar(det('P1', 'GBP-3579'))
        self.assertFalse(respuesta['aceptada'])

    def test_guarda_la_imagen_de_la_camara(self):
        self.capturar(det('P1', 'GBP-3579'), imagen=jpeg())
        self.assertTrue(Camara.objects.get().ruta_imagen().exists())


class ApiAgenteTest(PruebaZentry):
    def cliente(self, token=None):
        cliente = APIClient()
        if token:
            cliente.credentials(HTTP_AUTHORIZATION=f'Token {token}')
        return cliente

    def test_sin_token_no_entra(self):
        respuesta = self.cliente().get(reverse('verificacion:api_configuracion'))
        self.assertEqual(respuesta.status_code, 401)

    def test_un_usuario_web_no_puede_usar_la_api_del_agente(self):
        token = Token.objects.create(user=self.admin).key
        respuesta = self.cliente(token).get(reverse('verificacion:api_configuracion'))
        self.assertEqual(respuesta.status_code, 403)

    def test_la_cuenta_del_agente_no_puede_iniciar_sesion_en_la_web(self):
        self.assertFalse(User.objects.get(username='agente').has_usable_password())

    def test_configuracion(self):
        datos = self.cliente(self.token_agente).get(reverse('verificacion:api_configuracion')).json()
        self.assertEqual(datos, configuracion_agente())
        camara = datos['camaras'][0]
        self.assertEqual(camara['codigo'], 'CAM_01')
        self.assertEqual([p['nombre'] for p in camara['plazas']], ['P1', 'P2', 'P3'])
        self.assertEqual(len(datos['version']), 12)

    def test_captura_multipart_con_imagen(self):
        cliente = self.cliente(self.token_agente)
        for _ in range(2):
            respuesta = cliente.post(reverse('verificacion:api_capturas'), {
                'camara': 'CAM_01',
                'detecciones': json.dumps([det('P2', 'XYZ-1234')]),
                'imagen': SimpleUploadedFile('captura.jpg', jpeg(), content_type='image/jpeg'),
            }, format='multipart')
            self.assertEqual(respuesta.status_code, 201, respuesta.content)
        self.assertEqual(respuesta.json()['cambios'][0]['resultado'], 'no_registrada')
        alerta = Alerta.objects.get()
        self.assertTrue(alerta.evidencia and alerta.evidencia_recorte)

    def test_captura_de_camara_desconocida(self):
        respuesta = self.cliente(self.token_agente).post(reverse('verificacion:api_capturas'), {
            'camara': 'CAM_99', 'detecciones': '[]'}, format='multipart')
        self.assertEqual(respuesta.status_code, 400)

    def test_detecciones_mal_formadas(self):
        respuesta = self.cliente(self.token_agente).post(reverse('verificacion:api_capturas'), {
            'camara': 'CAM_01', 'detecciones': json.dumps([{'plaza': 'P1', 'ocupada': True, 'confianza': 500}])},
            format='multipart')
        self.assertEqual(respuesta.status_code, 400)

    def test_el_simulador_registra_una_camara_con_sus_plazas(self):
        plazas = [{'nombre': f'P{n}', 'poligono': [[0.1, 0.5], [0.2, 0.5], [0.2, 0.9], [0.1, 0.9]]} for n in (7, 8)]
        respuesta = self.cliente(self.token_agente).post(reverse('verificacion:api_camaras'), {
            'codigo': 'CAM_02', 'nombre': 'CAM 02', 'tipo': 'virtual', 'url': 'http://127.0.0.1:8765/camaras/CAM_02',
            'ancho': 1920, 'alto': 1080, 'plazas_sugeridas': plazas, 'sincronizar_plazas': True}, format='json')
        self.assertEqual(respuesta.status_code, 201, respuesta.content)
        self.assertTrue(respuesta.json()['plazas_aplicadas'])
        camara = Camara.objects.get(codigo='CAM_02')
        self.assertEqual(camara.plazas_origen, Camara.ORIGEN_SIMULADOR)
        self.assertEqual(sorted(Plaza.objects.filter(camara=camara).values_list('nombre', flat=True)), ['P7', 'P8'])
