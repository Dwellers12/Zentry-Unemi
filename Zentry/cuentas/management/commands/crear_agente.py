from django.conf import settings
from django.core.management.base import BaseCommand

from cuentas import servicios


class Command(BaseCommand):
    help = 'Crea la cuenta del agente de captura y muestra su token (lo guarda en .agente_token).'

    def add_arguments(self, parser):
        parser.add_argument('--renovar', action='store_true', help='Invalida el token anterior y crea uno nuevo.')

    def handle(self, *args, **opciones):
        clave = servicios.token_del_agente(renovar=opciones['renovar'])
        self.stdout.write(self.style.SUCCESS(f'Token del agente: {clave}'))
        self.stdout.write(f'Guardado en {settings.ZENTRY["TOKEN_AGENTE_ARCHIVO"]}')
