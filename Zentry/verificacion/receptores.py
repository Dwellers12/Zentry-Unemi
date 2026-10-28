"""Reacciones de verificacion a los cambios del registro (M1)."""
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from registro.models import Vehiculo

from .servicios import reevaluar_plazas


@receiver(post_save, sender=Vehiculo, dispatch_uid='verificacion_vehiculo_guardado')
@receiver(post_delete, sender=Vehiculo, dispatch_uid='verificacion_vehiculo_eliminado')
def vehiculo_cambiado(sender, instance, **kwargs):
    # Un vehículo nuevo, editado o borrado puede volver legal (o ilegal) a uno ya estacionado.
    reevaluar_plazas()
