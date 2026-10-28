"""Lógica de negocio de cuentas: crear usuarios con su rol y gestionar el token del agente."""
import os

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.db import transaction
from rest_framework.authtoken.models import Token

from . import roles

USUARIO_AGENTE = 'agente'


@transaction.atomic
def crear_usuario(nombre, contrasena, rol, **datos):
    """Crea un usuario de la web con el rol indicado."""
    if rol not in roles.ROLES_WEB:
        raise ValueError(f'Rol no válido: {rol}')
    usuario = User.objects.create_user(username=nombre, password=contrasena, **datos)
    usuario.groups.set([Group.objects.get(name=rol)])
    if rol == roles.ADMINISTRADOR:
        # El administrador también puede entrar al panel /admin/ de Django.
        usuario.is_staff = True
        usuario.save(update_fields=['is_staff'])
    return usuario


def asignar_rol(usuario, rol):
    usuario.groups.set([Group.objects.get(name=rol)])
    usuario.__dict__.pop('_zentry_rol', None)


@transaction.atomic
def token_del_agente(renovar=False, guardar_archivo=True):
    """
    Devuelve el token de la cuenta del agente, creándola si no existe.

    El agente lo envía en cada petición ("Authorization: Token <clave>"). En
    desarrollo también se guarda en .agente_token para que el agente y el puente
    del simulador se conecten solos.
    """
    usuario, creado = User.objects.get_or_create(
        username=USUARIO_AGENTE,
        defaults={'first_name': 'Agente', 'last_name': 'de captura'},
    )
    if creado or usuario.has_usable_password():
        usuario.set_unusable_password()
        usuario.save()
    asignar_rol(usuario, roles.AGENTE)

    if renovar:
        Token.objects.filter(user=usuario).delete()
    token, _ = Token.objects.get_or_create(user=usuario)

    if guardar_archivo:
        ruta = settings.ZENTRY['TOKEN_AGENTE_ARCHIVO']
        ruta.write_text(token.key + '\n', encoding='utf-8')
        try:
            os.chmod(ruta, 0o600)
        except OSError:
            pass
    return token.key
