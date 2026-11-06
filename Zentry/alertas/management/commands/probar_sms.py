"""
Manda UN solo SMS de prueba al guardia con la última alerta grave.

    python manage.py probar_sms        # muestra el texto y el costo, y pregunta antes de enviar
    python manage.py probar_sms --si   # envía sin preguntar

Funciona aunque el SMS esté apagado (ZENTRY_NOTIFICACION): solo necesita
ZENTRY_SMS_DESTINOS y las credenciales de AWS. Cada SMS a Ecuador cuesta $0,31 y una
cuenta nueva de AWS solo permite gastar $1 al mes en SMS (unos 3 mensajes).
"""
from django.core.management.base import BaseCommand, CommandError

from alertas.adaptadores import NotificacionSMS, texto_sms
from alertas.models import Alerta

PRECIO_SMS_ECUADOR = 0.31


class Command(BaseCommand):
    help = 'Manda un solo SMS de prueba con la última alerta grave.'

    def add_arguments(self, parser):
        parser.add_argument('--si', action='store_true', help='Envía sin pedir confirmación.')

    def handle(self, *args, si=False, **opciones):
        alerta = (Alerta.objects.filter(resultado__in=Alerta.RESULTADOS_CON_AVISO)
                  .order_by('-creada_en').first())
        if alerta is None:
            raise CommandError('No hay alertas graves para usar en la prueba.')
        try:
            sms = NotificacionSMS()
        except Exception as error:  # noqa: BLE001  (falta el celular, la región o boto3)
            raise CommandError(f'No se puede preparar el SMS: {error}') from error

        texto = texto_sms(alerta)
        self.stdout.write(f'Texto ({len(texto)} caracteres, 1 SMS): {texto}')
        self.stdout.write(f'Para: {", ".join(sms.destinos)}  |  costo: ${PRECIO_SMS_ECUADOR * len(sms.destinos):.2f}')
        if not si and input('¿Enviar? Escribe "si": ').strip().lower() not in ('si', 'sí'):
            self.stdout.write('No se envió nada.')
            return

        enviados = sms.notificar(alerta)
        if not enviados:
            raise CommandError('No se envió (mira el error de arriba: sesión de AWS o celular sin verificar).')
        self.stdout.write(self.style.SUCCESS(f'SMS enviado a {enviados} celular(es).'))
