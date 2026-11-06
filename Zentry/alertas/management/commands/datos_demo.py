"""
Carga datos de demostración coherentes con las dos imágenes de ejemplo (4K) del parqueadero.

    python manage.py datos_demo              # solo si la base está vacía
    python manage.py datos_demo --reiniciar  # borra residentes, cámaras, alertas... y vuelve a cargar
    python manage.py datos_demo --si-vacia   # como el primero, pero sin error si ya hay datos

Crea:
  - Usuarios admin / Zentry2026 (Administrador) y seguridad / Zentry2026 (Personal de seguridad).
  - La cuenta técnica del agente y su token (archivo .agente_token).
  - CAM_01 y CAM_02 con las plazas calibradas de plazas_demo.json, junto a este archivo (P1–P12).
    Las fotos 4K de ejemplos/ no están en el repositorio: sin ellas las cámaras se ven sin imagen
    hasta que se conecta el simulador (camaras_virtuales.py).
  - Seis residentes con las placas que de verdad se ven en las imágenes.
  - El estado actual de cada plaza (P11 tiene un vehículo que no le corresponde -> alerta).
  - Un historial de alertas de la última semana (sin imagen: es historial de ejemplo).
"""
import io
import json
import shutil
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from alertas import servicios as alertas_servicios
from alertas.models import Alerta, EventoAlerta
from cuentas import roles
from cuentas.servicios import crear_usuario, token_del_agente
from registro.models import Camara, Plaza, Residente, Vehiculo
from verificacion.models import EstadoPlaza, Lectura, Resultado
from verificacion.servicios import Padron

CONTRASENA_DEMO = 'Zentry2026'

# Polígonos de las plazas P1–P12 sobre las fotos 4K de ejemplo (en píxeles de 3840 × 2160).
CALIBRACION = Path(__file__).with_name('plazas_demo.json')

CAMARAS = [
    ('CAM_01', 'Cámara 1 · Fila A', 'cam_01_3840x2160_2026-09-28_14-45-33.png'),
    ('CAM_02', 'Cámara 2 · Fila B', 'cam_02_3840x2160_2026-09-28_14-45-33.png'),
]

RESIDENTES = [
    ('María Torres', '0923456781', 'A-101', '0987654321', 'maria.torres@correo.com'),
    ('Carlos Mendoza', '0914785236', 'A-102', '0991234567', 'carlos.mendoza@correo.com'),
    ('Lucía Cedeño', '0931597534', 'B-201', '0978112233', 'lucia.cedeno@correo.com'),
    ('Jorge Paredes', '0927418529', 'B-203', '0969445566', 'jorge.paredes@correo.com'),
    ('Ana Ríos', '0942583691', 'C-301', '0983778899', 'ana.rios@correo.com'),
    ('Diego Salazar', '0918273645', 'C-302', '0995667788', 'diego.salazar@correo.com'),
]

# (cédula del residente, placa, marca y modelo, color, plaza asignada)
VEHICULOS = [
    ('0923456781', 'GBP-3579', 'Chevrolet D-Max', 'Azul', 'P3'),
    ('0914785236', 'KNZ-6203', 'Chevrolet Aveo', 'Azul', 'P1'),
    ('0931597534', 'GSD-7710', 'Toyota Yaris', 'Rojo', 'P11'),
    ('0927418529', 'QNO-7477', 'Hyundai Tucson', 'Azul', 'P6'),
    ('0942583691', 'UPJ-0372', 'Suzuki Swift', 'Amarillo', 'P8'),
    ('0918273645', 'ZQJ-6606', 'Kia Carnival', 'Azul', 'P4'),
]

# Lo que se ve en las imágenes de ejemplo: plaza -> (placa, confianza, minutos que lleva ahí).
OCUPACION = {
    'P1': ('KNZ-6203', 96, 190),
    'P4': ('ZQJ-6606', 94, 75),
    'P6': ('QNO-7477', 92, 260),
    'P8': ('UPJ-0372', 95, 140),
    'P11': ('GBP-3579', 91, 35),  # el D-Max de María está en la plaza de Lucía
}

# Historial (días atrás, hora, plaza, placa, resultado, minutos hasta resolverse, cómo se resolvió).
HISTORIAL = [
    (1, '13:10', 'P1', 'PBA-7721', Alerta.NO_REGISTRADA, 25, 'manual', 'Visitante autorizado por administración'),
    (1, '11:58', 'P6', 'GSD-7710', Alerta.DISCREPANCIA, 5, 'salida', ''),
    (2, '19:24', 'P8', 'MBC-4410', Alerta.NO_REGISTRADA, 40, 'revisada', 'Se notificó al residente y movió el vehículo'),
    (2, '17:05', 'P4', 'UPJ-0372', Alerta.DISCREPANCIA, 12, 'salida', ''),
    (3, '08:40', 'P2', '', Alerta.NO_CONCLUYENTE, 22, 'corregida', 'Visitante del A-102; se registró su salida en portería'),
    (4, '22:15', 'P9', 'GZK-1180', Alerta.NO_REGISTRADA, 18, 'salida', ''),
    (5, '07:30', 'P12', 'QNO-7477', Alerta.DISCREPANCIA, 9, 'salida', ''),
    (6, '15:45', 'P5', 'ABZ-5521', Alerta.NO_REGISTRADA, 30, 'manual', 'Error de lectura de la placa'),
    (6, '09:10', 'P3', 'KNZ-6203', Alerta.DISCREPANCIA, 8, 'salida', ''),
]


def caja_vehiculo(plaza):
    """Caja aproximada del vehículo: la cámara ve el carro "de pie" sobre su plaza."""
    x1, y1, x2, y2 = plaza.caja()
    alto = y2 - y1
    return [round(x1 + 0.01, 4), round(max(0, y1 - alto * 1.2), 4), round(x2 - 0.01, 4), round(y2 - alto * 0.05, 4)]


class Command(BaseCommand):
    help = 'Carga datos de demostración (usuarios, cámaras, plazas, residentes, alertas).'

    def add_arguments(self, parser):
        parser.add_argument('--reiniciar', action='store_true',
                            help='Borra los datos del parqueadero antes de cargar la demostración.')
        parser.add_argument('--si-vacia', action='store_true',
                            help='Si ya hay datos no hace nada (lo usa iniciar_zentry.bat).')

    def handle(self, *args, reiniciar=False, si_vacia=False, **options):
        hay_datos = Residente.objects.exists() or Camara.objects.exists() or Alerta.objects.exists()
        if hay_datos and not reiniciar:
            if si_vacia:
                return
            raise CommandError('Ya hay datos en la base. Usa --reiniciar para borrarlos y cargar la demostración.')
        if reiniciar:
            self._borrar()
        with transaction.atomic():
            self._usuarios()
            plazas = self._camaras()
            self._registro()
            self._estado_actual(plazas)
            self._historial(plazas)
        self.stdout.write(self.style.SUCCESS(
            'Datos de demostración listos. Entra con admin / Zentry2026 o seguridad / Zentry2026.'))

    # ------------------------------------------------------------------ pasos
    def _borrar(self):
        Alerta.objects.all().delete()
        Lectura.objects.all().delete()
        EstadoPlaza.objects.all().delete()
        Vehiculo.objects.all().delete()
        Residente.objects.all().delete()
        Plaza.objects.all().delete()
        Camara.objects.all().delete()
        for carpeta in ('evidencias', 'camaras'):
            shutil.rmtree(settings.MEDIA_ROOT / carpeta, ignore_errors=True)
        self.stdout.write('Datos anteriores borrados.')

    def _usuarios(self):
        for nombre, rol, nombres in (('admin', roles.ADMINISTRADOR, ('Admin', 'Zentry')),
                                     ('seguridad', roles.SEGURIDAD, ('Personal', 'de seguridad'))):
            usuario = User.objects.filter(username=nombre).first()
            if usuario is None:
                crear_usuario(nombre, CONTRASENA_DEMO, rol, first_name=nombres[0], last_name=nombres[1])
            else:
                usuario.set_password(CONTRASENA_DEMO)
                usuario.first_name, usuario.last_name = nombres
                usuario.is_active = True
                usuario.save()
        token_del_agente()
        self.stdout.write('Usuarios admin y seguridad, y token del agente en .agente_token.')

    def _camaras(self):
        try:
            calibracion = json.loads(CALIBRACION.read_text(encoding='utf-8'))['camaras']
        except (OSError, KeyError, ValueError):
            raise CommandError(f'No se encontró {CALIBRACION.name} con la calibración de las cámaras de ejemplo.')
        plazas = {}
        for orden, (codigo, nombre, archivo) in enumerate(CAMARAS, start=1):
            origen = settings.RAIZ_PROYECTO / 'ejemplos' / archivo
            camara = Camara.objects.create(
                codigo=codigo, nombre=nombre, tipo=Camara.IMAGEN, orden=orden, ancho=1920, alto=1080,
                url=str(origen.relative_to(settings.RAIZ_PROYECTO)),
                ultima_captura=timezone.now() - timedelta(minutes=2),
            )
            for item in calibracion.get(archivo, []):
                poligono = [[round(x / 3840, 4), round(y / 2160, 4)] for x, y in item['poligono']]
                plaza = Plaza.objects.create(nombre=item['id'], camara=camara, poligono=poligono)
                plazas[plaza.nombre] = plaza
            self._imagen(camara, origen)
        self.stdout.write(f'{len(CAMARAS)} cámaras y {len(plazas)} plazas.')
        return plazas

    def _imagen(self, camara, origen):
        """Última imagen de la cámara (1920 px) a partir de la foto 4K de ejemplo."""
        from PIL import Image

        if not origen.exists():
            self.stdout.write(self.style.WARNING(
                f'No está la imagen {origen.name}: la cámara se verá sin imagen hasta que se conecte el simulador.'))
            return
        with Image.open(origen) as imagen:
            imagen = imagen.convert('RGB')
            imagen.thumbnail((1920, 1080))
            salida = io.BytesIO()
            imagen.save(salida, 'JPEG', quality=85)
        ruta = camara.ruta_imagen()
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_bytes(salida.getvalue())

    def _registro(self):
        residentes = {}
        for nombre, cedula, depto, telefono, correo in RESIDENTES:
            residentes[cedula] = Residente.objects.create(
                nombre=nombre, cedula=cedula, departamento=depto, telefono=telefono, correo=correo)
        for cedula, placa, modelo, color, plaza in VEHICULOS:
            Vehiculo.objects.create(placa=placa, marca_modelo=modelo, color=color, residente=residentes[cedula],
                                    plaza=Plaza.objects.get(nombre=plaza))
        self.stdout.write(f'{len(RESIDENTES)} residentes y {len(VEHICULOS)} vehículos.')

    def _estado_actual(self, plazas):
        ahora = timezone.now()
        padron = Padron()
        for nombre, plaza in plazas.items():
            if nombre not in OCUPACION:
                EstadoPlaza.objects.create(plaza=plaza, visto=ahora)
                continue
            placa, confianza, minutos = OCUPACION[nombre]
            llegada = ahora - timedelta(minutes=minutos)
            estado = EstadoPlaza.objects.create(
                plaza=plaza, ocupada=True, placa=placa, confianza=confianza,
                resultado=padron.clasificar(plaza, True, placa), vehiculo_caja=caja_vehiculo(plaza),
                actualizado=llegada, visto=ahora,
            )
            lectura = Lectura.objects.create(
                camara=plaza.camara, plaza=plaza, plaza_nombre=plaza.nombre, tipo=Lectura.LLEGADA, placa=placa,
                confianza=confianza, resultado=estado.resultado, capturada_en=llegada,
            )
            if estado.resultado != Resultado.COINCIDENCIA:
                # Se crea con el mismo servicio que usa el sistema real (evidencia incluida).
                imagen = plaza.camara.ruta_imagen().read_bytes() if plaza.camara.ruta_imagen().exists() else None
                alertas_servicios.sincronizar_plaza(plaza, estado, lectura=lectura, imagen=imagen)
        self.stdout.write('Estado actual de las plazas (1 alerta activa en P11).')

    def _historial(self, plazas):
        admin = User.objects.get(username='admin')
        seguridad = User.objects.get(username='seguridad')
        hoy = timezone.localtime()
        for dias, hora, nombre, placa, resultado, minutos, forma, texto in HISTORIAL:
            hh, mm = map(int, hora.split(':'))
            creada = (hoy - timedelta(days=dias)).replace(hour=hh, minute=mm, second=0, microsecond=0)
            resuelta = creada + timedelta(minutes=minutos)
            plaza = plazas.get(nombre)
            confianza = 0 if resultado == Alerta.NO_CONCLUYENTE else 88
            alerta = Alerta.objects.create(
                plaza=plaza, plaza_nombre=nombre, camara=plaza.camara if plaza else None,
                camara_nombre=plaza.camara.nombre if plaza and plaza.camara else '', placa=placa,
                resultado=resultado, estado=Alerta.RESUELTA, confianza=confianza, creada_en=creada,
                resuelta_en=resuelta, vehiculo_caja=caja_vehiculo(plaza) if plaza else None,
            )
            alerta.vista_por.add(admin, seguridad)
            Lectura.objects.create(camara=alerta.camara, plaza=plaza, plaza_nombre=nombre, tipo=Lectura.LLEGADA,
                                   placa=placa, confianza=confianza, resultado=resultado,
                                   capturada_en=creada)
            self._evento(alerta, EventoAlerta.CREADA,
                         f'Creada por la cámara: {placa}' if placa else 'Creada por la cámara: placa ilegible u oculta', None, creada)
            if forma == 'salida':
                texto = (f'Resuelta automáticamente: el vehículo {placa} salió de la plaza {nombre} '
                         f'a las {timezone.localtime(resuelta):%H:%M}.')
                alerta.resolucion, alerta.resolucion_autor, alerta.resolucion_automatica = texto, EventoAlerta.SISTEMA, True
                self._evento(alerta, EventoAlerta.AUTOMATICA, texto, None, resuelta)
                Lectura.objects.create(camara=alerta.camara, plaza=plaza, plaza_nombre=nombre, tipo=Lectura.SALIDA,
                                       placa_anterior=placa, resultado=Resultado.LIBRE, capturada_en=resuelta)
            else:
                if forma == 'revisada':
                    self._evento(alerta, EventoAlerta.REVISADA, 'Alerta revisada: se llamó al residente del departamento',
                                 seguridad, creada + timedelta(minutes=minutos // 3))
                if forma == 'corregida':
                    alerta.placa, alerta.resultado, alerta.confianza = 'PCB-1180', Alerta.NO_REGISTRADA, 100
                    self._evento(alerta, EventoAlerta.CORREGIDA, 'Lectura corregida: ilegible → PCB-1180', admin,
                                 creada + timedelta(minutes=6))
                alerta.resolucion, alerta.resolucion_autor, alerta.resolucion_automatica = texto, 'Admin Zentry', False
                self._evento(alerta, EventoAlerta.RESUELTA, texto, admin, resuelta)
            alerta.save()
        self.stdout.write(f'{len(HISTORIAL)} alertas de historial.')

    @staticmethod
    def _evento(alerta, tipo, texto, usuario, en):
        EventoAlerta.objects.create(
            alerta=alerta, tipo=tipo, texto=texto, usuario=usuario, creado_en=en,
            autor=(usuario.get_full_name() or usuario.username) if usuario else EventoAlerta.SISTEMA,
        )
