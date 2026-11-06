"""
Adaptadores de los puertos de alertas.

- EvidenciaLocal:      carpeta media/evidencias (desarrollo, SQLite).
- EvidenciaS3:         bucket de Amazon S3 (producción). Variables: ZENTRY_S3_BUCKET,
                       ZENTRY_S3_PREFIJO (opcional) y las credenciales estándar de AWS.
- NotificacionConsola: escribe el aviso en el log del servidor (por defecto).
- NotificacionSMS:     SMS con Amazon SNS al guardia o administrador (ZENTRY_SMS_DESTINOS).
                       Cada SMS a Ecuador cuesta $0,31: se activa a propósito en el .env.
- NotificacionSES:     correo con Amazon SES (RF-13, ZENTRY_SES_ORIGEN, ZENTRY_SES_DESTINOS).

boto3 solo se importa si se usa un adaptador de Amazon.
"""
import logging
import os
import re
import unicodedata
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


# Celulares de Ecuador: 10 dígitos que empiezan por 09 (como en el registro de residentes).
_CELULAR_EC = re.compile(r'^(?:\+?593|0)(9\d{8})$')


def numero_internacional(numero):
    """'0991234567', '+593991234567' o '593 99 123 4567' -> '+593991234567' (formato que pide AWS)."""
    coincidencia = _CELULAR_EC.match(re.sub(r'[\s-]', '', numero))
    if not coincidencia:
        raise ValueError(f'Celular no válido: {numero!r} (usa 09XXXXXXXX)')
    return '+593' + coincidencia.group(1)


def texto_sms(alerta):
    """El aviso en un solo SMS: sin tildes ni símbolos y con 160 caracteres como máximo.

    Un solo carácter fuera del alfabeto básico de los SMS (como «·» o «á») baja el
    límite a 70 caracteres y el aviso se cobraría como 2 SMS.
    """
    texto = (f'Zentry: {alerta.get_resultado_display()} en la plaza {alerta.plaza_nombre}: '
             f'{alerta.placa or "placa ilegible"} ({timezone.localtime(alerta.creada_en):%d/%m/%Y %H:%M})')
    return unicodedata.normalize('NFKD', texto).encode('ascii', 'ignore').decode('ascii')[:160]


class NotificacionSMS(PuertoNotificacion):
    """
    SMS con Amazon SNS a los celulares de ZENTRY_SMS_DESTINOS (separados por comas).
    Usa las credenciales y la región estándar de AWS (AWS_DEFAULT_REGION en el .env).
    """

    def __init__(self, cliente=None, destinos=None):
        if destinos is None:
            destinos = os.environ.get('ZENTRY_SMS_DESTINOS', '').split(',')
        self.destinos = [numero_internacional(d) for d in destinos if d.strip()]
        if not self.destinos:
            raise ValueError('Falta ZENTRY_SMS_DESTINOS con al menos un celular')
        if cliente is None:
            import boto3
            cliente = boto3.client('sns')
        self.cliente = cliente

    def notificar(self, alerta):
        texto = texto_sms(alerta)
        enviados = 0
        for numero in self.destinos:
            try:
                self.cliente.publish(PhoneNumber=numero, Message=texto, MessageAttributes={
                    # «Transactional» = aviso importante: el operador lo entrega con prioridad.
                    'AWS.SNS.SMS.SMSType': {'DataType': 'String', 'StringValue': 'Transactional'},
                })
                enviados += 1
            except Exception:  # noqa: BLE001  (si falla un número, se avisa igual a los demás)
                log.exception('No se pudo enviar el SMS de la alerta %s al celular ...%s', alerta.pk, numero[-4:])
        log.info('Alerta %s avisada por SMS a %s de %s celulares', alerta.pk, enviados, len(self.destinos))
        return enviados


class NotificacionSES(PuertoNotificacion):
    """Correo con Amazon SES (RF-13, pedido del cliente en la Tarea 3)."""

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
