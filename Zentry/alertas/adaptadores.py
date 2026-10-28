"""
Adaptadores de los puertos de alertas.

- EvidenciaLocal:      carpeta media/evidencias (desarrollo, SQLite).
- EvidenciaS3:         bucket de Amazon S3 (producción). Variables: ZENTRY_S3_BUCKET,
                       ZENTRY_S3_PREFIJO (opcional) y las credenciales estándar de AWS.
- NotificacionConsola: escribe el aviso en el log del servidor.
- NotificacionSNS:     publica en un tema de Amazon SNS (ZENTRY_SNS_TOPIC_ARN).
- NotificacionSES:     envía un correo con Amazon SES (ZENTRY_SES_ORIGEN, ZENTRY_SES_DESTINOS).

boto3 solo se importa si se usa un adaptador de Amazon.
"""
import logging
import os
import re
import uuid
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from .puertos import PuertoEvidencia, PuertoNotificacion

log = logging.getLogger('zentry.alertas')


def _nombre_seguro(nombre):
    return re.sub(r'[^A-Za-z0-9_.-]', '_', nombre)[:80]


def _texto_alerta(alerta):
    return (f'Zentry · {alerta.get_resultado_display()} en la plaza {alerta.plaza_nombre}: '
            f'{alerta.placa or "placa ilegible"} ({timezone.localtime(alerta.creada_en):%d/%m/%Y %H:%M})')


class EvidenciaLocal(PuertoEvidencia):
    def __init__(self, raiz=None):
        self.raiz = Path(raiz or settings.MEDIA_ROOT) / 'evidencias'

    def _ruta(self, clave):
        ruta = (self.raiz / clave).resolve()
        if self.raiz.resolve() not in ruta.parents:
            raise ValueError('Clave de evidencia no válida')
        return ruta

    def guardar(self, nombre, datos, tipo='image/jpeg'):
        hoy = timezone.localdate()
        clave = f'{hoy:%Y/%m}/{uuid.uuid4().hex[:12]}_{_nombre_seguro(nombre)}'
        ruta = self._ruta(clave)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        temporal = ruta.with_name(ruta.name + '.tmp')
        temporal.write_bytes(datos)
        os.replace(temporal, ruta)
        return clave

    def leer(self, clave):
        try:
            return self._ruta(clave).read_bytes()
        except (OSError, ValueError):
            return None

    def eliminar(self, clave):
        try:
            self._ruta(clave).unlink(missing_ok=True)
        except (OSError, ValueError):
            pass


class EvidenciaS3(PuertoEvidencia):
    def __init__(self):
        import boto3

        self.bucket = os.environ['ZENTRY_S3_BUCKET']
        self.prefijo = os.environ.get('ZENTRY_S3_PREFIJO', 'evidencias/')
        self.cliente = boto3.client('s3')

    def guardar(self, nombre, datos, tipo='image/jpeg'):
        hoy = timezone.localdate()
        clave = f'{self.prefijo}{hoy:%Y/%m}/{uuid.uuid4().hex[:12]}_{_nombre_seguro(nombre)}'
        self.cliente.put_object(Bucket=self.bucket, Key=clave, Body=datos, ContentType=tipo,
                                ServerSideEncryption='AES256')
        return clave

    def leer(self, clave):
        try:
            return self.cliente.get_object(Bucket=self.bucket, Key=clave)['Body'].read()
        except Exception:  # noqa: BLE001  (objeto borrado o sin permisos)
            log.exception('No se pudo leer la evidencia %s', clave)
            return None

    def eliminar(self, clave):
        self.cliente.delete_object(Bucket=self.bucket, Key=clave)


class NotificacionConsola(PuertoNotificacion):
    def notificar(self, alerta):
        log.warning('Nueva alerta · %s', _texto_alerta(alerta))


class NotificacionSNS(PuertoNotificacion):
    def __init__(self):
        import boto3

        self.tema = os.environ['ZENTRY_SNS_TOPIC_ARN']
        self.cliente = boto3.client('sns')

    def notificar(self, alerta):
        self.cliente.publish(TopicArn=self.tema, Subject='Zentry · nueva alerta', Message=_texto_alerta(alerta))


class NotificacionSES(PuertoNotificacion):
    def __init__(self):
        import boto3

        self.origen = os.environ['ZENTRY_SES_ORIGEN']
        self.destinos = [d.strip() for d in os.environ['ZENTRY_SES_DESTINOS'].split(',') if d.strip()]
        self.cliente = boto3.client('ses')

    def notificar(self, alerta):
        texto = _texto_alerta(alerta)
        self.cliente.send_email(
            Source=self.origen,
            Destination={'ToAddresses': self.destinos},
            Message={'Subject': {'Data': 'Zentry · nueva alerta'}, 'Body': {'Text': {'Data': texto}}},
        )
