"""Etiquetas y filtros comunes de las plantillas de Zentry."""
from django import template
from django.utils import timezone
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from cuentas.iconos import ICONOS

register = template.Library()


@register.simple_tag
def icono(nombre, tamano=18, clase=''):
    """SVG en línea de un icono de Lucide: {% icono 'bell' 18 'clase' %}."""
    trazos = ICONOS.get(nombre, '')
    return format_html(
        '<svg class="icono {}" width="{}" height="{}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{}</svg>',
        clase, tamano, tamano, mark_safe(trazos),
    )


@register.filter
def hace(fecha):
    """'hace un momento', 'hace 12 min', 'hace 2 h', 'hace 3 días' (como en el diseño)."""
    if not fecha:
        return ''
    minutos = max(0, int((timezone.now() - fecha).total_seconds() // 60))
    if minutos < 1:
        return 'hace un momento'
    if minutos < 60:
        return f'hace {minutos} min'
    if minutos < 1440:
        return f'hace {minutos // 60} h'
    dias = minutos // 1440
    return f'hace {dias} {"día" if dias == 1 else "días"}'


@register.filter
def marca_tiempo(fecha):
    """Milisegundos desde 1970, para que el JavaScript actualice el 'hace X min'."""
    return int(fecha.timestamp() * 1000) if fecha else ''


@register.simple_tag(takes_context=True)
def activo(context, seccion):
    return 'activo' if context.get('seccion') == seccion else ''


ETIQUETAS_EVENTO = {
    'creada': 'Creada por la cámara',
    'revisada': 'Revisada',
    'resuelta': 'Resuelta',
    'automatica': 'Resuelta automáticamente',
    'reabierta': 'Reabierta',
    'editada': 'Descripción editada',
    'corregida': 'Lectura corregida',
    'correccion': 'Lectura corregida',
    'llegada': 'Llegada',
    'salida': 'Salida',
    'cambio': 'Cambio de placa',
}


@register.filter
def etiqueta_evento(evento):
    """Título de un evento del historial o de la actividad (como en el diseño)."""
    tipo = evento.get('tipo') if isinstance(evento, dict) else getattr(evento, 'tipo', '')
    return ETIQUETAS_EVENTO.get(tipo, tipo)


@register.simple_tag
def icono_evento(tipo, tamano=16):
    nombres = {
        'creada': 'bell', 'revisada': 'file-text', 'resuelta': 'shield-check', 'automatica': 'check-check',
        'reabierta': 'refresh-cw', 'editada': 'pencil', 'corregida': 'scan-line', 'correccion': 'scan-line',
        'cambio': 'scan-line', 'llegada': 'log-in', 'salida': 'log-out',
    }
    return icono(nombres.get(tipo, 'info'), tamano)
