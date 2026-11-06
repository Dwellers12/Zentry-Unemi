"""
Reglas de negocio de M3 (alertas). Las vistas, los receptores de señales y los
comandos solo llaman a estas funciones.

alertas depende de verificacion y de registro; ninguno de ellos depende de alertas.
"""
import csv
import io
import logging
from datetime import datetime, time, timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from registro.models import Camara, Plaza, Vehiculo
from verificacion.models import RESULTADOS_IRREGULARES, EstadoPlaza, Lectura, Resultado
from verificacion.servicios import (ErrorVerificacion, clasificar, corregir_lectura, estado_agente,
                                    placa_escrita)

from . import puertos
from .models import Alerta, EventoAlerta

log = logging.getLogger('zentry.alertas')

MIN_RESOLUCION, MAX_RESOLUCION = 10, 300


class ErrorAlerta(ValueError):
    """Operación no válida (el mensaje se muestra tal cual al usuario)."""


def _hora(momento):
    return timezone.localtime(momento).strftime('%H:%M')


def _autor(usuario):
    if usuario is None:
        return EventoAlerta.SISTEMA
    return usuario.get_full_name() or usuario.get_username()


def _evento(alerta, tipo, texto, usuario=None, en=None):
    return EventoAlerta.objects.create(
        alerta=alerta, tipo=tipo, texto=texto, autor=_autor(usuario), usuario=usuario,
        creado_en=en or timezone.now(),
    )


# --------------------------------------------------------------------------- evidencia (RF-10)

def _recortar(datos, caja, margen=0.35, ancho_max=720):
    """Recorte JPEG alrededor de la caja relativa [x1, y1, x2, y2]; None si no se puede."""
    if not datos or not caja:
        return None
    try:
        from PIL import Image

        imagen = Image.open(io.BytesIO(datos))
        imagen = imagen.convert('RGB')
        ancho, alto = imagen.size
        x1, y1, x2, y2 = caja
        mx, my = (x2 - x1) * margen, (y2 - y1) * margen
        izquierda, arriba = max(0, (x1 - mx) * ancho), max(0, (y1 - my) * alto)
        derecha, abajo = min(ancho, (x2 + mx) * ancho), min(alto, (y2 + my) * alto)
        if derecha - izquierda < 8 or abajo - arriba < 8:
            return None
        recorte = imagen.crop((int(izquierda), int(arriba), int(derecha), int(abajo)))
        if recorte.width > ancho_max:
            recorte = recorte.resize((ancho_max, round(recorte.height * ancho_max / recorte.width)))
        salida = io.BytesIO()
        recorte.save(salida, 'JPEG', quality=85)
        return salida.getvalue()
    except Exception:  # noqa: BLE001  (imagen dañada: la alerta se crea igual)
        log.exception('No se pudo recortar la evidencia')
        return None


def _guardar_evidencia(alerta, imagen, caja):
    almacen = puertos.evidencia()
    base = f'{alerta.plaza_nombre}_{alerta.placa or "ilegible"}'
    try:
        if imagen:
            alerta.evidencia = almacen.guardar(f'{base}.jpg', imagen)
            recorte = _recortar(imagen, caja)
            if recorte:
                alerta.evidencia_recorte = almacen.guardar(f'{base}_recorte.jpg', recorte)
    except Exception:  # noqa: BLE001
        log.exception('No se pudo guardar la evidencia de la alerta %s', alerta.pk)


def _imagen_actual(camara):
    """Última imagen de la cámara (cuando la alerta nace por un cambio en el registro)."""
    if camara is None:
        return None
    try:
        return camara.ruta_imagen().read_bytes()
    except OSError:
        return None


# --------------------------------------------------------------------------- ciclo de vida

def _crear(plaza, estado, lectura, imagen, motivo):
    camara = plaza.camara
    if imagen is None:
        imagen = _imagen_actual(camara)
    caja = estado.vehiculo_caja or plaza.caja()
    alerta = Alerta.objects.create(
        plaza=plaza, plaza_nombre=plaza.nombre, camara=camara, camara_nombre=camara.nombre if camara else '',
        placa=estado.placa, resultado=Alerta.resultado_de(estado.resultado), confianza=estado.confianza,
        creada_en=lectura.capturada_en if lectura else timezone.now(), lectura=lectura,
        vehiculo_caja=caja,
    )
    _guardar_evidencia(alerta, imagen, caja)
    alerta.save(update_fields=['evidencia', 'evidencia_recorte'])
    if motivo == 'registro':
        texto = f'Creada al cambiar el registro: {estado.placa} ya no está autorizada en {plaza.nombre}'
    elif estado.placa:
        texto = f'Creada por la cámara: {estado.placa}'
    else:
        texto = 'Creada por la cámara: placa ilegible u oculta'
    _evento(alerta, EventoAlerta.CREADA, texto, en=alerta.creada_en)
    transaction.on_commit(lambda: _notificar(alerta.pk))
    return alerta


def _notificar(alerta_id):
    alerta = Alerta.objects.filter(pk=alerta_id).first()
    if alerta is None or alerta.resultado not in Alerta.RESULTADOS_CON_AVISO:
        return
    try:
        puertos.notificacion().notificar(alerta)
    except Exception:  # noqa: BLE001  (un aviso fallido no debe tumbar la verificación)
        log.exception('No se pudo enviar la notificación de la alerta %s', alerta_id)


def _resolver_sola(alerta, texto, en=None):
    en = en or timezone.now()
    alerta.estado = Alerta.RESUELTA
    alerta.resuelta_en = en
    alerta.resolucion = texto
    alerta.resolucion_autor = EventoAlerta.SISTEMA
    alerta.resolucion_automatica = True
    alerta.save()
    _evento(alerta, EventoAlerta.AUTOMATICA, texto, en=en)


@transaction.atomic
def sincronizar_plaza(plaza, estado, lectura=None, imagen=None, motivo='lectura', placa_anterior='', usuario=None):
    """
    Pone las alertas de una plaza de acuerdo con su nuevo estado (se llama desde
    la señal `estado_plaza_cambiado`). Crea, actualiza o resuelve alertas.
    """
    irregular = estado.resultado in RESULTADOS_IRREGULARES
    nuevo_resultado = Alerta.resultado_de(estado.resultado)
    en = lectura.capturada_en if lectura else timezone.now()
    cubierta = False

    for alerta in Alerta.objects.select_for_update().filter(plaza=plaza, estado__in=Alerta.ACTIVAS):
        if irregular and alerta.placa == estado.placa:
            # El mismo vehículo sigue sin autorización; solo puede cambiar el motivo.
            if alerta.resultado != nuevo_resultado:
                alerta.resultado = nuevo_resultado
                alerta.save(update_fields=['resultado'])
            cubierta = True
            continue

        if motivo == 'correccion':
            _evento(alerta, EventoAlerta.CORREGIDA,
                    f'Lectura corregida: {alerta.placa or "ilegible"} → {estado.placa}', usuario=usuario, en=en)
            alerta.placa = estado.placa
            alerta.confianza = 100
            if irregular:
                alerta.resultado = nuevo_resultado
                alerta.save(update_fields=['placa', 'confianza', 'resultado'])
                cubierta = True
            else:
                alerta.resultado = Alerta.CORREGIDA
                alerta.save(update_fields=['placa', 'confianza', 'resultado'])
                _resolver_sola(alerta, f'Resuelta automáticamente: la lectura se corrigió a {estado.placa}, '
                                       'que es la placa autorizada.', en)
            continue

        if (lectura and lectura.tipo == Lectura.CAMBIO and not alerta.placa and estado.placa):
            # Era "no concluyente" y la cámara por fin leyó la placa del mismo vehículo.
            _evento(alerta, EventoAlerta.CORREGIDA, f'La cámara leyó la placa: {estado.placa}', en=en)
            alerta.placa, alerta.confianza = estado.placa, estado.confianza
            if irregular:
                alerta.resultado = nuevo_resultado
                alerta.save(update_fields=['placa', 'confianza', 'resultado'])
                cubierta = True
            else:
                alerta.save(update_fields=['placa', 'confianza'])
                _resolver_sola(alerta, f'Resuelta automáticamente: la cámara leyó {estado.placa}, '
                                       f'que está autorizada en la plaza {plaza.nombre}.', en)
            continue

        if motivo == 'registro' and estado.ocupada and alerta.placa == estado.placa:
            texto = f'Resuelta automáticamente: la placa {estado.placa} fue asignada a la plaza {plaza.nombre}.'
        else:
            texto = (f'Resuelta automáticamente: el vehículo {alerta.placa or "sin placa legible"} '
                     f'salió de la plaza {plaza.nombre} a las {_hora(en)}.')
        _resolver_sola(alerta, texto, en)

    if irregular and not cubierta:
        _crear(plaza, estado, lectura, imagen, motivo)


def resolver_por_plaza_eliminada(plaza):
    for alerta in Alerta.objects.filter(plaza=plaza, estado__in=Alerta.ACTIVAS):
        _resolver_sola(alerta, f'Resuelta automáticamente: la plaza {plaza.nombre} fue eliminada.')


# --------------------------------------------------------------------------- acciones del personal

def _validar_nota(nota):
    nota = (nota or '').strip()
    if not MIN_RESOLUCION <= len(nota) <= MAX_RESOLUCION:
        raise ErrorAlerta(f'La descripción debe tener entre {MIN_RESOLUCION} y {MAX_RESOLUCION} caracteres.')
    return nota


@transaction.atomic
def cambiar_estado(alerta, destino, nota, usuario):
    """Pendiente / Revisada / Resuelta, con historial (RF-09). Reabrir también queda registrado."""
    if destino not in dict(Alerta.ESTADOS):
        raise ErrorAlerta('Estado no válido.')
    alerta = Alerta.objects.select_for_update().get(pk=alerta.pk)
    if alerta.estado == destino:
        raise ErrorAlerta(f'La alerta ya está {alerta.get_estado_display().lower()}.')
    nota = (nota or '').strip()
    if len(nota) > MAX_RESOLUCION:
        raise ErrorAlerta(f'La nota no puede pasar de {MAX_RESOLUCION} caracteres.')
    ahora = timezone.now()
    reabre = alerta.estado == Alerta.RESUELTA or destino == Alerta.PENDIENTE
    etiqueta = dict(Alerta.ESTADOS)[destino]

    if destino == Alerta.RESUELTA:
        nota = _validar_nota(nota)
        alerta.resuelta_en = ahora
        alerta.resolucion = nota
        alerta.resolucion_autor = _autor(usuario)
        alerta.resolucion_automatica = False
        _evento(alerta, EventoAlerta.RESUELTA, nota, usuario, ahora)
    elif reabre:
        alerta.resuelta_en = None
        _evento(alerta, EventoAlerta.REABIERTA, f'Alerta reabierta como {etiqueta}' + (f': {nota}' if nota else ''),
                usuario, ahora)
    else:
        _evento(alerta, EventoAlerta.REVISADA, 'Alerta revisada' + (f': {nota}' if nota else ''), usuario, ahora)
    alerta.estado = destino
    alerta.save()
    return alerta


@transaction.atomic
def editar_resolucion(alerta, texto, usuario):
    if alerta.estado != Alerta.RESUELTA:
        raise ErrorAlerta('Solo se puede editar la descripción de una alerta resuelta.')
    texto = _validar_nota(texto)
    if texto == alerta.resolucion:
        raise ErrorAlerta('La descripción no cambió.')
    anterior = alerta.resolucion
    alerta.resolucion = texto
    alerta.resolucion_autor = _autor(usuario)
    alerta.resolucion_automatica = False
    alerta.save(update_fields=['resolucion', 'resolucion_autor', 'resolucion_automatica'])
    _evento(alerta, EventoAlerta.EDITADA, f'Descripción editada. Antes: «{anterior}»', usuario)
    return alerta


def corregir_placa(alerta, placa, usuario):
    """
    Corrige la placa que leyó la cámara. Si el vehículo sigue en la plaza se corrige
    también la lectura en vivo (verificacion), y la alerta se actualiza o resuelve sola.
    """
    nueva = placa_escrita(placa)
    if not nueva:
        raise ErrorAlerta('Formato requerido: ABC-1234')
    if nueva == alerta.placa:
        raise ErrorAlerta('La placa corregida es igual a la detectada.')

    estado = EstadoPlaza.objects.filter(plaza_id=alerta.plaza_id).first() if alerta.plaza_id else None
    en_vivo = alerta.activa and estado is not None and estado.ocupada and estado.placa == alerta.placa
    if en_vivo:
        try:
            corregir_lectura(alerta.plaza, nueva, usuario)
        except ErrorVerificacion as error:
            raise ErrorAlerta(str(error))
        alerta.refresh_from_db()
        return alerta

    # El vehículo ya no está: solo se corrige el historial de la alerta.
    with transaction.atomic():
        _evento(alerta, EventoAlerta.CORREGIDA, f'Lectura corregida: {alerta.placa or "ilegible"} → {nueva}', usuario)
        alerta.placa, alerta.confianza = nueva, 100
        resultado = clasificar(alerta.plaza, True, nueva) if alerta.plaza else Resultado.NO_REGISTRADA
        if resultado == Resultado.COINCIDENCIA:
            alerta.resultado = Alerta.CORREGIDA
            alerta.save()
            if alerta.activa:
                _resolver_sola(alerta, f'Resuelta automáticamente: la lectura se corrigió a {nueva}, '
                                       'que es la placa autorizada.')
        else:
            alerta.resultado = Alerta.resultado_de(resultado) or Alerta.NO_REGISTRADA
            alerta.save()
    return alerta


def marcar_vistas(usuario, alertas=None):
    consulta = alertas if alertas is not None else Alerta.objects.filter(estado=Alerta.PENDIENTE)
    usuario.alertas_vistas.add(*consulta.exclude(vista_por=usuario))


# --------------------------------------------------------------------------- consultas

def no_vistas(usuario):
    return Alerta.objects.filter(estado=Alerta.PENDIENTE).exclude(vista_por=usuario)


def ordenar(alertas):
    """Atrasadas primero (pendientes de más de ALERTA_ATRASADA_MIN minutos), luego las más nuevas."""
    return sorted(alertas, key=lambda a: (not a.atrasada, -a.creada_en.timestamp()))


def filtrar(params):
    """Filtros de la lista de alertas y de reportes. `params` es un QueryDict o dict."""
    consulta = Alerta.objects.select_related('plaza', 'camara')
    estado = params.get('estado') or ''
    if estado in dict(Alerta.ESTADOS):
        consulta = consulta.filter(estado=estado)
    elif estado == 'activas':
        consulta = consulta.filter(estado__in=Alerta.ACTIVAS)
    elif estado == 'hoy':
        consulta = consulta.filter(creada_en__date=timezone.localdate())
    resultado = params.get('resultado') or ''
    if resultado in dict(Alerta.RESULTADOS):
        consulta = consulta.filter(resultado=resultado)
    if params.get('camara'):
        consulta = consulta.filter(camara__codigo=params['camara'])
    if params.get('plaza'):
        consulta = consulta.filter(plaza_nombre=str(params['plaza']).upper())
    texto = (params.get('q') or '').strip()
    if texto:
        consulta = consulta.filter(Q(placa__icontains=texto) | Q(plaza_nombre__iexact=texto))
    desde, hasta = _fecha(params.get('desde')), _fecha(params.get('hasta'))
    if desde and hasta and desde > hasta:
        raise ErrorAlerta('La fecha inicial no puede ser posterior a la final')
    zona = timezone.get_current_timezone()
    if desde:
        consulta = consulta.filter(creada_en__gte=datetime.combine(desde, time.min, tzinfo=zona))
    if hasta:
        consulta = consulta.filter(creada_en__lte=datetime.combine(hasta, time.max, tzinfo=zona))
    return consulta


def _fecha(texto):
    try:
        return datetime.strptime(str(texto), '%Y-%m-%d').date() if texto else None
    except ValueError:
        return None


def resumen():
    """Números del dashboard."""
    hoy = timezone.localdate()
    conteo = Alerta.objects.aggregate(
        total=Count('pk'),
        pendientes=Count('pk', filter=Q(estado=Alerta.PENDIENTE)),
        revisadas=Count('pk', filter=Q(estado=Alerta.REVISADA)),
        resueltas=Count('pk', filter=Q(estado=Alerta.RESUELTA)),
        hoy=Count('pk', filter=Q(creada_en__date=hoy)),
        resueltas_hoy=Count('pk', filter=Q(estado=Alerta.RESUELTA, resuelta_en__date=hoy)),
    )
    plazas = Plaza.objects.filter(camara__isnull=False).count()
    estados = dict(
        EstadoPlaza.objects.filter(plaza__camara__isnull=False, ocupada=True)
        .values_list('resultado').annotate(n=Count('pk')).values_list('resultado', 'n')
    )
    ocupadas = sum(estados.values())
    limite = timezone.now() - timedelta(minutes=settings.ZENTRY['ALERTA_ATRASADA_MIN'])
    tiempos = [
        (resuelta - creada).total_seconds() / 60
        for creada, resuelta in Alerta.objects.filter(estado=Alerta.RESUELTA, resuelta_en__isnull=False)
        .order_by('-resuelta_en').values_list('creada_en', 'resuelta_en')[:500]
    ]
    conteo.update({
        'promedio_min': round(sum(max(0, t) for t in tiempos) / len(tiempos)) if tiempos else None,
        'atrasadas': Alerta.objects.filter(estado=Alerta.PENDIENTE, creada_en__lt=limite).count(),
        'plazas': plazas,
        'libres': max(0, plazas - ocupadas),
        'ocupadas': estados.get(Resultado.COINCIDENCIA, 0),
        'no_autorizadas': estados.get(Resultado.DISCREPANCIA, 0) + estados.get(Resultado.NO_REGISTRADA, 0),
        'no_concluyentes': estados.get(Resultado.NO_CONCLUYENTE, 0),
    })
    return conteo


def actividad(limite=30, plaza=None):
    """Actividad reciente: llegadas, salidas y lo que pasó con las alertas, mezclado por fecha."""
    lecturas = Lectura.objects.select_related('plaza')
    eventos = EventoAlerta.objects.select_related('alerta')
    if plaza is not None:
        lecturas = lecturas.filter(plaza=plaza)
        eventos = eventos.filter(alerta__plaza=plaza)
    items = [
        {'tipo': lec.tipo, 'texto': lec.texto, 'autor': EventoAlerta.SISTEMA, 'en': lec.capturada_en,
         'plaza': lec.plaza_nombre, 'alerta_id': None}
        for lec in lecturas.order_by('-capturada_en', '-pk')[:limite]
    ] + [
        {'tipo': ev.tipo, 'texto': ev.texto, 'autor': ev.autor, 'en': ev.creado_en,
         'plaza': ev.alerta.plaza_nombre, 'alerta_id': ev.alerta_id}
        for ev in eventos.order_by('-creado_en', '-pk')[:limite]
    ]
    items.sort(key=lambda i: i['en'], reverse=True)
    return items[:limite]


def plazas_en_vivo():
    """Estado de todas las plazas con cámara, para el dashboard y el mapa (polling)."""
    estados = {e.plaza_id: e for e in EstadoPlaza.objects.all()}
    activas = {}
    for alerta in Alerta.objects.filter(estado__in=Alerta.ACTIVAS).order_by('creada_en'):
        activas[alerta.plaza_id] = alerta
    duenos, vehiculos_por_placa = {}, {}
    for vehiculo in Vehiculo.objects.select_related('residente').order_by('placa'):
        vehiculos_por_placa[vehiculo.placa] = vehiculo
        if vehiculo.plaza_id:
            duenos.setdefault(vehiculo.plaza_id, []).append(vehiculo)

    plazas = []
    for plaza in Plaza.objects.select_related('camara').order_by('orden', 'nombre'):
        estado = estados.get(plaza.pk)
        autorizados = duenos.get(plaza.pk, [])
        resultado = estado.resultado if estado and estado.ocupada else Resultado.LIBRE
        vehiculo = vehiculos_por_placa.get(estado.placa) if estado and estado.placa else None
        alerta = activas.get(plaza.pk)
        plazas.append({
            'id': plaza.pk,
            'nombre': plaza.nombre,
            'camara': plaza.camara.codigo if plaza.camara else None,
            'poligono': plaza.poligono,
            'resultado': resultado,
            'estado': estado.estado_visual if estado and estado.ocupada else 'Libre',
            'placa': estado.placa if estado and estado.ocupada else '',
            'confianza': estado.confianza if estado and estado.ocupada else None,
            'desde': estado.actualizado.isoformat() if estado and estado.ocupada and estado.actualizado else None,
            'vehiculo_caja': estado.vehiculo_caja if estado and estado.ocupada else None,
            'placa_caja': estado.placa_caja if estado and estado.ocupada else None,
            'corregida': bool(estado and estado.correccion),
            'detectado': (
                {'marca_modelo': vehiculo.marca_modelo, 'color': vehiculo.color,
                 'residente': vehiculo.residente.nombre, 'departamento': vehiculo.residente.departamento}
                if vehiculo else None
            ),
            'asignada_a': (
                {'residente': autorizados[0].residente.nombre, 'departamento': autorizados[0].residente.departamento,
                 'residente_id': autorizados[0].residente_id,
                 'vehiculos': [{'placa': v.placa, 'marca_modelo': v.marca_modelo, 'color': v.color}
                               for v in autorizados]}
                if autorizados else None
            ),
            'alerta': (
                {'id': alerta.pk, 'estado': alerta.estado, 'resultado': alerta.get_resultado_display(),
                 'placa': alerta.placa, 'atrasada': alerta.atrasada}
                if alerta else None
            ),
        })
    return plazas


def camaras_en_vivo():
    return [
        {
            'codigo': c.codigo, 'nombre': c.nombre, 'tipo': c.tipo, 'activa': c.activa,
            'en_linea': c.en_linea, 'ancho': c.ancho, 'alto': c.alto,
            'ultima_captura': c.ultima_captura.isoformat() if c.ultima_captura else None,
            'tiene_imagen': c.ruta_imagen().exists(),
        }
        for c in Camara.objects.filter(activa=True)
    ]


def estado_en_vivo(usuario, desde=None):
    """Todo lo que la web consulta cada pocos segundos (dashboard, mapa, campana)."""
    pendientes_no_vistas = no_vistas(usuario)
    ultimas = Alerta.objects.order_by('-creada_en', '-pk')[:5]
    vistas = set(usuario.alertas_vistas.filter(pk__in=[a.pk for a in ultimas]).values_list('pk', flat=True))
    datos = {
        'ahora': timezone.now().isoformat(),
        'resumen': resumen(),
        'agente': {**estado_agente()},
        'camaras': camaras_en_vivo(),
        'plazas': plazas_en_vivo(),
        'campana': {
            'no_vistas': pendientes_no_vistas.count(),
            'ultimas': [
                {'id': a.pk, 'plaza': a.plaza_nombre, 'placa': a.placa_visible, 'estado': a.get_estado_display(),
                 'creada_en': a.creada_en.isoformat(), 'vista': a.pk in vistas}
                for a in ultimas
            ],
        },
        'avisos': [],
    }
    if datos['agente']['ultimo_contacto']:
        datos['agente']['ultimo_contacto'] = datos['agente']['ultimo_contacto'].isoformat()
    if desde:
        # Avisos (toasts) de lo ocurrido desde la última consulta del navegador.
        for ev in (EventoAlerta.objects.select_related('alerta')
                   .filter(creado_en__gt=desde, tipo__in=[EventoAlerta.CREADA, EventoAlerta.AUTOMATICA])
                   .order_by('creado_en')[:10]):
            texto = (f'Nueva alerta en {ev.alerta.plaza_nombre}' if ev.tipo == EventoAlerta.CREADA
                     else f'Alerta resuelta automáticamente · {ev.alerta.plaza_nombre}')
            datos['avisos'].append({'texto': texto, 'alerta_id': ev.alerta_id, 'tipo': ev.tipo})
    return datos


# --------------------------------------------------------------------------- reportes

def reporte(alertas):
    """Datos de las gráficas de Reportes a partir de una consulta ya filtrada."""
    alertas = list(alertas)
    hoy = timezone.localdate()
    dias = []
    for i in range(6, -1, -1):
        dia = hoy - timedelta(days=i)
        dias.append({'etiqueta': dia.strftime('%d/%m'),
                     'valor': sum(1 for a in alertas if timezone.localtime(a.creada_en).date() == dia)})
    por_plaza = {}
    for a in alertas:
        por_plaza[a.plaza_nombre] = por_plaza.get(a.plaza_nombre, 0) + 1
    plazas = sorted(({'etiqueta': k, 'valor': v} for k, v in por_plaza.items()), key=lambda i: -i['valor'])[:8]
    resueltas = [a for a in alertas if a.resuelta_en]
    minutos = [max(0, (a.resuelta_en - a.creada_en).total_seconds() / 60) for a in resueltas]
    return {
        'dias': dias,
        'plazas': plazas,
        'total': len(alertas),
        'resueltas': len(resueltas),
        'automaticas': sum(1 for a in resueltas if a.resolucion_automatica),
        'promedio_min': round(sum(minutos) / len(minutos)) if minutos else None,
    }


def grafica_barras(items, ancho=600, alto=220):
    """Coordenadas de un gráfico de barras SVG (se dibuja en la plantilla, sin JavaScript)."""
    maximo = max([1] + [i['valor'] for i in items])
    paso = ancho / max(1, len(items))
    barras = []
    for n, item in enumerate(items):
        altura = item['valor'] / maximo * 140
        barras.append({
            **item,
            'x': round(n * paso + paso * 0.2, 1), 'ancho': round(paso * 0.6, 1),
            'y': round(175 - altura, 1), 'alto': round(altura, 1),
            'cx': round(n * paso + paso * 0.5, 1), 'ty': round(165 - altura, 1),
        })
    return {'barras': barras, 'ancho': ancho, 'alto': alto, 'lineas': [25, 75, 125, 175]}


def _celda(valor):
    """Evita la inyección de fórmulas al abrir el CSV en Excel."""
    texto = '' if valor is None else str(valor)
    if texto[:1] in ('=', '+', '-', '@', '\t', '\r'):
        texto = "'" + texto
    return texto


def exportar_csv(alertas):
    salida = io.StringIO()
    salida.write('﻿')
    escritor = csv.writer(salida, delimiter=';', quoting=csv.QUOTE_ALL, lineterminator='\r\n')
    escritor.writerow([
        'ID', 'Cámara', 'Plaza', 'Placa detectada', 'Fecha', 'Hora', 'Resultado', 'Estado', 'Confianza OCR (%)',
        'Descripción de resolución', 'Autor de resolución', 'Fecha de resolución', 'Hora de resolución',
        'Automática', 'Tiempo de resolución (min)', 'Historial',
    ])
    for a in alertas.prefetch_related('eventos'):
        creada = timezone.localtime(a.creada_en)
        resuelta = timezone.localtime(a.resuelta_en) if a.resuelta_en else None
        historial = ' | '.join(
            f'{timezone.localtime(e.creado_en):%d/%m/%Y %H:%M} {e.get_tipo_display()} ({e.autor}): {e.texto}'
            for e in a.eventos.all()
        )
        escritor.writerow([_celda(v) for v in [
            a.pk, a.camara_nombre, a.plaza_nombre, a.placa_visible, f'{creada:%d/%m/%Y}', f'{creada:%H:%M}',
            a.get_resultado_display(), a.get_estado_display(), a.confianza, a.resolucion, a.resolucion_autor,
            f'{resuelta:%d/%m/%Y}' if resuelta else '', f'{resuelta:%H:%M}' if resuelta else '',
            ('Sí' if a.resolucion_automatica else 'No') if resuelta else '',
            round((a.resuelta_en - a.creada_en).total_seconds() / 60) if resuelta else '',
            historial,
        ]])
    return salida.getvalue()


# --------------------------------------------------------------------------- mantenimiento

def depurar_evidencias(dias=None):
    """Borra la evidencia de alertas resueltas más antiguas que la retención (RNF de privacidad)."""
    dias = dias or settings.ZENTRY['RETENCION_DIAS']
    limite = timezone.now() - timedelta(days=dias)
    almacen = puertos.evidencia()
    borradas = 0
    for alerta in Alerta.objects.filter(estado=Alerta.RESUELTA, creada_en__lt=limite).exclude(evidencia=''):
        for clave in (alerta.evidencia, alerta.evidencia_recorte):
            if clave:
                almacen.eliminar(clave)
        alerta.evidencia = alerta.evidencia_recorte = ''
        alerta.save(update_fields=['evidencia', 'evidencia_recorte'])
        borradas += 1
    return borradas
