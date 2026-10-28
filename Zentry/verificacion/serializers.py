"""Validación de lo que envía el agente (nunca se confía en sus datos sin revisar)."""
import json

from rest_framework import serializers

from registro.models import Camara


class CajaField(serializers.ListField):
    """Caja relativa [x1, y1, x2, y2] con valores entre 0 y 1."""

    child = serializers.FloatField(min_value=0, max_value=1)

    def __init__(self, **kwargs):
        kwargs.setdefault('min_length', 4)
        kwargs.setdefault('max_length', 4)
        kwargs.setdefault('required', False)
        kwargs.setdefault('allow_null', True)
        super().__init__(**kwargs)


class DeteccionSerializer(serializers.Serializer):
    plaza = serializers.CharField(max_length=24)
    ocupada = serializers.BooleanField()
    placa = serializers.CharField(max_length=20, required=False, allow_blank=True, default='')
    confianza = serializers.IntegerField(min_value=0, max_value=100, required=False, default=0)
    vehiculo_caja = CajaField()
    placa_caja = CajaField()


class JSONTextoField(serializers.Field):
    """En multipart los datos estructurados llegan como texto JSON."""

    def to_internal_value(self, data):
        if isinstance(data, (list, dict)):
            return data
        try:
            return json.loads(data)
        except (TypeError, ValueError):
            raise serializers.ValidationError('JSON no válido.')


class CapturaSerializer(serializers.Serializer):
    camara = serializers.SlugField(max_length=30)
    captura = serializers.UUIDField(required=False, allow_null=True)
    capturada_en = serializers.DateTimeField(required=False)
    detecciones = JSONTextoField()
    imagen = serializers.FileField(required=False, allow_empty_file=False)

    def validate_detecciones(self, valor):
        if not isinstance(valor, list) or len(valor) > 200:
            raise serializers.ValidationError('Se espera una lista de detecciones.')
        detecciones = DeteccionSerializer(data=valor, many=True)
        detecciones.is_valid(raise_exception=True)
        return detecciones.validated_data

    def validate_imagen(self, archivo):
        if archivo.size > 8 * 1024 * 1024:
            raise serializers.ValidationError('La imagen no puede pasar de 8 MB.')
        return archivo

    def validate_camara(self, codigo):
        camara = Camara.objects.filter(codigo=codigo, activa=True).first()
        if camara is None:
            raise serializers.ValidationError('Cámara desconocida o desactivada. Regístrala primero.')
        return camara


class PlazaSugeridaSerializer(serializers.Serializer):
    nombre = serializers.CharField(max_length=24)
    poligono = serializers.ListField(
        child=serializers.ListField(child=serializers.FloatField(min_value=0, max_value=1), min_length=2, max_length=2),
        min_length=3, max_length=40,
    )


class CamaraAgenteSerializer(serializers.Serializer):
    codigo = serializers.SlugField(max_length=30)
    nombre = serializers.CharField(max_length=60, required=False, allow_blank=True, default='')
    tipo = serializers.ChoiceField(choices=[t[0] for t in Camara.TIPOS], default=Camara.IP_WEBCAM)
    url = serializers.CharField(max_length=300, required=False, allow_blank=True, default='')
    ancho = serializers.IntegerField(min_value=16, max_value=10000, required=False)
    alto = serializers.IntegerField(min_value=16, max_value=10000, required=False)
    plazas_sugeridas = PlazaSugeridaSerializer(many=True, required=False)
    sincronizar_plazas = serializers.BooleanField(default=False)
