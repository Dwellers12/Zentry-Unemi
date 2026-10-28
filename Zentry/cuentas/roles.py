"""
Roles de Zentry (RF-11, RNF-08).

Cada rol es un grupo de Django. El superusuario cuenta como Administrador.
  - Administrador: puede hacer todo.
  - Personal de seguridad: consulta el Dashboard y las Alertas, sin editar (RF-10).
  - Agente: cuenta técnica del agente de captura; solo puede usar la API con su token.
"""
from functools import wraps

from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect
from rest_framework.permissions import BasePermission

ADMINISTRADOR = 'Administrador'
SEGURIDAD = 'Personal de seguridad'
AGENTE = 'Agente'

ROLES = (ADMINISTRADOR, SEGURIDAD, AGENTE)
ROLES_WEB = (ADMINISTRADOR, SEGURIDAD)

MENSAJE_DENEGADO = 'Acción denegada: tu rol solo permite consultar alertas'


def rol_de(usuario):
    """Nombre del rol del usuario, o None si no tiene ninguno."""
    if usuario is None or not usuario.is_authenticated:
        return None
    if hasattr(usuario, '_zentry_rol'):
        return usuario._zentry_rol
    if usuario.is_superuser:
        rol = ADMINISTRADOR
    else:
        grupos = set(usuario.groups.values_list('name', flat=True))
        rol = next((r for r in ROLES if r in grupos), None)
    usuario._zentry_rol = rol
    return rol


def es_admin(usuario):
    return rol_de(usuario) == ADMINISTRADOR


def es_usuario_web(usuario):
    return rol_de(usuario) in ROLES_WEB


def es_agente(usuario):
    return rol_de(usuario) == AGENTE


def _quiere_json(request):
    return (request.headers.get('Accept', '').startswith('application/json')
            or request.headers.get('X-Requested-With') == 'fetch')


def requiere_rol(*roles):
    """Decorador para vistas web: exige sesión iniciada y uno de los roles indicados."""

    def decorador(vista):
        @wraps(vista)
        @login_required
        def envoltura(request, *args, **kwargs):
            rol = rol_de(request.user)
            if rol in roles:
                return vista(request, *args, **kwargs)
            if rol not in ROLES_WEB:
                # Cuentas sin rol o del agente: no pueden usar la web.
                logout(request)
                messages.error(request, 'Tu cuenta no tiene acceso a la web de Zentry.')
                return redirect('cuentas:iniciar_sesion')
            if _quiere_json(request):
                return JsonResponse({'error': MENSAJE_DENEGADO}, status=403)
            messages.error(request, MENSAJE_DENEGADO)
            return redirect('alertas:dashboard')

        return envoltura

    return decorador


solo_admin = requiere_rol(ADMINISTRADOR)
usuario_web = requiere_rol(ADMINISTRADOR, SEGURIDAD)


# Permisos para Django REST Framework ----------------------------------------

class EsAgente(BasePermission):
    """Solo la cuenta del agente (con su token) puede enviar lecturas."""

    message = 'Solo el agente de captura puede usar este recurso.'

    def has_permission(self, request, view):
        return es_agente(request.user)


class EsUsuarioWeb(BasePermission):
    message = 'Inicia sesión con una cuenta de Zentry.'

    def has_permission(self, request, view):
        return es_usuario_web(request.user)


class EsAdministrador(BasePermission):
    message = MENSAJE_DENEGADO

    def has_permission(self, request, view):
        return es_admin(request.user)
