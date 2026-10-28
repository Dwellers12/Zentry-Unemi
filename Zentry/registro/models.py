"""
M1 · Gestión de Residentes, Vehículos y Plazas (RF-01 a RF-04).

Relaciones:
    Residente 1 ── * Vehiculo * ── 0..1 Plaza * ── 0..1 Camara

Una plaza pertenece al residente dueño de los vehículos autorizados en ella.
Un residente puede tener varios vehículos autorizados en la misma plaza (HU-02).
"""
import re

from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

validar_cedula = RegexValidator(r'^\d{10}$', 'La cédula debe tener 10 dígitos')
validar_telefono = RegexValidator(r'^09\d{8}$', 'Ingresa 10 dígitos que empiecen por 09')
# Placa ecuatoriana: 3 letras, guion y 4 dígitos (las antiguas tienen 3 dígitos).
PATRON_PLACA = r'^[A-Z]{3}-\d{3,4}$'
validar_placa = RegexValidator(PATRON_PLACA, 'Formato requerido: ABC-1234')


def numero_de(nombre):
    """Número de una plaza 'P12' -> 12 (para ordenar P2 antes que P10)."""
    m = re.search(r'\d+', nombre or '')
    return int(m.group()) if m else 0


class Residente(models.Model):
    nombre = models.CharField('nombre completo', max_length=120)
    cedula = models.CharField('cédula', max_length=10, unique=True, validators=[validar_cedula])
    departamento = models.CharField('departamento o casa', max_length=30)
    telefono = models.CharField('teléfono', max_length=10, validators=[validar_telefono])
    correo = models.EmailField('correo', blank=True)
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['nombre']
        verbose_name = 'residente'

    def __str__(self):
        return self.nombre


class Camara(models.Model):
    """Cámara fija que vigila una fila de plazas."""

    IP_WEBCAM = 'ip_webcam'
    VIRTUAL = 'virtual'
    IMAGEN = 'imagen'
    TIPOS = [
        (IP_WEBCAM, 'Cámara IP (IP Webcam)'),
        (VIRTUAL, 'Cámara virtual del simulador'),
        (IMAGEN, 'Imagen de ejemplo'),
    ]

    ORIGEN_MANUAL = 'manual'
    ORIGEN_SIMULADOR = 'simulador'
    ORIGEN_DETECCION = 'deteccion'
    ORIGENES = [
        (ORIGEN_MANUAL, 'Editadas a mano'),
        (ORIGEN_SIMULADOR, 'Sincronizadas con el simulador'),
        (ORIGEN_DETECCION, 'Detectadas automáticamente'),
    ]

    codigo = models.SlugField('código', max_length=30, unique=True,
                              help_text='Identificador que usa el agente, por ejemplo CAM_01.')
    nombre = models.CharField(max_length=60)
    tipo = models.CharField(max_length=20, choices=TIPOS, default=IP_WEBCAM)
    url = models.CharField('fuente', max_length=300, blank=True,
                           help_text='URL de IP Webcam (http://IP:8080) o ruta de la imagen de ejemplo.')
    activa = models.BooleanField(default=True)
    orden = models.PositiveSmallIntegerField(default=0)
    ancho = models.PositiveIntegerField(default=1920)
    alto = models.PositiveIntegerField(default=1080)

    plazas_origen = models.CharField(max_length=20, choices=ORIGENES, default=ORIGEN_MANUAL)
    # Plazas que propone el agente (detección por líneas o verdad de terreno del simulador).
    plazas_sugeridas = models.JSONField(default=list, blank=True)
    sugeridas_en = models.DateTimeField(null=True, blank=True)

    ultima_captura = models.DateTimeField(null=True, blank=True)
    ultima_conexion = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['orden', 'codigo']
        verbose_name = 'cámara'
        verbose_name_plural = 'cámaras'

    def __str__(self):
        return self.nombre

    @property
    def en_linea(self):
        limite = settings.ZENTRY['AGENTE_SIN_SENAL_S']
        return bool(self.ultima_captura and (timezone.now() - self.ultima_captura).total_seconds() < limite)

    @property
    def relacion(self):
        return self.alto / self.ancho if self.ancho else 9 / 16

    def ruta_imagen(self):
        """Última imagen recibida del agente (se sobrescribe en cada captura)."""
        return settings.MEDIA_ROOT / 'camaras' / f'{self.codigo}.jpg'


class Plaza(models.Model):
    nombre = models.CharField(max_length=24, unique=True)
    camara = models.ForeignKey(Camara, on_delete=models.SET_NULL, null=True, blank=True, related_name='plazas')
    # Polígono sobre la imagen de la cámara, en coordenadas relativas (0 a 1): [[x, y], ...].
    poligono = models.JSONField(default=list, blank=True)
    orden = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['orden', 'nombre']

    def __str__(self):
        return self.nombre

    def save(self, *args, **kwargs):
        self.nombre = self.nombre.strip().upper()
        self.orden = numero_de(self.nombre)
        super().save(*args, **kwargs)

    def residente_asignado(self):
        vehiculo = self.vehiculos.select_related('residente').first()
        return vehiculo.residente if vehiculo else None

    def caja(self):
        """Caja [x1, y1, x2, y2] del polígono (relativa), o None."""
        if not self.poligono:
            return None
        xs = [p[0] for p in self.poligono]
        ys = [p[1] for p in self.poligono]
        return [min(xs), min(ys), max(xs), max(ys)]


class Vehiculo(models.Model):
    placa = models.CharField(max_length=8, unique=True, validators=[validar_placa])
    marca_modelo = models.CharField('marca y modelo', max_length=60)
    color = models.CharField(max_length=30)
    residente = models.ForeignKey(Residente, on_delete=models.CASCADE, related_name='vehiculos')
    plaza = models.ForeignKey(Plaza, on_delete=models.SET_NULL, null=True, blank=True,
                              related_name='vehiculos', verbose_name='plaza asignada')
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['placa']
        verbose_name = 'vehículo'

    def __str__(self):
        return self.placa

    def save(self, *args, **kwargs):
        self.placa = self.placa.strip().upper()
        super().save(*args, **kwargs)
