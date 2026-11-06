"""
M3 · Gestión de Alertas (RF-07 a RF-12).

Una alerta nace cuando una plaza pasa a un resultado irregular (discrepancia,
placa no registrada o no concluyente) y se resuelve sola cuando el vehículo se
va, se corrige la lectura o el registro la vuelve válida. El personal puede
revisarla, resolverla con una descripción o reabrirla; todo queda en su historial.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone

from registro.models import Camara, Plaza
from verificacion.models import Lectura, Resultado


class Alerta(models.Model):
    PENDIENTE = 'pendiente'
    REVISADA = 'revisada'
    RESUELTA = 'resuelta'
    ESTADOS = [(PENDIENTE, 'Pendiente'), (REVISADA, 'Revisada'), (RESUELTA, 'Resuelta')]
    ACTIVAS = (PENDIENTE, REVISADA)

    DISCREPANCIA = 'discrepancia'
    NO_REGISTRADA = 'no_registrada'
    NO_CONCLUYENTE = 'no_concluyente'
    CORREGIDA = 'corregida'
    # Solo estas avisan al guardia; las «No concluyente» (placa ilegible) se revisan en la web.
    RESULTADOS_CON_AVISO = (DISCREPANCIA, NO_REGISTRADA)
    RESULTADOS = [
        (DISCREPANCIA, 'Discrepancia'),
        (NO_REGISTRADA, 'Placa no registrada'),
        (NO_CONCLUYENTE, 'No concluyente'),
        (CORREGIDA, 'Lectura corregida'),
    ]

    plaza = models.ForeignKey(Plaza, on_delete=models.SET_NULL, null=True, blank=True, related_name='alertas')
    plaza_nombre = models.CharField(max_length=24)
    camara = models.ForeignKey(Camara, on_delete=models.SET_NULL, null=True, blank=True, related_name='alertas')
    camara_nombre = models.CharField(max_length=60, blank=True)
    placa = models.CharField(max_length=12, blank=True)
    resultado = models.CharField(max_length=20, choices=RESULTADOS)
    estado = models.CharField(max_length=10, choices=ESTADOS, default=PENDIENTE, db_index=True)
    confianza = models.PositiveSmallIntegerField(default=0)
    creada_en = models.DateTimeField(default=timezone.now, db_index=True)
    resuelta_en = models.DateTimeField(null=True, blank=True)

    resolucion = models.TextField(blank=True)
    resolucion_autor = models.CharField(max_length=150, blank=True)
    resolucion_automatica = models.BooleanField(default=False)

    # Evidencia (RF-10): claves del almacén de evidencias (local o S3).
    evidencia = models.CharField(max_length=255, blank=True)
    evidencia_recorte = models.CharField(max_length=255, blank=True)
    vehiculo_caja = models.JSONField(null=True, blank=True)

    lectura = models.ForeignKey(Lectura, on_delete=models.SET_NULL, null=True, blank=True, related_name='alertas')
    vista_por = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name='alertas_vistas')

    class Meta:
        ordering = ['-creada_en', '-pk']

    def __str__(self):
        return f'{self.plaza_nombre} · {self.placa or "sin placa"} · {self.get_estado_display()}'

    @property
    def activa(self):
        return self.estado in self.ACTIVAS

    @property
    def atrasada(self):
        """Pendiente por más de ALERTA_ATRASADA_MIN minutos (se resalta y va primero)."""
        limite = settings.ZENTRY['ALERTA_ATRASADA_MIN'] * 60
        return self.estado == self.PENDIENTE and (timezone.now() - self.creada_en).total_seconds() > limite

    @property
    def placa_visible(self):
        return self.placa or 'Ilegible'

    @staticmethod
    def resultado_de(resultado_verificacion):
        return {
            Resultado.DISCREPANCIA: Alerta.DISCREPANCIA,
            Resultado.NO_REGISTRADA: Alerta.NO_REGISTRADA,
            Resultado.NO_CONCLUYENTE: Alerta.NO_CONCLUYENTE,
        }.get(resultado_verificacion)


class EventoAlerta(models.Model):
    CREADA = 'creada'
    REVISADA = 'revisada'
    RESUELTA = 'resuelta'
    AUTOMATICA = 'automatica'
    REABIERTA = 'reabierta'
    EDITADA = 'editada'
    CORREGIDA = 'corregida'
    TIPOS = [
        (CREADA, 'Creada'),
        (REVISADA, 'Revisada'),
        (RESUELTA, 'Resuelta'),
        (AUTOMATICA, 'Resuelta automáticamente'),
        (REABIERTA, 'Reabierta'),
        (EDITADA, 'Descripción editada'),
        (CORREGIDA, 'Lectura corregida'),
    ]
    SISTEMA = 'Sistema Zentry'

    alerta = models.ForeignKey(Alerta, on_delete=models.CASCADE, related_name='eventos')
    tipo = models.CharField(max_length=12, choices=TIPOS)
    texto = models.TextField()
    autor = models.CharField(max_length=150, default=SISTEMA)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    creado_en = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['creado_en', 'pk']
        verbose_name = 'evento de alerta'
        verbose_name_plural = 'eventos de alertas'

    def __str__(self):
        return f'{self.get_tipo_display()} · {self.alerta_id}'
