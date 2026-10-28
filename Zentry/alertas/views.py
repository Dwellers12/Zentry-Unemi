"""
Vistas de M3 (alertas) y del Dashboard.

El Dashboard vive aquí porque junta información de verificacion (estado de las
plazas) y de alertas; colocarlo en verificacion haría que ese módulo dependiera
de alertas, rompiendo el sentido de las dependencias.
"""
from datetime import datetime

from django.contrib import messages
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from cuentas.roles import solo_admin, usuario_web
from registro.models import Camara, Plaza, Vehiculo
from registro.servicios import camaras_con_plazas
from verificacion.models import EstadoPlaza
from verificacion.servicios import estado_agente

from . import puertos, servicios
from .models import Alerta

SUGERENCIAS = [
    'Se notificó al residente y movió el vehículo',
    'El vehículo se retiró de la plaza',
    'Visitante autorizado por administración',
    'Error de lectura de la placa',
]
POR_PAGINA = 20


def _quiere_json(request):
    return (request.headers.get('Accept', '').startswith('application/json')
            or request.headers.get('X-Requested-With') == 'fetch')


def _responder(request, ok, mensaje, destino, status=200):
    """Respuesta de las acciones: JSON para el JavaScript, o mensaje + redirección sin JavaScript."""
    if _quiere_json(request):
        return JsonResponse({'ok': ok, 'mensaje': mensaje} if ok else {'ok': False, 'error': mensaje},
                            status=status if ok else 400)
    (messages.success if ok else messages.error)(request, mensaje)
    return redirect(destino)


@usuario_web
def inicio(request):
    return redirect('alertas:dashboard')


@usuario_web
def dashboard(request):
    camaras = list(camaras_con_plazas())
    recientes = list(Alerta.objects.select_related('camara')[:3])
    return render(request, 'alertas/dashboard.html', {
        'seccion': 'dashboard',
        'camaras': camaras,
        'resumen': servicios.resumen(),
        'recientes': recientes,
        'actividad': servicios.actividad(limite=10),
        'agente': estado_agente(),
        'zentry_inicial': servicios.estado_en_vivo(request.user),
    })


@require_GET
@usuario_web
def api_estado(request):
    desde = None
    if request.GET.get('desde'):
        try:
            desde = datetime.fromisoformat(request.GET['desde'])
            if timezone.is_naive(desde):
                desde = timezone.make_aware(desde)
        except ValueError:
            desde = None
    return JsonResponse(servicios.estado_en_vivo(request.user, desde))


@usuario_web
def lista(request):
    estado = request.GET.get('estado', '')
    filtros = {'estado': estado, 'q': request.GET.get('q', '')}
    alertas = servicios.ordenar(servicios.filtrar(filtros))
    pagina = Paginator(alertas, POR_PAGINA).get_page(request.GET.get('pagina'))
    conteos = servicios.resumen()
    pestanas = [
        ('', 'Todas', conteos['total']),
        (Alerta.PENDIENTE, 'Pendientes', conteos['pendientes']),
        (Alerta.REVISADA, 'Revisadas', conteos['revisadas']),
        (Alerta.RESUELTA, 'Resueltas', conteos['resueltas']),
    ]
    return render(request, 'alertas/lista.html', {
        'seccion': 'alertas',
        'pagina': pagina,
        'pestanas': pestanas,
        'estado': estado,
        'q': filtros['q'],
    })


@usuario_web
def detalle(request, pk):
    alerta = get_object_or_404(Alerta.objects.select_related('plaza', 'camara', 'lectura'), pk=pk)
    servicios.marcar_vistas(request.user, Alerta.objects.filter(pk=alerta.pk))
    detectado = Vehiculo.objects.select_related('residente').filter(placa=alerta.placa).first() if alerta.placa else None
    asignados = list(Vehiculo.objects.select_related('residente').filter(plaza=alerta.plaza)) if alerta.plaza else []
    estado = EstadoPlaza.objects.filter(plaza=alerta.plaza).first() if alerta.plaza else None
    sigue_ahi = bool(estado and estado.ocupada and estado.placa == alerta.placa and alerta.activa)
    return render(request, 'alertas/detalle.html', {
        'seccion': 'alertas',
        'titulo': 'Detalle alerta',
        'alerta': alerta,
        'eventos': list(alerta.eventos.all().order_by('-creado_en', '-pk')),
        'detectado': detectado,
        'asignados': asignados,
        'residente_asignado': asignados[0].residente if asignados else None,
        'sigue_ahi': sigue_ahi,
        'sugerencias': SUGERENCIAS,
        'estados': Alerta.ESTADOS,
        'confianza_baja': alerta.confianza < 70,
    })


@require_POST
@solo_admin
def cambiar_estado(request, pk):
    alerta = get_object_or_404(Alerta, pk=pk)
    destino = request.POST.get('estado', '')
    # Si alguien cambió la alerta mientras este usuario escribía, no se pisa su cambio.
    if request.POST.get('estado_original') and request.POST['estado_original'] != alerta.estado:
        return _responder(request, False, 'El estado cambió mientras editabas. Cierra el diálogo e inténtalo de nuevo.',
                          reverse('alertas:detalle', args=[pk]))
    reabre = alerta.estado == Alerta.RESUELTA or destino == Alerta.PENDIENTE
    try:
        servicios.cambiar_estado(alerta, destino, request.POST.get('nota', ''), request.user)
    except servicios.ErrorAlerta as error:
        return _responder(request, False, str(error), reverse('alertas:detalle', args=[pk]))
    mensaje = ('Alerta reabierta' if reabre else 'Alerta resuelta' if destino == Alerta.RESUELTA
               else 'Alerta revisada')
    return _responder(request, True, mensaje, reverse('alertas:detalle', args=[pk]))


@require_POST
@solo_admin
def corregir(request, pk):
    alerta = get_object_or_404(Alerta, pk=pk)
    try:
        alerta = servicios.corregir_placa(alerta, request.POST.get('placa', ''), request.user)
    except servicios.ErrorAlerta as error:
        return _responder(request, False, str(error), reverse('alertas:detalle', args=[pk]))
    mensaje = ('Alerta resuelta automáticamente: lectura corregida'
               if alerta.resultado == Alerta.CORREGIDA and alerta.resolucion_automatica
               else 'Lectura de placa corregida')
    return _responder(request, True, mensaje, reverse('alertas:detalle', args=[pk]))


@require_POST
@solo_admin
def editar_resolucion(request, pk):
    alerta = get_object_or_404(Alerta, pk=pk)
    try:
        servicios.editar_resolucion(alerta, request.POST.get('texto', ''), request.user)
    except servicios.ErrorAlerta as error:
        return _responder(request, False, str(error), reverse('alertas:detalle', args=[pk]))
    return _responder(request, True, 'Descripción actualizada', reverse('alertas:detalle', args=[pk]))


@require_GET
@usuario_web
def evidencia(request, pk, tipo):
    """Imagen de evidencia (RF-10). Nunca se sirve como archivo público: pasa por el login."""
    alerta = get_object_or_404(Alerta, pk=pk)
    clave = alerta.evidencia_recorte if tipo == 'recorte' else alerta.evidencia
    if tipo == 'recorte' and not clave:
        clave = alerta.evidencia
    datos = puertos.evidencia().leer(clave) if clave else None
    if not datos:
        raise Http404('La evidencia ya no está disponible')
    respuesta = HttpResponse(datos, content_type='image/jpeg')
    respuesta['Cache-Control'] = 'private, max-age=86400'
    return respuesta


@require_POST
@usuario_web
def marcar_vistas(request):
    servicios.marcar_vistas(request.user)
    return _responder(request, True, 'Notificaciones marcadas como vistas', reverse('alertas:lista'))


def _filtros_reporte(request):
    return {clave: request.GET.get(clave, '') for clave in ('desde', 'hasta', 'camara', 'plaza', 'estado', 'resultado')}


@solo_admin
def reportes(request):
    filtros = _filtros_reporte(request)
    error = ''
    try:
        alertas = list(servicios.filtrar(filtros).order_by('-creada_en'))
    except servicios.ErrorAlerta as e:
        error, alertas = str(e), []
    datos = servicios.reporte(alertas)
    plazas = Plaza.objects.select_related('camara').order_by('orden', 'nombre')
    if filtros['camara']:
        plazas = plazas.filter(camara__codigo=filtros['camara'])
    return render(request, 'alertas/reportes.html', {
        'seccion': 'reportes',
        'titulo': 'Reportes',
        'filtros': filtros,
        'error': error,
        'alertas': alertas,
        'datos': datos,
        'grafica_dias': servicios.grafica_barras(datos['dias']),
        'grafica_plazas': servicios.grafica_barras(datos['plazas']) if datos['plazas'] else None,
        'camaras': Camara.objects.all(),
        'plazas': plazas,
        'estados': Alerta.ESTADOS,
        'resultados': Alerta.RESULTADOS,
        'hay_filtros': any(filtros.values()),
    })


@solo_admin
def reportes_csv(request):
    try:
        alertas = servicios.filtrar(_filtros_reporte(request)).order_by('-creada_en')
    except servicios.ErrorAlerta as error:
        messages.error(request, str(error))
        return redirect('alertas:reportes')
    respuesta = HttpResponse(servicios.exportar_csv(alertas), content_type='text/csv; charset=utf-8')
    nombre = f'Zentry-alertas-{timezone.localdate():%Y-%m-%d}.csv'
    respuesta['Content-Disposition'] = f'attachment; filename="{nombre}"'
    return respuesta
