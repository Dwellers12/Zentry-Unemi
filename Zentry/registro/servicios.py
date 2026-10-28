"""
Reglas de negocio de M1 (registro). Las vistas y la API solo llaman a estas funciones.

Este módulo no depende de verificacion ni de alertas: cuando algo cambia aquí,
Django emite sus señales de modelo (post_save / post_delete) y los otros módulos
reaccionan por su cuenta.
"""
from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone

from .models import Camara, Plaza, Vehiculo, numero_de


class ErrorPlazas(ValueError):
    """Lista de plazas no válida (el mensaje se muestra tal cual al usuario)."""


def _normalizar_poligono(poligono):
    if not isinstance(poligono, (list, tuple)) or len(poligono) < 3:
        raise ErrorPlazas('Cada plaza necesita al menos 3 vértices.')
    puntos = []
    for punto in poligono:
        if (not isinstance(punto, (list, tuple)) or len(punto) != 2
                or not all(isinstance(c, (int, float)) and not isinstance(c, bool) for c in punto)):
            raise ErrorPlazas('Cada vértice debe ser un par [x, y].')
        x, y = (min(1.0, max(0.0, float(c))) for c in punto)
        puntos.append([round(x, 4), round(y, 4)])
    return puntos


def validar_lista_plazas(camara, plazas):
    """
    Revisa la lista que llega del editor: [{'id': 5 | None, 'nombre': 'P1', 'poligono': [[x, y], ...]}].
    Devuelve la lista normalizada o lanza ErrorPlazas.
    """
    if not isinstance(plazas, list):
        raise ErrorPlazas('El campo "plazas" debe ser una lista.')
    propias = set(camara.plazas.values_list('pk', flat=True))
    otras = {
        nombre.upper(): pk
        for pk, nombre in Plaza.objects.exclude(camara=camara).values_list('pk', 'nombre')
    }
    vistos, limpias = set(), []
    for item in plazas:
        if not isinstance(item, dict):
            raise ErrorPlazas('Formato de plaza no válido.')
        nombre = str(item.get('nombre') or '').strip().upper()
        if not nombre:
            raise ErrorPlazas('Cada plaza necesita un nombre.')
        if len(nombre) > 24:
            raise ErrorPlazas(f'El nombre "{nombre}" es demasiado largo.')
        if nombre in vistos or nombre in otras:
            raise ErrorPlazas(f'El nombre {nombre} no se puede repetir.')
        vistos.add(nombre)
        pk = item.get('id')
        if pk is not None and pk not in propias:
            pk = None  # una plaza de otra cámara o borrada: se crea como nueva
        limpias.append({'id': pk, 'nombre': nombre, 'poligono': _normalizar_poligono(item.get('poligono'))})
    return limpias


@transaction.atomic
def guardar_plazas_camara(camara, plazas, origen=Camara.ORIGEN_MANUAL):
    """
    Reemplaza las plazas de una cámara por la lista recibida (editor de plazas).

    Las plazas que ya no están se eliminan: sus vehículos quedan sin plaza
    asignada y sus alertas abiertas se resuelven solas (módulo alertas).
    Devuelve (plazas_guardadas, nombres_eliminados).
    """
    limpias = validar_lista_plazas(camara, plazas)
    conservar = {p['id'] for p in limpias if p['id']}
    eliminadas = list(camara.plazas.exclude(pk__in=conservar))
    nombres_eliminados = [p.nombre for p in eliminadas]
    for plaza in eliminadas:
        plaza.delete()

    # Primero se liberan los nombres (un intercambio P1 <-> P2 no choca con unique).
    existentes = {p.pk: p for p in camara.plazas.all()}
    for plaza in existentes.values():
        plaza.nombre = f'__tmp_{plaza.pk}'
        Plaza.objects.filter(pk=plaza.pk).update(nombre=plaza.nombre)

    guardadas = []
    for item in limpias:
        plaza = existentes.get(item['id']) or Plaza(camara=camara)
        plaza.nombre = item['nombre']
        plaza.poligono = item['poligono']
        plaza.save()
        guardadas.append(plaza)

    camara.plazas_origen = origen
    camara.save(update_fields=['plazas_origen'])
    return guardadas, nombres_eliminados


def plazas_de_camara(camara):
    return sorted(camara.plazas.all(), key=lambda p: (numero_de(p.nombre), p.nombre))


def siguiente_nombre_libre():
    numeros = [numero_de(n) for n in Plaza.objects.values_list('nombre', flat=True)]
    return f'P{max(numeros, default=0) + 1}'


@transaction.atomic
def registrar_camara(codigo, nombre, tipo, url='', ancho=None, alto=None, plazas_sugeridas=None,
                     sincronizar_plazas=False):
    """
    Alta o actualización de una cámara que anuncia el agente (por ejemplo, las
    cámaras virtuales del simulador al conectarse).

    - Guarda las plazas sugeridas para el botón "Detectar automáticamente".
    - Si la cámara no tiene plazas, o si `sincronizar_plazas` es True y sus plazas
      vienen del simulador (nadie las editó a mano), aplica las sugeridas.
    Devuelve (camara, creada, plazas_aplicadas).
    """
    camara, creada = Camara.objects.get_or_create(
        codigo=codigo,
        defaults={'nombre': nombre or codigo, 'tipo': tipo, 'url': url or '',
                  'orden': Camara.objects.count() + 1},
    )
    cambios = []
    era_otro_tipo = camara.tipo != tipo
    if era_otro_tipo:
        camara.tipo = tipo
        cambios.append('tipo')
    if url and camara.url != url:
        camara.url = url
        cambios.append('url')
    if ancho and alto and (camara.ancho, camara.alto) != (ancho, alto):
        camara.ancho, camara.alto = int(ancho), int(alto)
        cambios += ['ancho', 'alto']
    camara.ultima_conexion = timezone.now()
    cambios.append('ultima_conexion')

    aplicadas = False
    if plazas_sugeridas:
        camara.plazas_sugeridas = [
            {'nombre': str(p['nombre']).upper(), 'poligono': _normalizar_poligono(p['poligono'])}
            for p in plazas_sugeridas if p.get('nombre') and p.get('poligono')
        ]
        camara.sugeridas_en = timezone.now()
        cambios += ['plazas_sugeridas', 'sugeridas_en']
    camara.save(update_fields=list(dict.fromkeys(cambios)) if not creada else None)

    sin_plazas = not camara.plazas.exists()
    sincroniza = sincronizar_plazas and (
        camara.plazas_origen == Camara.ORIGEN_SIMULADOR or (era_otro_tipo and tipo == Camara.VIRTUAL)
    )
    if camara.plazas_sugeridas and (sin_plazas or sincroniza):
        aplicadas = _aplicar_sugeridas(camara, tipo == Camara.VIRTUAL)
    return camara, creada, aplicadas


def _aplicar_sugeridas(camara, es_simulador):
    """Aplica las plazas sugeridas conservando las plazas (y sus vehículos) con el mismo nombre."""
    actuales = {p.nombre: p for p in camara.plazas.all()}
    libres = {p.nombre: p for p in Plaza.objects.filter(camara__isnull=True)}
    ocupados = set(Plaza.objects.exclude(camara=camara).exclude(camara__isnull=True).values_list('nombre', flat=True))
    lista = []
    for sugerida in camara.plazas_sugeridas:
        nombre = sugerida['nombre']
        if nombre in ocupados:
            continue  # ese nombre ya lo usa otra cámara
        plaza = actuales.get(nombre) or libres.get(nombre)
        if plaza and plaza.camara_id is None:
            plaza.camara = camara
            plaza.save(update_fields=['camara'])
        lista.append({'id': plaza.pk if plaza else None, 'nombre': nombre, 'poligono': sugerida['poligono']})
    if not lista:
        return False
    origen = Camara.ORIGEN_SIMULADOR if es_simulador else Camara.ORIGEN_DETECCION
    guardar_plazas_camara(camara, lista, origen=origen)
    return True


def camaras_con_plazas():
    """Cámaras activas con sus plazas ordenadas (para el agente y la web)."""
    return Camara.objects.filter(activa=True).prefetch_related(
        Prefetch('plazas', queryset=Plaza.objects.order_by('orden', 'nombre'))
    )


def asignaciones():
    """{id_plaza: [vehículos autorizados]} para verificar placas sin repetir consultas."""
    resultado = {}
    for vehiculo in Vehiculo.objects.select_related('residente').exclude(plaza__isnull=True):
        resultado.setdefault(vehiculo.plaza_id, []).append(vehiculo)
    return resultado
