"""
Motor de base de datos: PostgreSQL de Amazon RDS con la contraseña en AWS Secrets Manager.

RDS guarda la contraseña en Secrets Manager y la cambia sola cada cierto tiempo.
Este motor se la pide a AWS al abrir una conexión (la recuerda 5 minutos) y, si la
base la rechaza porque ya cambió, la vuelve a pedir una vez. Así la contraseña
nunca se copia al .env ni a ningún otro archivo.

Se activa en settings.py con ZENTRY_DB_SECRETO (el ARN del secreto). Las
credenciales de AWS salen de la sesión de `aws login` (en la PC) o del rol del
servidor (en AWS).
"""
import json
import threading
import time

from django.db.backends.postgresql import base

VIGENCIA_SEGUNDOS = 300
_candado = threading.Lock()
_memoria = {}  # arn -> (cuándo se leyó, {'username': ..., 'password': ...})


def _leer_de_aws(arn):
    import boto3

    valor = boto3.client('secretsmanager').get_secret_value(SecretId=arn)['SecretString']
    return json.loads(valor)


def credenciales(arn, renovar=False):
    """Usuario y contraseña del secreto; se vuelven a pedir a AWS cada 5 min o si se pide renovar."""
    with _candado:
        guardado = _memoria.get(arn)
        if renovar or guardado is None or time.monotonic() - guardado[0] > VIGENCIA_SEGUNDOS:
            guardado = (time.monotonic(), _leer_de_aws(arn))
            _memoria[arn] = guardado
        return guardado[1]


class DatabaseWrapper(base.DatabaseWrapper):
    def _secreto(self):
        return self.settings_dict.get('SECRETO_AWS')

    def get_connection_params(self):
        params = super().get_connection_params()
        if self._secreto():
            datos = credenciales(self._secreto())
            params['user'], params['password'] = datos['username'], datos['password']
        return params

    def get_new_connection(self, conn_params):
        try:
            return super().get_new_connection(conn_params)
        except self.Database.OperationalError as error:
            if not self._secreto() or 'password authentication failed' not in str(error):
                raise
            # AWS cambió la contraseña: se pide la nueva y se intenta una vez más.
            datos = credenciales(self._secreto(), renovar=True)
            return super().get_new_connection(
                {**conn_params, 'user': datos['username'], 'password': datos['password']})
