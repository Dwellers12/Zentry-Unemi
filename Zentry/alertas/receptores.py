"""Reacciones de alertas a lo que pasa en verificacion (lecturas) y en registro (plazas borradas)."""
from django.db.models.signals import pre_delete
from django.dispatch import receiver

from registro.models import Plaza
from verificacion.senales import estado_plaza_cambiado

from . import servicios


@receiver(estado_plaza_cambiado, dispatch_uid='alertas_estado_plaza')
def estado_plaza(sender, plaza, estado, lectura=None, imagen=None, motivo='lectura', placa_anterior='',
                 usuario=None, **kwargs):
    servicios.sincronizar_plaza(plaza, estado, lectura=lectura, imagen=imagen, motivo=motivo,
                                placa_anterior=placa_anterior, usuario=usuario)


@receiver(pre_delete, sender=Plaza, dispatch_uid='alertas_plaza_eliminada')
def plaza_eliminada(sender, instance, **kwargs):
    # Antes de borrar: las alertas abiertas de esa plaza se cierran con una explicación.
    servicios.resolver_por_plaza_eliminada(instance)
