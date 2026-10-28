from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.models import User
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import roles, servicios
from .forms import CrearCuentaForm, IniciarSesionForm


def iniciar_sesion(request):
    if roles.es_usuario_web(request.user):
        return redirect('alertas:dashboard')

    form = IniciarSesionForm(request, data=request.POST or None)
    if request.method == 'POST' and form.is_valid():
        login(request, form.usuario_autenticado)
        destino = request.POST.get('next') or request.GET.get('next')
        if not destino or not url_has_allowed_host_and_scheme(destino, {request.get_host()}, request.is_secure()):
            destino = settings.LOGIN_REDIRECT_URL
        return redirect(destino)

    demo = settings.DEBUG and User.objects.filter(username__in=['admin', 'seguridad']).count() == 2
    return render(request, 'cuentas/iniciar_sesion.html', {
        'form': form,
        'next': request.GET.get('next', ''),
        'mostrar_demo': demo,
    })


@require_POST
def cerrar_sesion(request):
    logout(request)
    return redirect('cuentas:iniciar_sesion')


@roles.solo_admin
def usuarios(request):
    """Lista de cuentas y formulario "Crear cuenta" (solo Administrador)."""
    form = CrearCuentaForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        datos = form.cleaned_data
        servicios.crear_usuario(datos['usuario'], datos['contrasena'], datos['rol'])
        messages.success(request, f'Cuenta "{datos["usuario"]}" creada')
        return redirect('cuentas:usuarios')

    lista = []
    for usuario in User.objects.order_by('username').prefetch_related('groups'):
        lista.append({'obj': usuario, 'rol': roles.rol_de(usuario) or 'Sin rol'})
    return render(request, 'cuentas/usuarios.html', {
        'seccion': 'usuarios',
        'titulo': 'Usuarios',
        'form': form,
        'usuarios': lista,
        'abrir_formulario': request.method == 'POST',
    })


@roles.solo_admin
@require_POST
def cambiar_estado_usuario(request, pk):
    usuario = get_object_or_404(User, pk=pk)
    if usuario == request.user:
        messages.error(request, 'No puedes desactivar tu propia cuenta')
    elif usuario.username == servicios.USUARIO_AGENTE:
        messages.error(request, 'La cuenta del agente se controla con su token')
    else:
        usuario.is_active = not usuario.is_active
        usuario.save(update_fields=['is_active'])
        messages.success(request, f'Cuenta "{usuario.username}" {"activada" if usuario.is_active else "desactivada"}')
    return redirect('cuentas:usuarios')


@roles.solo_admin
@require_POST
def renovar_token_agente(request):
    clave = servicios.token_del_agente(renovar=True)
    messages.success(
        request,
        f'Token del agente renovado. Nuevo token: {clave} (se guardó también en .agente_token)',
    )
    return redirect('cuentas:usuarios')
