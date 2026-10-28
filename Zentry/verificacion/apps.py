from django.apps import AppConfig


class VerificacionConfig(AppConfig):
    """M2 · Reconocimiento y Verificación de Placas."""

    name = 'verificacion'
    verbose_name = 'M2 · Reconocimiento y verificación'

    def ready(self):
        from . import receptores  # noqa: F401  (conecta las señales)
