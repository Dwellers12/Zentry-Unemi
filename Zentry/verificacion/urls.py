from django.urls import path

from . import api

app_name = 'verificacion'

urlpatterns = [
    path('api/agente/configuracion/', api.Configuracion.as_view(), name='api_configuracion'),
    path('api/agente/camaras/', api.Camaras.as_view(), name='api_camaras'),
    path('api/agente/capturas/', api.Capturas.as_view(), name='api_capturas'),
]
