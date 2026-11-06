"""Pruebas del motor que pide la contraseña de RDS a AWS Secrets Manager (sin conectarse a AWS)."""
from unittest import mock

from django.db import connections
from django.test import SimpleTestCase

from Zentry.bd_aws import base as bd_aws

ARN = 'arn:aws:secretsmanager:us-east-1:000000000000:secret:rds!db-prueba'


class MotorConSecretoTest(SimpleTestCase):
    def setUp(self):
        bd_aws._memoria.clear()
        self.lecturas = []
        claves = iter(['clave-vieja', 'clave-nueva'])

        def leer(arn):
            self.lecturas.append(arn)
            return {'username': 'zentry_admin', 'password': next(claves)}

        parche = mock.patch.object(bd_aws, '_leer_de_aws', side_effect=leer)
        parche.start()
        self.addCleanup(parche.stop)
        ajustes = {**connections['default'].settings_dict, 'ENGINE': 'Zentry.bd_aws', 'NAME': 'zentry',
                   'USER': '', 'PASSWORD': '', 'HOST': 'base.ejemplo', 'PORT': '5432', 'OPTIONS': {},
                   'SECRETO_AWS': ARN}
        self.bd = bd_aws.DatabaseWrapper(ajustes, alias='prueba_secreto')

    def test_usa_el_usuario_y_la_clave_del_secreto(self):
        params = self.bd.get_connection_params()
        self.assertEqual((params['user'], params['password']), ('zentry_admin', 'clave-vieja'))

    def test_recuerda_el_secreto_unos_minutos(self):
        self.bd.get_connection_params()
        self.bd.get_connection_params()
        self.assertEqual(len(self.lecturas), 1)

    def test_si_la_clave_cambio_la_pide_otra_vez(self):
        params = self.bd.get_connection_params()
        rechazo = self.bd.Database.OperationalError('password authentication failed for user "zentry_admin"')
        with mock.patch.object(bd_aws.base.DatabaseWrapper, 'get_new_connection',
                               side_effect=[rechazo, 'conexion']) as conectar:
            self.assertEqual(self.bd.get_new_connection(params), 'conexion')
        self.assertEqual(conectar.call_args.args[-1]['password'], 'clave-nueva')
        self.assertEqual(len(self.lecturas), 2)

    def test_otros_errores_no_se_reintentan(self):
        params = self.bd.get_connection_params()
        caida = self.bd.Database.OperationalError('connection timeout expired')
        with mock.patch.object(bd_aws.base.DatabaseWrapper, 'get_new_connection', side_effect=caida):
            with self.assertRaises(self.bd.Database.OperationalError):
                self.bd.get_new_connection(params)
        self.assertEqual(len(self.lecturas), 1)
