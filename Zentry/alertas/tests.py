"""Pruebas de M3 · Alertas: ciclo de vida, permisos por rol, reportes y vista en vivo."""
from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from Zentry.pruebas import CLAVE, PruebaZentry, det, jpeg

from . import servicios
from .models import Alerta, EventoAlerta

FETCH = {'HTTP_X_REQUESTED_WITH': 'fetch'}


class CicloDeVidaTest(PruebaZentry):
    def setUp(self):
        super().setUp()
        self.capturar_varias(2, det('P2', 'GBP-3579'))
        self.alerta = Alerta.objects.get()

    def test_se_crea_con_historial(self):
        self.assertEqual(self.alerta.resultado, Alerta.DISCREPANCIA)
        self.assertEqual(list(self.alerta.eventos.values_list('tipo', flat=True)), [EventoAlerta.CREADA])

    def test_el_mismo_vehiculo_no_duplica_la_alerta(self):
        self.capturar_varias(4, det('P2', 'GBP-3579'))
        self.assertEqual(Alerta.objects.count(), 1)

    def test_resolver_exige_descripcion(self):
        with self.assertRaises(servicios.ErrorAlerta):
            servicios.cambiar_estado(self.alerta, Alerta.RESUELTA, 'corta', self.admin)
        alerta = servicios.cambiar_estado(self.alerta, Alerta.RESUELTA, 'Se habló con la residente.', self.admin)
        self.assertEqual(alerta.estado, Alerta.RESUELTA)
        self.assertFalse(alerta.resolucion_automatica)
        self.assertEqual(alerta.resolucion_autor, 'Ana')

    def test_reabrir_queda_en_el_historial(self):
        servicios.cambiar_estado(self.alerta, Alerta.RESUELTA, 'Se habló con la residente.', self.admin)
        alerta = servicios.cambiar_estado(self.alerta, Alerta.PENDIENTE, '', self.admin)
        self.assertEqual(alerta.estado, Alerta.PENDIENTE)
        self.assertIn(EventoAlerta.REABIERTA, alerta.eventos.values_list('tipo', flat=True))

    def test_corregir_la_placa_a_la_autorizada_resuelve(self):
        # La cámara leyó mal: en realidad es un vehículo autorizado en P2.
        from registro.models import Vehiculo
        Vehiculo.objects.create(placa='GBD-3579', marca_modelo='Kia Rio', color='Gris', residente=self.maria,
                                plaza=self.p2)
        alerta = servicios.corregir_placa(self.alerta, 'GBD-3579', self.admin)
        self.assertEqual(alerta.estado, Alerta.RESUELTA)
        self.assertEqual(alerta.placa, 'GBD-3579')
        # Mientras el vehículo siga ahí, la corrección se mantiene aunque el OCR vuelva a leer mal.
        self.capturar_varias(4, det('P2', 'GBP-3579'))
        self.assertEqual(Alerta.objects.filter(estado=Alerta.PENDIENTE).count(), 0)

    def test_retencion_borra_la_evidencia_vieja(self):
        from verificacion.models import Lectura
        for _ in range(2):
            self.capturar(det('P3', 'XYZ-1234'), imagen=jpeg())
        self.capturar_varias(2, det('P3', ocupada=False))   # sale el vehículo: la alerta se resuelve sola
        alerta = Alerta.objects.get(plaza_nombre='P3')
        self.assertEqual(alerta.estado, Alerta.RESUELTA)
        self.assertTrue(alerta.evidencia and alerta.evidencia_recorte)
        Alerta.objects.update(creada_en=timezone.now() - timedelta(days=100))
        Lectura.objects.update(capturada_en=timezone.now() - timedelta(days=100))
        call_command('depurar', '--dias', '90', stdout=StringIO())
        alerta.refresh_from_db()
        self.assertEqual((alerta.evidencia, alerta.evidencia_recorte), ('', ''))
        self.assertFalse(Lectura.objects.exists())

    def test_csv_sin_inyeccion_de_formulas(self):
        servicios.cambiar_estado(self.alerta, Alerta.RESUELTA, '=HYPERLINK("http://x")', self.admin)
        csv = servicios.exportar_csv(Alerta.objects.all())
        self.assertIn('"\'=HYPERLINK', csv)
        self.assertTrue(csv.startswith('﻿'))


class PermisosTest(PruebaZentry):
    def setUp(self):
        super().setUp()
        self.capturar_varias(2, det('P3', 'XYZ-1234'))
        self.alerta = Alerta.objects.get()

    def entrar(self, usuario):
        self.assertTrue(self.client.login(username=usuario.username, password=CLAVE))

    def test_sin_sesion_va_al_inicio_de_sesion(self):
        for nombre, args in [('alertas:dashboard', []), ('alertas:lista', []), ('alertas:api_estado', []),
                             ('alertas:evidencia', [self.alerta.pk, 'completa'])]:
            respuesta = self.client.get(reverse(nombre, args=args))
            self.assertEqual(respuesta.status_code, 302, nombre)
            self.assertIn(reverse('cuentas:iniciar_sesion'), respuesta['Location'])

    def test_seguridad_consulta_pero_no_cambia_el_estado(self):
        self.entrar(self.guardia)
        self.assertEqual(self.client.get(reverse('alertas:dashboard')).status_code, 200)
        self.assertEqual(self.client.get(reverse('alertas:detalle', args=[self.alerta.pk])).status_code, 200)
        respuesta = self.client.post(reverse('alertas:cambiar_estado', args=[self.alerta.pk]),
                                     {'estado': 'revisada'}, **FETCH)
        self.assertEqual(respuesta.status_code, 403)
        self.assertEqual(Alerta.objects.get().estado, Alerta.PENDIENTE)
        self.assertEqual(self.client.get(reverse('alertas:reportes')).status_code, 302)

    def test_admin_cambia_el_estado(self):
        self.entrar(self.admin)
        respuesta = self.client.post(reverse('alertas:cambiar_estado', args=[self.alerta.pk]),
                                     {'estado': 'resuelta', 'nota': 'El visitante se retiró.'}, **FETCH)
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.assertEqual(Alerta.objects.get().estado, Alerta.RESUELTA)

    def test_no_pisa_un_cambio_hecho_por_otra_persona(self):
        self.entrar(self.admin)
        respuesta = self.client.post(reverse('alertas:cambiar_estado', args=[self.alerta.pk]),
                                     {'estado': 'revisada', 'estado_original': 'revisada'}, **FETCH)
        self.assertEqual(respuesta.status_code, 400)

    def test_estado_en_vivo(self):
        self.entrar(self.guardia)
        datos = self.client.get(reverse('alertas:api_estado')).json()
        self.assertEqual(datos['resumen']['pendientes'], 1)
        plazas = {p['nombre']: p for p in datos['plazas']}
        self.assertEqual(plazas['P3']['resultado'], 'no_registrada')
        self.assertEqual(datos['camaras'][0]['codigo'], 'CAM_01')

    def test_reporte_csv_para_admin(self):
        self.entrar(self.admin)
        respuesta = self.client.get(reverse('alertas:reportes_csv'))
        self.assertEqual(respuesta.status_code, 200)
        self.assertIn('text/csv', respuesta['Content-Type'])
        self.assertIn('XYZ-1234', respuesta.content.decode('utf-8-sig'))
