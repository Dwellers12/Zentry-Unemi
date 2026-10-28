from cuentas.roles import es_usuario_web

from .models import Alerta
from .servicios import no_vistas


def alertas(request):
    """Campana de notificaciones: alertas pendientes que el usuario no ha visto y las 5 últimas."""
    usuario = getattr(request, 'user', None)
    if not usuario or not usuario.is_authenticated or not es_usuario_web(usuario):
        return {}
    ultimas = list(Alerta.objects.order_by('-creada_en', '-pk')[:5])
    vistas = set(usuario.alertas_vistas.filter(pk__in=[a.pk for a in ultimas]).values_list('pk', flat=True))
    for alerta in ultimas:
        alerta.vista = alerta.pk in vistas
    return {
        'campana_no_vistas': no_vistas(usuario).count(),
        'alertas_pendientes': Alerta.objects.filter(estado=Alerta.PENDIENTE).count(),
        'campana_ultimas': ultimas,
    }
