"""
M2 · Reconocimiento y Verificación de Placas (RF-05, RF-06).

El agente envía capturas; el servidor guarda aquí:
  - EstadoPlaza: lo que se ve ahora en cada plaza y el resultado de verificarlo.
  - Lectura: cada cambio confirmado (llegada, salida, cambio de placa o corrección).
"""
from django.db import models

from registro.models import Camara, Plaza


class Resultado(models.TextChoices):
    LIBRE = 'libre', 'Libre'
    COINCIDENCIA = 'coincidencia', 'Coincidencia'
    DISCREPANCIA = 'discrepancia', 'Discrepancia'
    NO_REGISTRADA = 'no_registrada', 'Placa no registrada'
    NO_CONCLUYENTE = 'no_concluyente', 'No concluyente'


# Resultados que significan "hay un vehículo que no corresponde" (generan alerta).
RESULTADOS_IRREGULARES = (Resultado.DISCREPANCIA, Resultado.NO_REGISTRADA, Resultado.NO_CONCLUYENTE)

# Estado que se muestra en la web (leyenda del diseño + "No concluyente").
ESTADO_VISUAL = {
    Resultado.LIBRE: 'Libre',
    Resultado.COINCIDENCIA: 'Ocupada',
    Resultado.DISCREPANCIA: 'No autorizada',
    Resultado.NO_REGISTRADA: 'No autorizada',
    Resultado.NO_CONCLUYENTE: 'No concluyente',
}


class EstadoPlaza(models.Model):
    plaza = models.OneToOneField(Plaza, on_delete=models.CASCADE, primary_key=True, related_name='estado')
    ocupada = models.BooleanField(default=False)
    placa = models.CharField(max_length=12, blank=True)
    confianza = models.PositiveSmallIntegerField(default=0, help_text='Confianza del OCR en %')
    resultado = models.CharField(max_length=20, choices=Resultado.choices, default=Resultado.LIBRE)
    # Cajas relativas (0 a 1) del vehículo y de su placa en la última imagen.
    vehiculo_caja = models.JSONField(null=True, blank=True)
    placa_caja = models.JSONField(null=True, blank=True)
    actualizado = models.DateTimeField(null=True, blank=True, help_text='Último cambio confirmado')
    visto = models.DateTimeField(null=True, blank=True, help_text='Última captura que incluyó la plaza')
    # Observación distinta del estado actual que todavía no se confirma.
    candidato = models.JSONField(null=True, blank=True)
    candidato_veces = models.PositiveSmallIntegerField(default=0)
    # Corrección manual de la lectura mientras el vehículo siga en la plaza:
    # {'leida': 'KNZ-62O3', 'corregida': 'KNZ-6203', 'usuario': 'seguridad'}.
    correccion = models.JSONField(null=True, blank=True)

    class Meta:
        verbose_name = 'estado de plaza'
        verbose_name_plural = 'estados de plazas'

    def __str__(self):
        return f'{self.plaza} · {self.estado_visual}'

    @property
    def estado_visual(self):
        return ESTADO_VISUAL.get(self.resultado, 'Libre')


class Lectura(models.Model):
    LLEGADA = 'llegada'
    SALIDA = 'salida'
    CAMBIO = 'cambio'
    CORRECCION = 'correccion'
    TIPOS = [
        (LLEGADA, 'Llegada'),
        (SALIDA, 'Salida'),
        (CAMBIO, 'Cambio de placa'),
        (CORRECCION, 'Lectura corregida'),
    ]

    captura = models.UUIDField(null=True, blank=True, help_text='Id único de la captura del agente')
    camara = models.ForeignKey(Camara, on_delete=models.SET_NULL, null=True, blank=True, related_name='lecturas')
    plaza = models.ForeignKey(Plaza, on_delete=models.SET_NULL, null=True, blank=True, related_name='lecturas')
    plaza_nombre = models.CharField(max_length=24)
    tipo = models.CharField(max_length=12, choices=TIPOS)
    placa = models.CharField(max_length=12, blank=True)
    placa_anterior = models.CharField(max_length=12, blank=True)
    confianza = models.PositiveSmallIntegerField(default=0)
    resultado = models.CharField(max_length=20, choices=Resultado.choices)
    capturada_en = models.DateTimeField(db_index=True)
    recibida_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-capturada_en', '-pk']
        constraints = [
            models.UniqueConstraint(fields=['captura', 'plaza_nombre'], name='lectura_unica_por_captura'),
        ]

    def __str__(self):
        return f'{self.get_tipo_display()} · {self.plaza_nombre} · {self.placa or "—"}'

    @property
    def texto(self):
        if self.tipo == self.LLEGADA:
            return f'Llegada de {self.placa or "un vehículo sin placa legible"}'
        if self.tipo == self.SALIDA:
            return f'Salida de {self.placa_anterior or "un vehículo"}'
        if self.tipo == self.CORRECCION:
            return f'Lectura corregida: {self.placa_anterior or "—"} → {self.placa}'
        return f'Cambio de placa: {self.placa_anterior or "—"} → {self.placa or "sin lectura"}'
