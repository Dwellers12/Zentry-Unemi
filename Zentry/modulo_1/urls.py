from django.urls import path

from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('api/verificar/', views.verificar_estado, name='verificar_estado'),
    path('api/plazas/', views.gestionar_plazas, name='gestionar_plazas'),
    path('api/plazas/detectar/', views.detectar_plazas, name='detectar_plazas'),
    path('api/imagenes/', views.listar_imagenes, name='listar_imagenes'),
]
