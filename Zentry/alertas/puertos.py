"""
Puertos (interfaces) del módulo de alertas. Las reglas de negocio solo conocen
estas clases; el adaptador concreto se elige en settings.ZENTRY:

    'EVIDENCIA':    'alertas.adaptadores.EvidenciaLocal'   o  'alertas.adaptadores.EvidenciaS3'
    'NOTIFICACION': 'alertas.adaptadores.NotificacionConsola', '...NotificacionSMS' o '...NotificacionSES'

Así, pasar a Amazon (S3, SMS con SNS, correo con SES) es cambiar la configuración, no el código.
"""
from functools import lru_cache

from django.conf import settings
from django.utils.module_loading import import_string


class PuertoEvidencia:
    """Dónde se guardan las imágenes de evidencia de las alertas."""

    def guardar(self, nombre, datos, tipo='image/jpeg'):
        """Guarda los bytes y devuelve una clave para encontrarlos después."""
        raise NotImplementedError

    def leer(self, clave):
        """Devuelve los bytes guardados o None si ya no existen."""
        raise NotImplementedError

    def eliminar(self, clave):
        raise NotImplementedError


class PuertoNotificacion:
    """Aviso al guardia cuando se crea una alerta grave (consola, SMS o correo)."""

    def notificar(self, alerta):
        raise NotImplementedError


@lru_cache(maxsize=None)
def _crear(ruta):
    return import_string(ruta)()


def evidencia() -> PuertoEvidencia:
    return _crear(settings.ZENTRY['EVIDENCIA'])


def notificacion() -> PuertoNotificacion:
    return _crear(settings.ZENTRY['NOTIFICACION'])


def reiniciar():
    """Olvida los adaptadores creados (útil en pruebas al cambiar la configuración)."""
    _crear.cache_clear()
