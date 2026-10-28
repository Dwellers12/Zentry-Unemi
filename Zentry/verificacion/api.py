"""
API para el agente local (RF-05). Solo acepta el token del usuario "agente".

    GET  /api/agente/configuracion/   cámaras, fuentes y plazas que debe vigilar
    POST /api/agente/camaras/         anuncia una cámara (p. ej. las virtuales del simulador)
    POST /api/agente/capturas/        resultado del reconocimiento + imagen (multipart)
"""
from rest_framework import status
from rest_framework.authentication import TokenAuthentication
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from cuentas.roles import EsAgente
from registro.servicios import ErrorPlazas, registrar_camara

from . import servicios
from .serializers import CamaraAgenteSerializer, CapturaSerializer


class VistaAgente(APIView):
    authentication_classes = [TokenAuthentication]
    permission_classes = [EsAgente]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        servicios.marcar_agente_activo(request.user)


class Configuracion(VistaAgente):
    def get(self, request):
        return Response(servicios.configuracion_agente())


class Camaras(VistaAgente):
    parser_classes = [JSONParser]

    def post(self, request):
        datos = CamaraAgenteSerializer(data=request.data)
        datos.is_valid(raise_exception=True)
        d = datos.validated_data
        try:
            camara, creada, aplicadas = registrar_camara(
                codigo=d['codigo'], nombre=d['nombre'], tipo=d['tipo'], url=d['url'],
                ancho=d.get('ancho'), alto=d.get('alto'), plazas_sugeridas=d.get('plazas_sugeridas'),
                sincronizar_plazas=d['sincronizar_plazas'],
            )
        except ErrorPlazas as error:
            return Response({'detail': str(error)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {
                'codigo': camara.codigo,
                'creada': creada,
                'plazas_aplicadas': aplicadas,
                'activa': camara.activa,
                'version': servicios.configuracion_agente()['version'],
            },
            status=status.HTTP_201_CREATED if creada else status.HTTP_200_OK,
        )


class Capturas(VistaAgente):
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request):
        datos = CapturaSerializer(data=request.data)
        datos.is_valid(raise_exception=True)
        d = datos.validated_data
        imagen = d['imagen'].read() if d.get('imagen') else None
        resultado = servicios.procesar_captura(
            d['camara'], d['detecciones'], capturada_en=d.get('capturada_en'),
            captura_id=d.get('captura'), imagen=imagen,
        )
        resultado['version'] = servicios.configuracion_agente()['version']
        return Response(resultado, status=status.HTTP_201_CREATED if resultado['aceptada'] else status.HTTP_200_OK)
