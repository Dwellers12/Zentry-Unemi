from django.urls import path

from . import views

app_name = 'cuentas'

urlpatterns = [
    path('iniciar-sesion/', views.iniciar_sesion, name='iniciar_sesion'),
    path('cerrar-sesion/', views.cerrar_sesion, name='cerrar_sesion'),
    path('usuarios/', views.usuarios, name='usuarios'),
    path('usuarios/<int:pk>/estado/', views.cambiar_estado_usuario, name='cambiar_estado_usuario'),
    path('usuarios/token-agente/', views.renovar_token_agente, name='renovar_token_agente'),
]
