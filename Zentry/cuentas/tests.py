"""Pruebas de cuentas: inicio de sesión, roles y token del agente."""
from django.contrib.auth.models import User
from django.urls import reverse

from Zentry.pruebas import CLAVE, PruebaZentry

from . import roles
from .servicios import token_del_agente


class SesionTest(PruebaZentry):
    def test_inicio_de_sesion(self):
        respuesta = self.client.post(reverse('cuentas:iniciar_sesion'),
                                     {'usuario': self.guardia.username, 'contrasena': CLAVE})
        self.assertRedirects(respuesta, reverse('alertas:dashboard'))

    def test_contrasena_incorrecta(self):
        respuesta = self.client.post(reverse('cuentas:iniciar_sesion'),
                                     {'usuario': self.guardia.username, 'contrasena': 'otra-clave'})
        self.assertEqual(respuesta.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_usuarios_solo_para_el_admin(self):
        self.client.login(username=self.guardia.username, password=CLAVE)
        self.assertEqual(self.client.get(reverse('cuentas:usuarios')).status_code, 302)
        self.client.login(username=self.admin.username, password=CLAVE)
        self.assertEqual(self.client.get(reverse('cuentas:usuarios')).status_code, 200)


class RolesTest(PruebaZentry):
    def test_roles(self):
        self.assertEqual(roles.rol_de(self.admin), roles.ADMINISTRADOR)
        self.assertEqual(roles.rol_de(self.guardia), roles.SEGURIDAD)
        self.assertEqual(roles.rol_de(User.objects.get(username='agente')), roles.AGENTE)
        superusuario = User.objects.create_superuser('raiz', password=CLAVE)
        self.assertEqual(roles.rol_de(superusuario), roles.ADMINISTRADOR)

    def test_renovar_el_token_invalida_el_anterior(self):
        nuevo = token_del_agente(renovar=True, guardar_archivo=False)
        self.assertNotEqual(nuevo, self.token_agente)
