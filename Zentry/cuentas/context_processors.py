from django.conf import settings

from . import roles

# Menú lateral (diseño de Figma). La Seguridad solo ve Dashboard y Alertas.
MENU = [
    {'nombre': 'Dashboard', 'url': 'alertas:dashboard', 'icono': 'layout-dashboard', 'seccion': 'dashboard', 'roles': roles.ROLES_WEB},
    {'nombre': 'Residentes', 'url': 'registro:residentes', 'icono': 'users', 'seccion': 'residentes', 'roles': (roles.ADMINISTRADOR,)},
    {'nombre': 'Vehículos y plazas', 'url': 'registro:vehiculos', 'icono': 'car-front', 'seccion': 'vehiculos', 'roles': (roles.ADMINISTRADOR,)},
    {'nombre': 'Alertas', 'url': 'alertas:lista', 'icono': 'bell', 'seccion': 'alertas', 'roles': roles.ROLES_WEB},
    {'nombre': 'Reportes', 'url': 'alertas:reportes', 'icono': 'chart-column', 'seccion': 'reportes', 'roles': (roles.ADMINISTRADOR,)},
    {'nombre': 'Usuarios', 'url': 'cuentas:usuarios', 'icono': 'user-cog', 'seccion': 'usuarios', 'roles': (roles.ADMINISTRADOR,)},
]


def zentry(request):
    usuario = getattr(request, 'user', None)
    rol = roles.rol_de(usuario)
    if not rol:
        return {'refresco_ms': settings.ZENTRY['REFRESCO_MS']}
    nombre = usuario.get_full_name() or usuario.username
    return {
        'rol': rol,
        'es_admin': rol == roles.ADMINISTRADOR,
        'menu': [item for item in MENU if rol in item['roles']],
        'nombre_visible': nombre,
        'iniciales': usuario.username[:2].upper(),
        'refresco_ms': settings.ZENTRY['REFRESCO_MS'],
    }
