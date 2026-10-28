from django.conf import settings
from django.core.management.base import BaseCommand

from alertas.servicios import depurar_evidencias
from verificacion.servicios import depurar_lecturas


class Command(BaseCommand):
    help = ('Retención de datos (RF-12): borra la evidencia de alertas resueltas y las lecturas más antiguas '
            'que el periodo de retención. Pensado para ejecutarse una vez al día (cron o Amazon EventBridge).')

    def add_arguments(self, parser):
        parser.add_argument('--dias', type=int, default=None,
                            help=f'días que se conservan (por defecto ZENTRY_RETENCION_DIAS = '
                                 f'{settings.ZENTRY["RETENCION_DIAS"]})')

    def handle(self, *args, dias=None, **opciones):
        if dias is not None and dias < 1:
            self.stderr.write('--dias debe ser 1 o más.')
            return
        evidencias = depurar_evidencias(dias)
        lecturas = depurar_lecturas(dias)
        self.stdout.write(self.style.SUCCESS(
            f'Retención aplicada: evidencia borrada de {evidencias} alertas resueltas y {lecturas} lecturas antiguas.'))
