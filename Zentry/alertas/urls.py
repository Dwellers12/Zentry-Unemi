from django.urls import path

from . import views

app_name = 'alertas'

urlpatterns = [
    path('', views.inicio, name='inicio'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('api/estado/', views.api_estado, name='api_estado'),
    path('alertas/', views.lista, name='lista'),
    path('alertas/marcar-vistas/', views.marcar_vistas, name='marcar_vistas'),
    path('alertas/<int:pk>/', views.detalle, name='detalle'),
    path('alertas/<int:pk>/estado/', views.cambiar_estado, name='cambiar_estado'),
    path('alertas/<int:pk>/corregir/', views.corregir, name='corregir'),
    path('alertas/<int:pk>/resolucion/', views.editar_resolucion, name='editar_resolucion'),
    path('alertas/<int:pk>/evidencia/<str:tipo>.jpg', views.evidencia, name='evidencia'),
    path('reportes/', views.reportes, name='reportes'),
    path('reportes/alertas.csv', views.reportes_csv, name='reportes_csv'),
]
