"""
Reglas de negocio de M2 (verificacion).

Flujo de una captura del agente:
    1. Se descartan capturas repetidas o más viejas que la última aceptada.
    2. Se guarda la imagen como "última imagen" de la cámara (muro de cámaras).
    3. Por cada plaza se compara lo observado con el estado actual. Un cambio solo
       se confirma tras LECTURAS_PARA_CONFIRMAR capturas seguidas que lo ven igual
       (evita parpadeos del OCR o de la detección).
    4. Al confirmar: se clasifica (coincidencia, discrepancia, no registrada o no
       concluyente), se guarda una Lectura y se emite `estado_plaza_cambiado`.

verificacion depende de registro (padrón de vehículos y plazas) pero no de alertas.
"""
import hashlib
import json
import os
import re
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from registro.models import PATRON_PLACA, Camara, Plaza, Vehiculo
from registro.servicios import camaras_con_plazas

from .models import EstadoPlaza, Lectura, Resultado
from .senales import estado_plaza_cambiado

MAX_IMAGEN_BYTES = 8 * 1024 * 1024


class ErrorVerificacion(ValueError):
    """Operación no válida (el mensaje se muestra tal cual al usuario)."""


def _config(clave):
    return settings.ZENTRY[clave]


# --------------------------------------------------------------------------- placas

_A_LETRA = str.maketrans({'0': 'O', '1': 'I', '2': 'Z', '4': 'A', '5': 'S', '6': 'G', '7': 'T', '8': 'B'})
_A_DIGITO = str.maketrans({'O': '0', 'Q': '0', 'D': '0', 'U': '0', 'I': '1', 'L': '1', 'J': '1',
                           'Z': '2', 'A': '4', 'S': '5', 'G': '6', 'T': '7', 'B': '8'})


def normalizar_placa(texto):
    """
    Lleva una lectura del OCR al formato ABC-1234 (o ABC-123 de las placas antiguas).
    Corrige confusiones típicas (0/O, 1/I, 5/S, 8/B) según la posición.
    Devuelve '' si no parece una placa.
    """
    limpio = re.sub(r'[^A-Z0-9]', '', str(texto or '').upper())
    if len(limpio) not in (6, 7):
        return ''
    letras = limpio[:3].translate(_A_LETRA)
    digitos = limpio[3:].translate(_A_DIGITO)
    placa = f'{letras}-{digitos}'
    return placa if re.fullmatch(PATRON_PLACA, placa) else ''


def placa_escrita(texto):
    """Placa escrita por una persona: sin adivinar letras por números. 'abc1234' -> 'ABC-1234'."""
    limpio = re.sub(r'[\s-]', '', str(texto or '').upper())
    placa = f'{limpio[:3]}-{limpio[3:]}'
    return placa if re.fullmatch(PATRON_PLACA, placa) else ''


# --------------------------------------------------------------------------- clasificación

class Padron:
    """Foto del registro (M1) para clasificar varias plazas con dos consultas."""

    def __init__(self):
        self.por_placa = {}
        self.residentes_por_plaza = {}
        for placa, plaza_id, residente_id in Vehiculo.objects.values_list('placa', 'plaza_id', 'residente_id'):
            self.por_placa[placa] = (plaza_id, residente_id)
            if plaza_id:
                self.residentes_por_plaza.setdefault(plaza_id, set()).add(residente_id)

    def clasificar(self, plaza, ocupada, placa):
        if not ocupada:
            return Resultado.LIBRE
        if not placa:
            return Resultado.NO_CONCLUYENTE  # HU-03: placa ilegible u oculta
        registro = self.por_placa.get(placa)
        if registro is None:
            return Resultado.NO_REGISTRADA
        plaza_id, residente_id = registro
        if plaza_id == plaza.pk:
            return Resultado.COINCIDENCIA
        # HU-02: un vehículo sin plaza propia puede usar la plaza de su residente.
        if plaza_id is None and residente_id in self.residentes_por_plaza.get(plaza.pk, ()):
            return Resultado.COINCIDENCIA
        return Resultado.DISCREPANCIA


def clasificar(plaza, ocupada, placa):
    return Padron().clasificar(plaza, ocupada, placa)


# --------------------------------------------------------------------------- capturas

def _caja(valor):
    if not isinstance(valor, (list, tuple)) or len(valor) != 4:
        return None
    try:
        x1, y1, x2, y2 = (min(1.0, max(0.0, float(v))) for v in valor)
    except (TypeError, ValueError):
        return None
    if x2 <= x1 or y2 <= y1:
        return None
    return [round(x1, 4), round(y1, 4), round(x2, 4), round(y2, 4)]


def _clave(ocupada, placa):
    """Resumen comparable de lo que hay en una plaza: 'libre', '?' (ilegible) o la placa."""
    if not ocupada:
        return 'libre'
    return placa or '?'


def guardar_imagen_camara(camara, datos):
    """Guarda la última imagen de la cámara sin dejar archivos a medias."""
    if not datos or len(datos) > MAX_IMAGEN_BYTES or datos[:3] != b'\xff\xd8\xff':
        return False
    ruta = camara.ruta_imagen()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_suffix('.tmp')
    temporal.write_bytes(datos)
    os.replace(temporal, ruta)
    return True


def _observar(estado, det, capturada_en, padron):
    """
    Aplica una observación del agente al estado de la plaza.
    Devuelve un dict con el cambio confirmado o None si no cambió nada.
    """
    ocupada = bool(det.get('ocupada'))
    confianza = max(0, min(100, int(det.get('confianza') or 0)))
    placa = normalizar_placa(det.get('placa')) if ocupada else ''
    if placa and confianza < _config('CONFIANZA_MINIMA'):
        placa = ''  # lectura dudosa: cuenta como ilegible
    if not placa:
        confianza = 0
    # Mientras el vehículo siga ahí, una lectura corregida a mano sigue valiendo.
    if estado.correccion and ocupada and placa in ('', estado.correccion.get('leida')):
        placa, confianza = estado.correccion['corregida'], 100

    estado.visto = capturada_en
    actual = _clave(estado.ocupada, estado.placa)
    nueva = _clave(ocupada, placa)

    if nueva == actual:
        estado.candidato, estado.candidato_veces = None, 0
        if ocupada:
            estado.vehiculo_caja = _caja(det.get('vehiculo_caja')) or estado.vehiculo_caja
            estado.placa_caja = _caja(det.get('placa_caja')) or estado.placa_caja
            if placa:
                estado.confianza = confianza
        estado.save()
        return None

    candidato = estado.candidato or {}
    if candidato.get('clave') == nueva:
        estado.candidato_veces += 1
    elif ocupada and candidato.get('ocupada') and '?' in (nueva, candidato.get('clave')):
        # Mismo vehículo, pero el OCR a veces no lee la placa: se queda la lectura legible.
        if nueva == '?':
            nueva, placa, confianza = candidato['clave'], candidato['placa'], candidato['confianza']
        estado.candidato_veces += 1
    else:
        estado.candidato_veces = 1
    estado.candidato = {'clave': nueva, 'ocupada': ocupada, 'placa': placa, 'confianza': confianza,
                        'vehiculo_caja': _caja(det.get('vehiculo_caja')), 'placa_caja': _caja(det.get('placa_caja'))}

    necesarias = _config('LECTURAS_PARA_CONFIRMAR')
    if nueva == '?':
        if estado.ocupada and estado.placa:
            necesarias *= 3  # perder una placa ya leída exige más pruebas
        elif not estado.ocupada:
            necesarias *= 2  # al llegar se da tiempo al OCR antes de marcar "no concluyente"
    if estado.candidato_veces < necesarias:
        estado.save()
        return None

    # Cambio confirmado.
    plaza = estado.plaza
    placa_anterior = estado.placa
    if not estado.ocupada:
        tipo = Lectura.LLEGADA
    elif not ocupada:
        tipo = Lectura.SALIDA
    else:
        tipo = Lectura.CAMBIO
    cajas = estado.candidato
    estado.ocupada = ocupada
    estado.placa = placa
    estado.confianza = confianza
    estado.resultado = padron.clasificar(plaza, ocupada, placa)
    estado.vehiculo_caja = cajas['vehiculo_caja'] if ocupada else None
    estado.placa_caja = cajas['placa_caja'] if ocupada else None
    estado.actualizado = capturada_en
    estado.candidato, estado.candidato_veces = None, 0
    if not ocupada or (estado.correccion and placa != estado.correccion.get('corregida')):
        estado.correccion = None
    estado.save()
    return {'estado': estado, 'tipo': tipo, 'placa_anterior': placa_anterior}


def procesar_captura(camara, detecciones, capturada_en=None, captura_id=None, imagen=None):
    """
    Procesa una captura del agente para `camara`.
    `detecciones`: [{'plaza': 'P1', 'ocupada': True, 'placa': 'ABC-1234', 'confianza': 91,
                     'vehiculo_caja': [x1, y1, x2, y2], 'placa_caja': [...]}, ...] (cajas relativas).
    Devuelve {'aceptada': bool, 'motivo': str, 'cambios': [...]}.
    """
    ahora = timezone.now()
    if capturada_en is None or capturada_en > ahora + timedelta(minutes=5):
        capturada_en = ahora

    with transaction.atomic():
        camara = Camara.objects.select_for_update().get(pk=camara.pk)
        if camara.ultima_captura and capturada_en <= camara.ultima_captura:
            return {'aceptada': False, 'motivo': 'Captura repetida o más antigua que la última recibida.',
                    'cambios': []}
        camara.ultima_captura = capturada_en
        camara.ultima_conexion = ahora
        camara.save(update_fields=['ultima_captura', 'ultima_conexion'])
        if imagen:
            guardar_imagen_camara(camara, imagen)

        plazas = {p.nombre: p for p in camara.plazas.all()}
        estados = {e.plaza_id: e for e in EstadoPlaza.objects.filter(plaza__in=plazas.values())}
        padron = Padron()
        cambios = []
        for det in detecciones:
            plaza = plazas.get(str(det.get('plaza') or '').strip().upper())
            if plaza is None:
                continue
            estado = estados.get(plaza.pk) or EstadoPlaza(plaza=plaza)
            estado.plaza = plaza
            cambio = _observar(estado, det, capturada_en, padron)
            if not cambio:
                continue
            lectura = Lectura.objects.create(
                captura=captura_id, camara=camara, plaza=plaza, plaza_nombre=plaza.nombre,
                tipo=cambio['tipo'], placa=estado.placa, placa_anterior=cambio['placa_anterior'],
                confianza=estado.confianza, resultado=estado.resultado, capturada_en=capturada_en,
            )
            cambios.append((estado, lectura, cambio['placa_anterior']))

        for estado, lectura, placa_anterior in cambios:
            estado_plaza_cambiado.send(
                sender=EstadoPlaza, plaza=estado.plaza, estado=estado, lectura=lectura, imagen=imagen,
                motivo='lectura', placa_anterior=placa_anterior, usuario=None,
            )

    return {
        'aceptada': True,
        'motivo': '',
        'cambios': [
            {'plaza': e.plaza.nombre, 'tipo': lec.tipo, 'placa': e.placa, 'resultado': e.resultado}
            for e, lec, _ in cambios
        ],
    }


# --------------------------------------------------------------------------- cambios del registro

def reevaluar_plazas():
    """
    Vuelve a clasificar las plazas ocupadas con el padrón actual. Se llama cuando
    cambia el registro (vehículo nuevo, editado o eliminado; plaza reasignada).
    """
    padron = Padron()
    cambiadas = []
    for estado in EstadoPlaza.objects.select_related('plaza', 'plaza__camara').filter(ocupada=True):
        nuevo = padron.clasificar(estado.plaza, True, estado.placa)
        if nuevo == estado.resultado:
            continue
        estado.resultado = nuevo
        estado.save(update_fields=['resultado'])
        cambiadas.append(estado)
        estado_plaza_cambiado.send(
            sender=EstadoPlaza, plaza=estado.plaza, estado=estado, lectura=None, imagen=None,
            motivo='registro', placa_anterior=estado.placa, usuario=None,
        )
    return cambiadas


@transaction.atomic
def corregir_lectura(plaza, placa, usuario):
    """
    El personal corrige la placa que leyó el OCR (o la escribe si no se pudo leer).
    La corrección vale mientras el vehículo siga en la plaza.
    """
    placa_limpia = placa_escrita(placa)
    if not placa_limpia:
        raise ErrorVerificacion('Formato requerido: ABC-1234')
    estado = EstadoPlaza.objects.select_for_update().select_related('plaza').filter(plaza=plaza).first()
    if estado is None or not estado.ocupada:
        raise ErrorVerificacion('La plaza ya no tiene un vehículo, no hay lectura que corregir.')
    anterior = estado.placa
    if anterior == placa_limpia:
        raise ErrorVerificacion('La placa corregida es igual a la leída.')

    leida = estado.correccion.get('leida', anterior) if estado.correccion else anterior
    estado.correccion = {'leida': leida, 'corregida': placa_limpia, 'usuario': usuario.get_username()}
    estado.placa = placa_limpia
    estado.confianza = 100
    estado.resultado = clasificar(plaza, True, placa_limpia)
    estado.actualizado = timezone.now()
    estado.candidato, estado.candidato_veces = None, 0
    estado.save()
    lectura = Lectura.objects.create(
        camara=plaza.camara, plaza=plaza, plaza_nombre=plaza.nombre, tipo=Lectura.CORRECCION,
        placa=placa_limpia, placa_anterior=anterior, confianza=100, resultado=estado.resultado,
        capturada_en=estado.actualizado,
    )
    estado_plaza_cambiado.send(
        sender=EstadoPlaza, plaza=plaza, estado=estado, lectura=lectura, imagen=None,
        motivo='correccion', placa_anterior=anterior, usuario=usuario,
    )
    return estado


# --------------------------------------------------------------------------- agente

def marcar_agente_activo(usuario):
    """Usa last_login del usuario del agente como "último contacto" (máximo una escritura cada 10 s)."""
    ahora = timezone.now()
    if not usuario.last_login or (ahora - usuario.last_login).total_seconds() > 10:
        get_user_model().objects.filter(pk=usuario.pk).update(last_login=ahora)
        usuario.last_login = ahora


def estado_agente():
    from cuentas.servicios import USUARIO_AGENTE

    limite = _config('AGENTE_SIN_SENAL_S')
    ahora = timezone.now()
    agente = get_user_model().objects.filter(username=USUARIO_AGENTE).values_list('last_login', flat=True).first()
    camaras = list(Camara.objects.filter(activa=True).values_list('ultima_captura', flat=True))
    en_linea_camaras = sum(1 for c in camaras if c and (ahora - c).total_seconds() < limite)
    ultimo = max([c for c in camaras if c] + ([agente] if agente else []), default=None)
    return {
        'en_linea': bool(ultimo and (ahora - ultimo).total_seconds() < limite),
        'ultimo_contacto': ultimo,
        'camaras_en_linea': en_linea_camaras,
        'camaras_total': len(camaras),
    }


def configuracion_agente():
    """Lo que el agente necesita saber: cámaras activas, sus fuentes y sus plazas."""
    camaras = []
    for camara in camaras_con_plazas():
        camaras.append({
            'codigo': camara.codigo,
            'nombre': camara.nombre,
            'tipo': camara.tipo,
            'url': camara.url,
            'ancho': camara.ancho,
            'alto': camara.alto,
            'plazas': [{'nombre': p.nombre, 'poligono': p.poligono} for p in camara.plazas.all() if p.poligono],
        })
    datos = {
        'intervalo_s': _config('INTERVALO_CAPTURA_S'),
        'confianza_minima': _config('CONFIANZA_MINIMA'),
        'camaras': camaras,
    }
    datos['version'] = hashlib.sha1(json.dumps(datos, sort_keys=True).encode()).hexdigest()[:12]
    return datos


def depurar_lecturas(dias=None):
    """Borra lecturas más antiguas que el periodo de retención."""
    dias = dias or _config('RETENCION_DIAS')
    borradas, _ = Lectura.objects.filter(capturada_en__lt=timezone.now() - timedelta(days=dias)).delete()
    return borradas


def estados_por_plaza():
    """{id_plaza: EstadoPlaza} de todas las plazas (las que no tienen estado se consideran libres)."""
    return {e.plaza_id: e for e in EstadoPlaza.objects.all()}


def plazas_sin_estado():
    return Plaza.objects.filter(estado__isnull=True)
