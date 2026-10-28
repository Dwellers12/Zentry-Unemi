from django.apps import AppConfig


class AlertasConfig(AppConfig):
    """M3 · Gestión de Alertas."""

    name = 'alertas'
    verbose_name = 'M3 · Alertas'

    def ready(self):
        from . import receptores  # noqa: F401  (conecta las señales)
