from django.urls import path

from . import views

app_name = 'registro'

urlpatterns = [
    path('residentes/', views.residentes, name='residentes'),
    path('residentes/<int:pk>/editar/', views.residente_editar, name='residente_editar'),
    path('residentes/<int:pk>/eliminar/', views.residente_eliminar, name='residente_eliminar'),
    path('vehiculos/', views.vehiculos, name='vehiculos'),
    path('vehiculos/<int:pk>/editar/', views.vehiculo_editar, name='vehiculo_editar'),
    path('vehiculos/<int:pk>/eliminar/', views.vehiculo_eliminar, name='vehiculo_eliminar'),
    path('camaras/<slug:codigo>/imagen.jpg', views.imagen_camara, name='imagen_camara'),
    path('api/camaras/<slug:codigo>/plazas/', views.api_plazas_camara, name='api_plazas_camara'),
]
